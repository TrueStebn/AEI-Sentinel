"""Presentation and aggregate-only export of already calculated engine results."""

import csv
import io

import pandas as pd

from src.analysis_service import AnalysisReport
from src.resistance import ResistanceResult
from src.anomalies import AnomalyResult

STATUS_LABELS = {"ok": "Meets Sample Threshold", "insufficient_data": "Insufficient Data", "no_data": "No Data"}


def rate_label(result: ResistanceResult) -> str:
    return f"{result.resistance_pct:.2f}%" if result.status == "ok" else STATUS_LABELS[result.status]


def period_label(result: ResistanceResult) -> str:
    return f"{result.start_date:%d %b %Y} – {result.end_date:%d %b %Y}"


def result_table(items: list[tuple[str, ResistanceResult]]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Period": label, "Start Date": result.start_date.isoformat(), "End Date": result.end_date.isoformat(),
        "S": result.n_susceptible, "R": result.n_resistant, "Total": result.n_tested,
        "First Isolates": result.n_first_isolates, "Resistance (%)": result.resistance_pct,
        "Status": STATUS_LABELS[result.status], "Data Version": result.data_version,
    } for label, result in items])


def summary_items(report: AnalysisReport) -> list[tuple[str, ResistanceResult]]:
    return ([("Selected Period", report.selected)] if report.selected else []) + [
        ("Last 28 Days", report.recent), ("Last 365 Days", report.annual)]


def export_csv(report: AnalysisReport) -> bytes:
    rows = []
    groups = [("summary", summary_items(report)),
              ("weekly", [("Week", p) for p in report.weekly.points]),
              ("monthly", [("Month", p) for p in report.monthly.points])]
    for section, items in groups:
        for label, result in items:
            record = result.to_dict()
            filters = record.pop("filters")
            rows.append({"section": section, "period": label, **record, **filters})
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def anomaly_record(result: AnomalyResult) -> dict:
    record = {**result.to_dict()["filters"], "analysis_date": result.to_dict()["analysis_date"]}
    for name in ("current", "baseline"):
        period = getattr(result, name)
        for key, value in period.to_dict().items():
            if key != "filters":
                record[f"{name}_{key}"] = value
        ci = getattr(result, f"{name}_ci")
        record[f"{name}_ci_lower_pct"] = ci.lower_pct if ci else None
        record[f"{name}_ci_upper_pct"] = ci.upper_pct if ci else None
        record[f"{name}_ci_confidence_level"] = ci.confidence_level if ci else None
    for key in ("difference_pp", "direction", "test_method", "p_value", "adjusted_p_value",
                "correction_method", "correction_family_size", "shared_patients",
                "current_distinct_patients", "baseline_distinct_patients", "minimum_change_pp",
                "alpha", "status", "explanation", "data_version"):
        record[key] = getattr(result, key)
    record["limitations"] = " | ".join(result.limitations)
    return record


def export_anomalies_csv(results: list[AnomalyResult] | tuple[AnomalyResult, ...]) -> bytes:
    if not results:
        raise ValueError("No anomaly comparison results are available to export")
    rows = [anomaly_record(result) for result in results]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")
