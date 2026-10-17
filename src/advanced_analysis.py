"""One-snapshot subgroup reports using the established shared first-isolate SQL.

Filters precede ranking; measurements are evaluated after ranking. Grouped
descriptions independently rank within groups and are never summed or tested.
"""

from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta

from scipy.stats import binomtest

from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine, AnomalyScan, ProportionInterval, _Population, comparison_windows
from src.cohort_queries import ranked_isolates_sql
from src.config import get_settings
from src.phenotype_analysis import MARKER_SPECIES
from src.resistance import ResistanceEngine, ResistanceFilters, _date
from src.signals import surveillance_signals

AGE_GROUPS = {"0–17": (0, 17), "18–44": (18, 44), "45–64": (45, 64), "65+": (65, 130)}
AGE_SQL = "CASE WHEN c.age_years<18 THEN '0–17' WHEN c.age_years<45 THEN '18–44' WHEN c.age_years<65 THEN '45–64' ELSE '65+' END"
HOSPITAL_DAY = "CAST(julianday(c.sample_date)-julianday(c.admission_date) AS INTEGER)+1"
VALID_ADMISSION = "w.ward_type='in' AND c.admission_date IS NOT NULL AND date(julianday(c.admission_date))=c.admission_date AND c.admission_date<=c.sample_date"
GROUPS = {"sex": "p.sex", "age": AGE_SQL, "ward": "c.ward_code", "ward_type": "w.ward_type",
          "institution": "w.institution", "home_area": "p.home_area",
          "admission_timing": f"CASE WHEN ({HOSPITAL_DAY})<=2 THEN 'days_1_2' ELSE 'day_3_plus' END"}
JOINS = "JOIN patients p ON p.patient_id=c.patient_id JOIN wards w ON w.ward_code=c.ward_code"


@dataclass(frozen=True)
class CohortFilters:
    ward_code: str | None = None
    organism: str | None = None
    specimen: str | None = None
    sex: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    home_area: str | None = None
    ward_type: str | None = None
    institution: str | None = None
    admission_timing: str | None = None

    def __post_init__(self):
        for name in ("ward_code", "organism", "home_area"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or value != value.strip()):
                raise ValueError(f"Invalid {name}")
        for name, choices in (("specimen", ("urine", "blood", "wound")), ("sex", ("F", "M")),
                              ("ward_type", ("in", "out")), ("institution", ("hospital", "primary_care")),
                              ("admission_timing", ("all_applicable", "days_1_2", "day_3_plus"))):
            if getattr(self, name) not in (None, *choices):
                raise ValueError(f"Invalid {name}")
        for value in (self.age_min, self.age_max):
            if value is not None and (type(value) is not int or not 0 <= value <= 130):
                raise ValueError("Age bounds must be integers between 0 and 130")
        if (self.age_min or 0) > (self.age_max if self.age_max is not None else 130):
            raise ValueError("Minimum age cannot exceed maximum age")
        if self.admission_timing and self.ward_type == "out":
            raise ValueError("Admission timing applies only to inpatient records; clear the outpatient restriction.")


def _conditions(cohort, mode, code):
    clauses, params = [], {}
    for name, column in (("ward_code", "c.ward_code"), ("organism", "i.organism"), ("specimen", "c.specimen"),
                         ("sex", "p.sex"), ("home_area", "p.home_area"), ("ward_type", "w.ward_type"), ("institution", "w.institution")):
        value = getattr(cohort, name)
        if value is not None:
            clauses.append(f"{column}=:{name}")
            params[name] = value
    for name, operator in (("age_min", ">="), ("age_max", "<=")):
        if getattr(cohort, name) is not None:
            clauses.append(f"c.age_years{operator}:{name}")
            params[name] = getattr(cohort, name)
    if cohort.admission_timing:
        clauses.append(f"({VALID_ADMISSION})")
        if cohort.admission_timing != "all_applicable":
            clauses.append(f"({HOSPITAL_DAY}) {'<=2' if cohort.admission_timing == 'days_1_2' else '>=3'}")
    if mode == "phenotype":
        names = []
        for index, organism in enumerate(MARKER_SPECIES[code]):
            key = f"supported_{index}"
            names.append(":" + key)
            params[key] = organism
        clauses.append(f"i.organism IN ({','.join(names)})")
    return clauses, params


