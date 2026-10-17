"""Read-only, set-based ward × antibiotic matrix and exact surveillance context."""

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta

from src.advanced_analysis import CohortFilters, JOINS, _conditions, _json
from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine, comparison_windows
from src.cohort_queries import ranked_isolates_sql
from src.config import get_settings
from src.investigations import database_fingerprint
from src.resistance import ResistanceEngine, ResistanceFilters, ResistanceResult, _date
from src.signals import surveillance_signals


@dataclass(frozen=True)
class MapCell:
    ward_code: str
    antibiotic_code: str
    level: ResistanceResult
    current: ResistanceResult
    baseline: ResistanceResult
    difference_pp: float | None
    status: str
    indicator: str | None = None


@dataclass(frozen=True)
class SurveillanceMap:
    cohort: CohortFilters
    analysis_date: date | None
    view: str
    period: str
    antibiotic_class: str | None
    wards: tuple[tuple[str, str], ...]
    antibiotics: tuple[tuple[str, str], ...]
    cells: tuple[MapCell, ...]
    data_version: int
    source_identity: str
    latest_sample: date | None
    indicator_scope: str

    def to_dict(self):
        return _json(asdict(self))


def exact_scan_scope(cohort, view, period):
    """Ward metadata restricts rows, never the patients within a displayed ward."""
    return (cohort.organism is not None and cohort.specimen is not None
        and all(getattr(cohort, field) is None for field in ("sex", "age_min", "age_max", "home_area", "admission_timing"))
        and (view == "change" or period == "current"))


def matching_indicators(report, scan, threshold, *, source_identity):
    """Only the original full-family scan can supply a classification."""
    if source_identity != report.source_identity:
        return {}
    if scan.data_version != report.data_version or scan.analysis_date != report.analysis_date:
        raise DatasetChangedError("Surveillance context and matrix have different source revisions or dates. Refresh data.")
    if not exact_scan_scope(report.cohort, report.view, report.period):
        return {}
    signals = {signal.comparison.filters: "P" for signal in surveillance_signals(scan, threshold)}
    signals.update({item.filters: "A" for item in scan.alerts if item.adjusted_p_value is not None and item.adjusted_p_value < item.alpha})
    results = {item.filters: item for item in scan.results}
    indicators = {}
    for cell in report.cells:
        result = results.get(cell.current.filters)
        if result is None or result.filters not in signals:
            continue
        def same_counts(left, right):
            return (left.start_date, left.end_date, left.n_first_isolates, left.n_susceptible, left.n_resistant) == (
                right.start_date, right.end_date, right.n_first_isolates, right.n_susceptible, right.n_resistant)
        if same_counts(cell.current, result.current) and same_counts(cell.baseline, result.baseline):
            indicators[cell.ward_code, cell.antibiotic_code] = signals[result.filters]
    return indicators


