"""Assemble engine outputs for a page and reject mixed source revisions."""

from dataclasses import dataclass
from datetime import date

from src.resistance import ResistanceEngine, ResistanceFilters, ResistanceResult, ResistanceTimeSeries
from src.anomalies import AnomalyEngine, AnomalyResult, AnomalyScan


class DatasetChangedError(RuntimeError):
    """The source changed while the page's separate engine calls were running."""


@dataclass(frozen=True)
class AnalysisReport:
    recent: ResistanceResult
    annual: ResistanceResult
    weekly: ResistanceTimeSeries
    monthly: ResistanceTimeSeries
    selected: ResistanceResult | None = None
    assessment: AnomalyResult | None = None
    surveillance_scan: AnomalyScan | None = None


def build_report(engine: ResistanceEngine, filters: ResistanceFilters, as_of_date: date,
                 expected_version: int, *, custom_period: tuple[date, date] | None = None,
                 include_anomalies: bool = False, scan_alerts: bool = False) -> AnalysisReport:
    recent = engine.last_28_days(filters, as_of_date=as_of_date)
    annual = engine.last_365_days(filters, as_of_date=as_of_date)
    selected = engine.calculate(filters, start_date=custom_period[0], end_date=custom_period[1]) if custom_period else None
    period = selected or annual
    weekly = engine.time_series(filters, start_date=period.start_date, end_date=period.end_date, frequency="week")
    monthly = engine.time_series(filters, start_date=period.start_date, end_date=period.end_date, frequency="month")
    outputs = [recent, annual, weekly, monthly] + ([selected] if selected else [])
    assessment = surveillance = None
    if include_anomalies:
        anomaly_engine = AnomalyEngine(engine.settings)
        assessment = anomaly_engine.analyze(filters, as_of_date=as_of_date)
        outputs.append(assessment)
        if scan_alerts:
            surveillance = anomaly_engine.scan(as_of_date=as_of_date)
            outputs.append(surveillance)
    if any(result.data_version != expected_version for result in outputs):
        raise DatasetChangedError("The dataset changed during calculation. Refresh data to load one consistent revision.")
    return AnalysisReport(recent, annual, weekly, monthly, selected, assessment, surveillance)
