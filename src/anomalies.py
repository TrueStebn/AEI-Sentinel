"""Exploratory, read-only resistance surveillance with conservative dependence guards."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from typing import Literal

from scipy.stats import binomtest, false_discovery_control, fisher_exact

from src.cohort_queries import ranked_isolates_sql
from src.config import Settings, get_settings
from src.resistance import DateInput, ResistanceEngine, ResistanceFilters, ResistanceResult, _date

AnomalyStatus = Literal["no_data", "insufficient_data", "no_significant_change",
                        "potential_increase", "potential_decrease", "analysis_limited"]
STATUSES = ("potential_increase", "potential_decrease", "no_significant_change",
            "analysis_limited", "insufficient_data", "no_data")
TEST_METHOD = "Fisher's exact test (two-sided)"
CORRECTION = "Benjamini–Yekutieli FDR"


@dataclass(frozen=True)
class ProportionInterval:
    lower_pct: float
    upper_pct: float
    confidence_level: float
    method: str = "Wilson"


@dataclass(frozen=True)
class AnomalyResult:
    filters: ResistanceFilters
    analysis_date: date | None
    current: ResistanceResult
    baseline: ResistanceResult
    difference_pp: float | None
    direction: str
    test_method: str
    p_value: float | None
    adjusted_p_value: float | None
    correction_method: str
    correction_family_size: int
    current_ci: ProportionInterval | None
    baseline_ci: ProportionInterval | None
    shared_patients: int
    current_distinct_patients: int
    baseline_distinct_patients: int
    minimum_change_pp: float
    alpha: float
    status: AnomalyStatus
    explanation: str
    limitations: tuple[str, ...]
    data_version: int

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["analysis_date"] = self.analysis_date.isoformat() if self.analysis_date else None
        payload["current"] = self.current.to_dict()
        payload["baseline"] = self.baseline.to_dict()
        return payload


@dataclass(frozen=True)
class AnomalyScan:
    analysis_date: date | None
    results: tuple[AnomalyResult, ...]
    tested_comparisons: int
    data_version: int
    correction_method: str = CORRECTION

    @property
    def alerts(self) -> tuple[AnomalyResult, ...]:
        return tuple(result for result in self.results if result.status.startswith("potential_"))

    @property
    def quality_counts(self) -> dict[str, int]:
        counts = Counter(result.status for result in self.results)
        return {status: counts[status] for status in STATUSES}

    def to_dict(self) -> dict:
        return {"analysis_date": self.analysis_date.isoformat() if self.analysis_date else None,
                "data_version": self.data_version, "correction_method": self.correction_method,
                "total_combinations": len(self.results), "tested_comparisons": self.tested_comparisons,
                "potential_deviations": len(self.alerts), "quality_counts": self.quality_counts,
                "results": [result.to_dict() for result in self.results]}


@dataclass
class _Population:
    susceptible: int = 0
    resistant: int = 0
    patients: set[str] = field(default_factory=set)


def comparison_windows(as_of_date: DateInput) -> tuple[date, date, date, date]:
    """Return current start/end, then preceding 365-day baseline start/end."""
    end = _date(as_of_date, "as_of_date")
    try:
        current_start = end - timedelta(days=27)
        baseline_end = current_start - timedelta(days=1)
        return current_start, end, baseline_end - timedelta(days=364), baseline_end
    except OverflowError as exc:
        raise ValueError("as_of_date is too early for the 393-day comparison span") from exc


def adjust_p_values(values: list[float]) -> list[float]:
    """BY rather than BH: antibiotic/cohort tests may have arbitrary dependence."""
    return [float(value) for value in false_discovery_control(values, method="by")] if values else []


def _explanation(result: AnomalyResult) -> str:
    c, b = result.current, result.baseline
    text = (f"Current: R={c.n_resistant}, S={c.n_susceptible}, tested={c.n_tested}. "
            f"Baseline: R={b.n_resistant}, S={b.n_susceptible}, tested={b.n_tested}. ")
    if result.difference_pp is not None:
        text += (f"Resistance changed from {b.resistance_pct:.2f}% to {c.resistance_pct:.2f}% "
                 f"({result.difference_pp:+.2f} percentage points). ")
    if result.status == "no_data":
        text += "At least one comparison period has no eligible S/R results; no change assessment is available."
    elif result.status == "insufficient_data":
        text += f"Each period requires at least {c.minimum_sample_size} tested first isolates; no statistical warning is issued."
    elif result.status == "analysis_limited":
        text += "Patient independence is not supported; Fisher testing and warning classification are withheld."
    else:
        p_text = (f"BY-adjusted p={result.adjusted_p_value:.4g} across {result.correction_family_size} tests"
                  if result.adjusted_p_value is not None else f"unadjusted p={result.p_value:.4g}")
        text += f"{TEST_METHOD}: {p_text}. "
        if result.status.startswith("potential_"):
            text += (f"Flagged for review: absolute change >= {result.minimum_change_pp:g} percentage points "
                     f"and p < {result.alpha:g}. This is an exploratory signal, not a clinical danger finding.")
        else:
            text += (f"The warning criteria (absolute change >= {result.minimum_change_pp:g} percentage points "
                     f"and p < {result.alpha:g}) were not both met; this does not prove absence of change.")
    return text


def _classify(result: AnomalyResult, p_value: float) -> AnomalyStatus:
    if abs(result.difference_pp) + 1e-12 >= result.minimum_change_pp and p_value < result.alpha:
        return "potential_increase" if result.difference_pp > 0 else "potential_decrease"
    return "no_significant_change"


class AnomalyEngine:
    """Analyze one filter set or scan observed exact cohorts in one read snapshot.

    Patient IDs are used transiently for independence checks and never returned.
    The scan uses one discovery query and one ranked-observation query, not one
    query per combination. All eligible p-values enter the correction family.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        threshold = max(self.settings.minimum_sample_size, self.settings.anomaly_minimum_sample_size)
        self.resistance = ResistanceEngine(replace(self.settings, minimum_sample_size=threshold))

    def _read(self, con, windows, filters=None):
        start, end, baseline_start, _ = windows
        parameters = {"start_date": baseline_start.isoformat(), "end_date": end.isoformat(),
                      "current_start": start.isoformat()}
        conditions = ["c.sample_date BETWEEN :start_date AND :end_date"]
        join = ""
        if filters is not None:
            parameters["antibiotic_code"] = filters.antibiotic_code
            join = "AND s.antibiotic_code = :antibiotic_code"
            for column, name in (("c.ward_code", "ward_code"), ("i.organism", "organism"), ("c.specimen", "specimen")):
                value = getattr(filters, name)
                if value is not None:
                    conditions.append(f"{column}=:{name}")
                    parameters[name] = value
        query = ranked_isolates_sql(conditions, by_ward=filters is None, split_period=True)
        rows = con.execute(f"""WITH ranked AS ({query})
            SELECT r.*,s.antibiotic_code,s.result FROM ranked r
            LEFT JOIN susceptibility s ON s.isolate_id=r.isolate_id {join}
            WHERE r.isolate_rank=1""", parameters)
        first = defaultdict(set)
        populations = defaultdict(_Population)
        for row in rows:
            period = row["reporting_period"]
            base_key = ((row["ward_code"], row["organism"], row["specimen"]) if filters is None else "selected")
            first[base_key, period].add(row["isolate_id"])
            if row["result"] not in ("S", "R"):
                continue
            cohort = filters or ResistanceFilters(row["antibiotic_code"], row["ward_code"], row["organism"], row["specimen"])
            pop = populations[cohort, period]
            pop.patients.add(row["patient_id"])
            if row["result"] == "S":
                pop.susceptible += 1
            else:
                pop.resistant += 1

        def population(cohort, period):
            base_key = (cohort.ward_code, cohort.organism, cohort.specimen) if filters is None else "selected"
            return populations.get((cohort, period), _Population()), len(first.get((base_key, period), ()))
        return population

    def _assess(self, filters, end, windows, population, version):
        current_pop, current_first = population(filters, "current")
        baseline_pop, baseline_first = population(filters, "baseline")
        start, current_end, baseline_start, baseline_end = windows if windows else (None,) * 4
        current = self.resistance._result(filters, start, current_end, current_first,
            current_pop.susceptible, current_pop.resistant, version)
        baseline = self.resistance._result(filters, baseline_start, baseline_end, baseline_first,
            baseline_pop.susceptible, baseline_pop.resistant, version)
        shared = len(current_pop.patients & baseline_pop.patients)
        limitations = []
        if shared:
            limitations.append(f"{shared} tested patients occur in both periods; the two samples are not independent.")
        repeated_current = current.n_tested - len(current_pop.patients)
        repeated_baseline = baseline.n_tested - len(baseline_pop.patients)
        if repeated_current or repeated_baseline:
            limitations.append(f"Pooled groups include repeated patients within a period "
                               f"(current extra observations: {repeated_current}; baseline: {repeated_baseline}).")
        difference = (current.resistance_pct - baseline.resistance_pct
                      if current.resistance_pct is not None and baseline.resistance_pct is not None else None)
        direction = ("unavailable" if difference is None else "increase" if difference > 0
                     else "decrease" if difference < 0 else "unchanged")
        def interval(result, patients):
            if result.status != "ok" or result.n_tested != len(patients):
                return None
            ci = binomtest(result.n_resistant, result.n_tested).proportion_ci(
                confidence_level=self.settings.anomaly_confidence_level, method="wilson")
            return ProportionInterval(float(ci.low * 100), float(ci.high * 100), self.settings.anomaly_confidence_level)
        p = None
        if current.n_tested == 0 or baseline.n_tested == 0:
            status = "no_data"
        elif current.status != "ok" or baseline.status != "ok":
            status = "insufficient_data"
        elif limitations:
            status = "analysis_limited"
        else:
            p = float(fisher_exact([[current.n_resistant, current.n_susceptible],
                                   [baseline.n_resistant, baseline.n_susceptible]], alternative="two-sided").pvalue)
            status = "no_significant_change"
        result = AnomalyResult(filters, end, current, baseline, difference, direction, TEST_METHOD,
            p, None, "none (selected exploratory comparison)", 0,
            interval(current, current_pop.patients), interval(baseline, baseline_pop.patients),
            shared, len(current_pop.patients), len(baseline_pop.patients),
            self.settings.anomaly_minimum_change_pp, self.settings.anomaly_alpha,
            status, "", tuple(limitations), version)
        if p is not None:
            result = replace(result, status=_classify(result, p))
        return replace(result, explanation=_explanation(result))

    def analyze(self, filters: ResistanceFilters, *, as_of_date: DateInput | None = None) -> AnomalyResult:
        explicit = _date(as_of_date, "as_of_date") if as_of_date is not None else None
        with self.resistance._snapshot() as (con, version, latest):
            end = explicit or latest
            windows = comparison_windows(end) if end else None
            population = self._read(con, windows, filters) if windows else lambda *_: (_Population(), 0)
            return self._assess(filters, end, windows, population, version)

    def scan(self, *, as_of_date: DateInput | None = None) -> AnomalyScan:
        explicit = _date(as_of_date, "as_of_date") if as_of_date is not None else None
        with self.resistance._snapshot() as (con, version, latest):
            end = explicit or latest
            if end is None:
                return AnomalyScan(None, (), 0, version)
            windows = comparison_windows(end)
            combinations = con.execute("""SELECT DISTINCT c.ward_code,i.organism,s.antibiotic_code,c.specimen
                FROM cultures c JOIN isolates i ON i.culture_id=c.culture_id
                JOIN susceptibility s ON s.isolate_id=i.isolate_id
                WHERE c.sample_date BETWEEN ? AND ?
                ORDER BY c.ward_code,i.organism,s.antibiotic_code,c.specimen""",
                (windows[2].isoformat(), end.isoformat())).fetchall()
            population = self._read(con, windows)
            results = [self._assess(ResistanceFilters(row[2], row[0], row[1], row[3]), end,
                                    windows, population, version) for row in combinations]
        tested = [index for index, result in enumerate(results) if result.p_value is not None]
        adjusted = adjust_p_values([results[index].p_value for index in tested])
        for index, value in zip(tested, adjusted):
            result = replace(results[index], adjusted_p_value=value, correction_method=CORRECTION,
                             correction_family_size=len(tested))
            result = replace(result, status=_classify(result, value))
            results[index] = replace(result, explanation=_explanation(result))
        for index, result in enumerate(results):
            if result.p_value is None:
                results[index] = replace(result, correction_method=CORRECTION, correction_family_size=len(tested))
        results.sort(key=lambda r: (0 if r.status.startswith("potential_") else 1 if r.p_value is not None else 2,
            r.adjusted_p_value if r.adjusted_p_value is not None else float("inf"),
            -abs(r.difference_pp or 0), r.filters.ward_code, r.filters.organism,
            r.filters.antibiotic_code, r.filters.specimen))
        return AnomalyScan(end, tuple(results), len(tested), version)