class SurveillanceMapEngine:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.resistance = ResistanceEngine(replace(self.settings, minimum_sample_size=max(
            self.settings.minimum_sample_size, self.settings.anomaly_minimum_sample_size)))

    def analyze(self, cohort, *, as_of_date=None, view="level", period="annual", antibiotic_class=None,
                expected_version=None, include_indicators=True, signal_threshold=None):
        if not isinstance(cohort, CohortFilters) or cohort.ward_code is not None:
            raise ValueError("The matrix requires an unrestricted ward axis.")
        if view not in ("level", "change") or period not in ("current", "annual"):
            raise ValueError("Invalid map view or reporting period")
        if antibiotic_class is not None and (not isinstance(antibiotic_class, str) or not antibiotic_class.strip()):
            raise ValueError("Invalid antibiotic class")
        end = _date(as_of_date, "as_of_date") if as_of_date is not None else None
        with self.resistance._snapshot() as (con, version, latest):
            if expected_version is not None and expected_version != version:
                raise DatasetChangedError("The dataset changed before matrix calculation. Refresh data.")
            end = end or latest
            ward_conditions, ward_params = [], {}
            for name in ("ward_type", "institution"):
                if getattr(cohort, name) is not None:
                    ward_conditions.append(f"{name}=:{name}")
                    ward_params[name] = getattr(cohort, name)
            wards = tuple((r[0], r[1]) for r in con.execute("SELECT ward_code,ward_en FROM wards" +
                (" WHERE " + " AND ".join(ward_conditions) if ward_conditions else "") + " ORDER BY ward_en,ward_code", ward_params))
            antibiotic_params = {"antibiotic_class": antibiotic_class}
            antibiotics = tuple((r[0], r[1], r[2]) for r in con.execute(
                "SELECT antibiotic_code,antibiotic,reported_for FROM antibiotics WHERE (:antibiotic_class IS NULL OR antibiotic_class=:antibiotic_class) ORDER BY antibiotic_code", antibiotic_params))
            counts, bounds = {}, {}
            if end is not None:
                start, _, historical_start, historical_end = comparison_windows(end)
                bounds = {"current": (start, end), "baseline": (historical_start, historical_end), "annual": (end-timedelta(days=364), end)}
                clauses, params = _conditions(cohort, "antibiotic", "unused")
                params.update(antibiotic_params)
                values = []
                for key, (low, high) in bounds.items():
                    values.append(f"(:{key}_key,:{key}_start,:{key}_end)")
                    params.update({key+"_key": key, key+"_start": low.isoformat(), key+"_end": high.isoformat()})
                ranked = ranked_isolates_sql(clauses or ["1=1"], by_ward=True,
                    partition_expressions=("periods.period_key",), extra_columns="periods.period_key",
                    joins=JOINS + " JOIN periods ON c.sample_date BETWEEN periods.start_date AND periods.end_date")
                rows = con.execute(f"""WITH periods(period_key,start_date,end_date) AS (VALUES {','.join(values)}),
                    ranked AS ({ranked}), firsts AS (SELECT * FROM ranked WHERE isolate_rank=1),
                    eligible AS (SELECT period_key,ward_code,COUNT(*) AS n_first FROM firsts GROUP BY period_key,ward_code),
                    tested AS (SELECT f.period_key,f.ward_code,s.antibiotic_code,
                        SUM(s.result='S') AS susceptible,SUM(s.result='R') AS resistant
                        FROM firsts f JOIN susceptibility s ON s.isolate_id=f.isolate_id
                        JOIN antibiotics a ON a.antibiotic_code=s.antibiotic_code
                        WHERE (:antibiotic_class IS NULL OR a.antibiotic_class=:antibiotic_class)
                        GROUP BY f.period_key,f.ward_code,s.antibiotic_code)
                    SELECT e.period_key,e.ward_code,a.antibiotic_code,e.n_first,
                        COALESCE(t.susceptible,0) AS susceptible,COALESCE(t.resistant,0) AS resistant
                    FROM eligible e CROSS JOIN antibiotics a LEFT JOIN tested t
                        ON t.period_key=e.period_key AND t.ward_code=e.ward_code AND t.antibiotic_code=a.antibiotic_code
                    WHERE (:antibiotic_class IS NULL OR a.antibiotic_class=:antibiotic_class)""", params)
                counts = {(r["period_key"], r["ward_code"], r["antibiotic_code"]): (r["n_first"], r["susceptible"], r["resistant"]) for r in rows}
            cells = []
            for ward, _ in wards:
                for antibiotic, _, reported_for in antibiotics:
                    filters = ResistanceFilters(antibiotic, ward, cohort.organism, cohort.specimen)
                    def result(key):
                        return self.resistance._result(filters, *bounds.get(key, (None, None)), *counts.get((key, ward, antibiotic), (0, 0, 0)), version)
                    current, baseline, level = result("current"), result("baseline"), result(period)
                    difference = current.resistance_pct-baseline.resistance_pct if current.resistance_pct is not None and baseline.resistance_pct is not None else None
                    unavailable = reported_for == "urine" and cohort.specimen in ("blood", "wound")
                    status = ("unavailable" if unavailable else level.status if view == "level" else
                        "ok" if difference is not None else "no_data" if not current.n_tested or not baseline.n_tested else "insufficient_data")
                    cells.append(MapCell(ward, antibiotic, level, current, baseline, difference if not unavailable else None, status))
            scope = ("Matching current/baseline all-patient scan" if exact_scan_scope(cohort, view, period) else
                     "Indicators withheld: the selected subgroup, pooled species/specimens or annual level does not match the surveillance scan.")
            report = SurveillanceMap(cohort, end, view, period, antibiotic_class, wards,
                tuple((code, name) for code, name, _ in antibiotics), tuple(cells), version,
                database_fingerprint(self.settings), latest, scope)
            if end is not None and cells and include_indicators and exact_scan_scope(cohort, view, period):
                scan = AnomalyEngine(self.settings).scan(as_of_date=end)
                flags = matching_indicators(report, scan, self.settings.signal_minimum_change_pp if signal_threshold is None else signal_threshold,
                                            source_identity=database_fingerprint(self.settings))
                report = replace(report, cells=tuple(replace(cell, indicator=flags.get((cell.ward_code, cell.antibiotic_code))) for cell in cells))
        return report
