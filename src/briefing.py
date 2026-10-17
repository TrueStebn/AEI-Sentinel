"""Version-validated surveillance briefing; reuse the original complete scan family."""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from src.advanced_analysis import AdvancedAnalysisEngine, CohortFilters, _json
from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine, STATUSES
from src.config import get_settings
from src.database import connect
from src.investigations import database_fingerprint
from src.signals import surveillance_signals
from src.ui_data import load_catalog
from src.workspaces import MODES

ALGORITHM = "briefing-v1 / first-isolate-v1 / temporal-Fisher-BY-v1"
QUALITY_LABELS = {"no_data": "No Data", "insufficient_data": "Insufficient Data",
    "analysis_limited": "Patient-dependence limited", "no_significant_change": "Alert criteria not met",
    "potential_increase": "Supported increase", "potential_decrease": "Supported decrease"}
ORDERINGS = ("Review Priority", "Absolute change", "Current sample size", "Ward", "Latest observation")


@dataclass(frozen=True)
class BriefingFilters:
    ward_code: str | None = None
    organism: str | None = None
    specimen: str | None = None

    def __post_init__(self):
        CohortFilters(**asdict(self))

    def matches(self, result):
        return all(value is None or value == getattr(result.filters, name) for name, value in asdict(self).items())


def supported(result):
    return (result.status.startswith("potential_") and result.adjusted_p_value is not None
            and result.adjusted_p_value < result.alpha)


def quality_summary(results):
    """Missing counts are antibiotic-test opportunities, never unique patients."""
    counts = Counter(item.status for item in results)
    summary = {name: counts[name] for name in STATUSES}
    summary.update(combinations=len(results), eligible=sum(item.p_value is not None for item in results),
        dependence_any=sum(bool(item.limitations) for item in results))
    for name in ("current", "baseline"):
        periods = [getattr(item, name) for item in results]
        summary[name + "_first_opportunities"] = sum(item.n_first_isolates for item in periods)
        summary[name + "_tested_opportunities"] = sum(item.n_tested for item in periods)
        summary[name + "_missing_opportunities"] = sum(item.n_first_isolates-item.n_tested for item in periods)
    return summary


@dataclass(frozen=True)
class Finding:
    comparison: object
    kind: str
    latest_relevant_sample: date | None


def ordered_findings(findings, ordering="Review Priority", status="All"):
    if ordering not in ORDERINGS or status not in ("All", "Preliminary", "Statistically supported"):
        raise ValueError("Invalid investigation queue ordering or status")
    items = [item for item in findings if status == "All" or
        (item.kind == "BY-alert") == (status == "Statistically supported")]
    def stable(item):
        f = item.comparison.filters
        return f.ward_code, f.organism, f.antibiotic_code, f.specimen
    def change(item):
        return -abs(item.comparison.difference_pp or 0)
    key = {"Review Priority": lambda item: (item.kind != "BY-alert", change(item),
            -min(item.comparison.current.n_tested, item.comparison.baseline.n_tested), stable(item)),
        "Absolute change": lambda item: (change(item), stable(item)),
        "Current sample size": lambda item: (-item.comparison.current.n_tested, stable(item)),
        "Ward": stable,
        "Latest observation": lambda item: (-(item.latest_relevant_sample or date.min).toordinal(), stable(item))}[ordering]
    return tuple(sorted(items, key=key))