def _json(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


@dataclass(frozen=True)
class AdvancedPeriod:
    start_date: date | None
    end_date: date | None
    n_first_isolates: int
    n_negative: int
    n_positive: int
    n_observed: int
    percentage: float | None
    status: str
    minimum_sample_size: int
    distinct_observed_patients: int
    confidence_interval: ProportionInterval | None

    @property
    def missing_tests(self):
        return self.n_first_isolates - self.n_observed


@dataclass(frozen=True)
class AdvancedAssessment:
    status: str
    difference_pp: float | None
    shared_patients: int
    current_distinct_patients: int
    baseline_distinct_patients: int
    p_value: float | None
    test_method: str
    correction_method: str
    limitations: tuple[str, ...]
    explanation: str
    preliminary_signal: bool


@dataclass(frozen=True)
class AdvancedReport:
    mode: str
    code: str
    cohort: CohortFilters
    analysis_date: date | None
    data_version: int
    latest_relevant_sample: date | None
    current: AdvancedPeriod
    baseline: AdvancedPeriod
    annual: AdvancedPeriod
    weekly: tuple[AdvancedPeriod, ...]
    monthly: tuple[AdvancedPeriod, ...]
    groups: tuple[tuple[str, AdvancedPeriod], ...]
    group_dimension: str
    group_period: str
    patients_in_multiple_groups: int
    assessment: AdvancedAssessment
    supported: bool

    def to_dict(self):
        return _json(asdict(self))


@dataclass
class _Counts:
    first: int = 0
    population: _Population = field(default_factory=_Population)
    eligible_patients: set = field(default_factory=set)


def _buckets(start, end, frequency):
    current = start
    while current <= end:
        remaining = 6 - current.weekday() if frequency == "week" else monthrange(current.year, current.month)[1] - current.day
        stop = current + timedelta(days=min(remaining, (end - current).days))
        yield current, stop
        if stop == end:
            break
        current = stop + timedelta(days=1)


class AdvancedAnalysisEngine:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.minimum = max(self.settings.minimum_sample_size, self.settings.anomaly_minimum_sample_size)

    def _period(self, counts, start, end, supported=True):
        pop = counts.population
        observed = pop.susceptible + pop.resistant
        status = "unsupported" if not supported else "no_data" if not observed else "insufficient_data" if observed < self.minimum else "ok"
        pct = 100 * pop.resistant / observed if status == "ok" else None
        ci = None
        if status == "ok" and observed == len(pop.patients):
            interval = binomtest(pop.resistant, observed).proportion_ci(confidence_level=self.settings.anomaly_confidence_level, method="wilson")
            ci = ProportionInterval(interval.low * 100, interval.high * 100, self.settings.anomaly_confidence_level)
        return AdvancedPeriod(start, end, counts.first, pop.susceptible, pop.resistant, observed, pct, status, self.minimum, len(pop.patients), ci)

    def _read_periods(self, con, cohort, mode, code, periods, *, dimension=None):
        clauses, params = _conditions(cohort, mode, code)
        values = []
        for index, (key, start, end) in enumerate(periods):
            values.append(f"(:k{index},:s{index},:e{index})")
            params.update({f"k{index}": key, f"s{index}": start.isoformat(), f"e{index}": end.isoformat()})
        groups = (GROUPS[dimension],) if dimension else ()
        extra = "periods.period_key" + (f", {GROUPS[dimension]} AS group_value" if dimension else ", 'selected' AS group_value")
        if mode == "phenotype":
            extra += f", i.{code.lower()} AS marker_value"  # Marker is a fixed allowlisted column.
        query = ranked_isolates_sql(clauses or ["1=1"], partition_expressions=("periods.period_key", *groups), extra_columns=extra,
            joins=JOINS + " JOIN periods ON c.sample_date BETWEEN periods.start_date AND periods.end_date")
        params["antibiotic_code"] = code
        measurement = "s.result" if mode == "antibiotic" else "r.marker_value"
        after_rank = "LEFT JOIN susceptibility s ON s.isolate_id=r.isolate_id AND s.antibiotic_code=:antibiotic_code" if mode == "antibiotic" else ""
        rows = con.execute(f"WITH periods(period_key,start_date,end_date) AS (VALUES {','.join(values)}), ranked AS ({query}) "
                           f"SELECT r.period_key,r.group_value,r.patient_id,{measurement} AS result FROM ranked r {after_rank} WHERE r.isolate_rank=1", params)
        populations = defaultdict(_Counts)
        for row in rows:
            counts = populations[row["period_key"], row["group_value"]]
            counts.first += 1
            counts.eligible_patients.add(row["patient_id"])
            observed = row["result"] in ("S", "R") if mode == "antibiotic" else row["result"] in (0, 1)
            positive = row["result"] == "R" if mode == "antibiotic" else row["result"] == 1
            if observed:
                counts.population.patients.add(row["patient_id"])
                if positive:
                    counts.population.resistant += 1
                else:
                    counts.population.susceptible += 1
        return populations

    def _group_context(self, cohort, dimension):
        remove = {"sex": ("sex",), "age": ("age_min", "age_max"), "ward": ("ward_code",), "ward_type": ("ward_type",),
                  "institution": ("institution",), "home_area": ("home_area",), "admission_timing": ("admission_timing",)}
        values = {name: None for name in remove[dimension]}
        if dimension == "admission_timing":
            # Keep other restrictions. Outpatient contexts produce empty applicable groups.
            values.update(ward_type="in", admission_timing="all_applicable")
        return replace(cohort, **values)

    def analyze(self, cohort, *, mode="antibiotic", code="TMP", as_of_date=None, group_by="age", group_period="annual",
                signal_threshold=None, expected_version=None):
        if not isinstance(cohort, CohortFilters) or mode not in ("antibiotic", "phenotype") or group_by not in GROUPS or group_period not in ("current", "baseline", "annual"):
            raise ValueError("Invalid advanced analysis request")
        if mode == "phenotype" and code not in MARKER_SPECIES:
            raise ValueError("Unsupported phenotype marker")
        if mode == "antibiotic":
            ResistanceFilters(code)
        end = _date(as_of_date, "as_of_date") if as_of_date is not None else None
        supported = mode != "phenotype" or cohort.organism is None or cohort.organism in MARKER_SPECIES[code]
        with ResistanceEngine(self.settings)._snapshot() as (con, version, latest):
            if expected_version is not None and version != expected_version:
                raise DatasetChangedError("The dataset changed during calculation. Refresh data to load one consistent revision.")
            end = end or latest
            if cohort.ward_code:
                ward = con.execute("SELECT ward_type,institution FROM wards WHERE ward_code=?", (cohort.ward_code,)).fetchone()
                if ward and ((cohort.ward_type and cohort.ward_type != ward[0]) or (cohort.institution and cohort.institution != ward[1]) or (cohort.admission_timing and ward[0] != "in")):
                    raise ValueError("Selected ward is incompatible with the ward type, institution or admission timing.")
            if end is None:
                empty = self._period(_Counts(), None, None, supported)
                assessment = AdvancedAssessment("no_data", None, 0, 0, 0, None, "Not performed", "none", (), "No samples are available.", False)
                return AdvancedReport(mode, code, cohort, None, version, None, empty, empty, empty, (), (), (), group_by, group_period, 0, assessment, supported)
            current_start, _, baseline_start, baseline_end = comparison_windows(end)
            bounds = {"current": (current_start, end), "baseline": (baseline_start, baseline_end), "annual": (end-timedelta(days=364), end)}
            periods = [(key, *value) for key, value in bounds.items()]
            for frequency in ("week", "month"):
                periods.extend((f"{frequency}-{index}", start, stop) for index, (start, stop) in enumerate(_buckets(baseline_start, end, frequency)))
            populations = self._read_periods(con, cohort, mode, code, periods) if supported else defaultdict(_Counts)
            clause, params = _conditions(cohort, mode, code)
            clause.append("c.sample_date<=:as_of")
            params["as_of"] = end.isoformat()
            relevant = con.execute(f"SELECT MAX(c.sample_date) FROM cultures c JOIN isolates i USING(culture_id) {JOINS} WHERE {' AND '.join(clause)}", params).fetchone()[0] if supported else None
            grouped_cohort = self._group_context(cohort, group_by)
            admission_applicable = not (group_by == "admission_timing" and
                (cohort.ward_type == "out" or (cohort.ward_code and ward and ward[0] == "out")))
            group_populations = self._read_periods(con, grouped_cohort, mode, code, [("group", *bounds[group_period])], dimension=group_by) if supported and admission_applicable else {}
            choices = {"sex": ["F", "M"], "age": list(AGE_GROUPS), "ward_type": ["in", "out"], "institution": ["hospital", "primary_care"],
                       "admission_timing": ["days_1_2", "day_3_plus"]}
            if group_by in ("ward", "home_area"):
                table, column = ("wards", "ward_code") if group_by == "ward" else ("patients", "home_area")
                labels = [row[0] for row in con.execute(f"SELECT DISTINCT {column} FROM {table} ORDER BY {column}")]
            else:
                labels = choices[group_by]
            grouped = tuple((label, self._period(group_populations.get(("group", label), _Counts()), *bounds[group_period], supported)) for label in labels)
            membership = Counter(patient for counts in group_populations.values() for patient in counts.eligible_patients)
            overlap = sum(count > 1 for count in membership.values())
        converted = {key: self._period(populations[key, "selected"], start, stop, supported) for key, start, stop in periods}
        base = ResistanceFilters(code, cohort.ward_code, cohort.organism, cohort.specimen)
        def population(_, period):
            # Adapt binary counts to the established temporal-test input only.
            # Phenotype counts above come directly from flags; no AST is joined.
            counts = populations[period, "selected"]
            return counts.population, counts.first
        raw = AnomalyEngine(self.settings)._assess(base, end, (current_start, end, baseline_start, baseline_end), population, version)
        preliminary = bool(surveillance_signals(AnomalyScan(end, (raw,), 0, version), self.settings.signal_minimum_change_pp if signal_threshold is None else signal_threshold)) if supported else False
        current, baseline = converted["current"], converted["baseline"]
        metric = "R/S/tested" if mode == "antibiotic" else "marker-positive/marker-negative/eligible"
        explanation = (f"Current {metric}: {current.n_positive}/{current.n_negative}/{current.n_observed}. "
                       f"Baseline: {baseline.n_positive}/{baseline.n_negative}/{baseline.n_observed}. ")
        explanation += ("This organism-marker combination is unsupported, not a negative marker result." if not supported else
                        "Patient independence is not supported; Fisher testing is withheld." if raw.status == "analysis_limited" else
                        f"Each period requires at least {self.minimum} observed first isolates; no statistical conclusion is available." if raw.p_value is None else
                        "The selected temporal comparison is exploratory and unadjusted for repeated investigations; it does not establish a cause or clinical infection.")
        limitations = tuple(message.replace("tested patients", "observed patients") for message in raw.limitations) if mode == "phenotype" else raw.limitations
        assessment = AdvancedAssessment(raw.status if supported else "unsupported", raw.difference_pp, raw.shared_patients,
            raw.current_distinct_patients, raw.baseline_distinct_patients, raw.p_value, raw.test_method, raw.correction_method,
            limitations, explanation, preliminary)
        return AdvancedReport(mode, code, cohort, end, version, date.fromisoformat(relevant) if relevant else None, current, baseline, converted["annual"],
                              tuple(converted[key] for key, _, _ in periods if key.startswith("week-")),
                              tuple(converted[key] for key, _, _ in periods if key.startswith("month-")), grouped,
                              group_by, group_period, overlap, assessment, supported)