@dataclass(frozen=True)
class Briefing:
    source: str
    source_identity: str
    data_version: int
    analysis_date: date | None
    generated_at: str
    latest_sample: date | None
    last_successful_import: str | None
    filters: BriefingFilters
    thresholds: dict
    scan: object
    selected_results: tuple
    findings: tuple[Finding, ...]
    selected_findings: tuple[Finding, ...]
    selected_analysis: object | None

    @property
    def global_quality(self):
        return quality_summary(self.scan.results)

    @property
    def selected_quality(self):
        return quality_summary(self.selected_results)

    def to_dict(self, scope="hospital"):
        if scope not in ("hospital", "selected"):
            raise ValueError("Invalid report scope")
        results = self.scan.results if scope == "hospital" else self.selected_results
        findings = self.findings if scope == "hospital" else self.selected_findings
        return _json({"report_format": 1, "algorithm": ALGORITHM, "scope": scope,
            "source": self.source, "source_identity": self.source_identity, "data_version": self.data_version,
            "generated_at": self.generated_at, "analysis_date": self.analysis_date,
            "latest_sample": self.latest_sample, "last_successful_import": self.last_successful_import,
            "filters": asdict(self.filters) if scope == "selected" else asdict(BriefingFilters()),
            "thresholds": self.thresholds, "global_quality": self.global_quality,
            "scope_quality": quality_summary(results), "global_BY_family_size": self.scan.tested_comparisons,
            "results": [item.to_dict() for item in results],
            "findings": [{"kind": item.kind, "latest_relevant_sample": item.latest_relevant_sample,
                "comparison": item.comparison.to_dict()} for item in findings],
            "selected_analysis": self.selected_analysis.to_dict() if scope == "selected" and self.selected_analysis else None})

    def digest(self, scope):
        return hashlib.sha256(json.dumps(self.to_dict(scope), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_revision(report, settings):
    with connect(settings, read_only=True) as con:
        version = con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
    if database_fingerprint(settings) != report.source_identity or version != report.data_version:
        raise DatasetChangedError("Report source revision changed. Refresh and generate a new report; old exports are not current.")


def build_briefing(settings=None, *, filters=None, as_of_date=None, source=MODES[0], expected_version=None,
                   code=None, signal_threshold=None, generated_at=None):
    settings = settings or get_settings()
    filters = filters or BriefingFilters()
    if source not in MODES:
        raise ValueError("Invalid report data source")
    catalog = load_catalog(settings)
    if expected_version is not None and catalog.version != expected_version:
        raise DatasetChangedError("Briefing metadata changed. Refresh data.")
    threshold = settings.signal_minimum_change_pp if signal_threshold is None else signal_threshold
    scan = AnomalyEngine(settings).scan(as_of_date=as_of_date)
    signals = surveillance_signals(scan, threshold)
    if scan.data_version != catalog.version:
        raise DatasetChangedError("Briefing calculations changed revision. Refresh data.")
    # Latest relevant eligible positive-isolate date, not the latest AST date.
    with connect(settings, read_only=True) as con:
        con.execute("BEGIN")
        if con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0] != catalog.version:
            raise DatasetChangedError("Briefing observations changed revision. Refresh data.")
        latest = {(row[0], row[1], row[2]): date.fromisoformat(row[3]) for row in con.execute(
            "SELECT c.ward_code,i.organism,c.specimen,MAX(c.sample_date) FROM cultures c JOIN isolates i USING(culture_id) "
            "WHERE c.sample_date<=? GROUP BY c.ward_code,i.organism,c.specimen", (scan.analysis_date.isoformat() if scan.analysis_date else "",))}
    # Union matters when configured descriptive threshold is above the statistical threshold.
    candidates = {item.comparison.filters: item.comparison for item in signals}
    candidates.update({item.filters: item for item in scan.alerts if supported(item)})
    findings = ordered_findings(tuple(Finding(item, "BY-alert" if supported(item) else "preliminary",
        latest.get((item.filters.ward_code, item.filters.organism, item.filters.specimen))) for item in candidates.values()))
    selected = tuple(item for item in scan.results if filters.matches(item))
    investigation = AdvancedAnalysisEngine(settings).analyze(CohortFilters(**asdict(filters)), code=code,
        as_of_date=scan.analysis_date, expected_version=catalog.version, signal_threshold=threshold) if code else None
    report = Briefing(source, database_fingerprint(settings), catalog.version, scan.analysis_date,
        generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"), catalog.latest_sample,
        catalog.last_successful_import, filters,
        {"minimum_sample_size": max(settings.minimum_sample_size, settings.anomaly_minimum_sample_size),
         "signal_change_pp_strictly_greater": threshold, "statistical_change_pp_at_least": settings.anomaly_minimum_change_pp,
         "alpha": settings.anomaly_alpha, "confidence_level": settings.anomaly_confidence_level},
        scan, selected, findings, tuple(item for item in findings if filters.matches(item.comparison)), investigation)
    validate_revision(report, settings)
    return report


def freshness(report, *, today=None):
    today = today or datetime.now(timezone.utc).date()
    return {"sample_age_days": (today-report.latest_sample).days if report.latest_sample else None,
        "analysis_lag_days": (report.analysis_date-report.latest_sample).days if report.analysis_date and report.latest_sample else None,
        "latest_sample_after_analysis": bool(report.latest_sample and report.analysis_date and report.latest_sample > report.analysis_date)}


def finding_rows(findings):
    rows = []
    for item in findings:
        r, f = item.comparison, item.comparison.filters
        rows.append({"Ward": f.ward_code, "Organism": f.organism, "Antibiotic": f.antibiotic_code, "Specimen": f.specimen,
            "Current (%)": r.current.resistance_pct, "Baseline (%)": r.baseline.resistance_pct, "Change (pp)": r.difference_pp,
            "Current S/R/N": f"{r.current.n_susceptible}/{r.current.n_resistant}/{r.current.n_tested}",
            "Baseline S/R/N": f"{r.baseline.n_susceptible}/{r.baseline.n_resistant}/{r.baseline.n_tested}",
            "Status": "Statistically supported" if item.kind == "BY-alert" else "Preliminary",
            "Shared patients": r.shared_patients, "Limitations": " | ".join(r.limitations) or "None detected",
            "Last relevant observation": item.latest_relevant_sample})
    return rows
