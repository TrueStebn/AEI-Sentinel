import csv
import io
from dataclasses import replace
from datetime import date

import pytest

from src.analysis_service import DatasetChangedError, build_report
from src.charts import comparison_chart, trend_chart
from src.data_loader import import_csv_bundle
from src.reporting import export_csv, rate_label, result_table
from src.resistance import ResistanceEngine, ResistanceFilters, ResistanceResult, ResistanceTimeSeries
from src.ui_data import load_catalog


def result(status="ok", percent=23.333333):
    return ResistanceResult(date(2026, 9, 1), date(2026, 9, 27), ResistanceFilters("CIP"),
                            30, 23, 7, 30, percent, status, 30, 1)


def test_trend_keeps_gaps_and_true_zero_and_hover_counts():
    valid = result()
    missing = replace(valid, status="insufficient_data", resistance_pct=None,
                      n_first_isolates=1, n_susceptible=1, n_resistant=0, n_tested=1)
    zero = replace(valid, n_susceptible=30, n_resistant=0, resistance_pct=0.0)
    series = ResistanceTimeSeries(valid.start_date, valid.end_date, "week", valid.filters, 30, 1,
                                  (valid, missing, zero))
    figure = trend_chart(series)
    assert tuple(figure.data[0].y) == (valid.resistance_pct, None, 0.0)
    assert figure.data[0].connectgaps is False
    assert list(figure.data[0].customdata[0])[1:4] == [23, 7, 30]
    assert figure.layout.yaxis.range == (0, 100)


@pytest.mark.parametrize("status,label", [("insufficient_data", "Insufficient Data"), ("no_data", "No Data")])
def test_empty_or_small_charts_show_status_instead_of_zero(status, label):
    point = replace(result(status, None), n_tested=1 if status == "insufficient_data" else 0)
    series = ResistanceTimeSeries(point.start_date, point.end_date, "month", point.filters, 30, 1, (point,))
    figure = trend_chart(series)
    assert figure.data[0].y == (None,)
    assert label in figure.layout.annotations[0].text
    compare = comparison_chart(point, point)
    assert compare.data[0].y == (None, None)
    assert len(compare.layout.annotations) == 2
    assert label in compare.layout.annotations[0].text
    assert rate_label(point) == label


def test_table_preserves_counts_status_and_missing_percentage():
    point = replace(result("insufficient_data", None), n_tested=29, n_susceptible=22)
    table = result_table([("Selected Period", point)])
    assert table.iloc[0]["S"] == 22 and table.iloc[0]["R"] == 7
    assert table.iloc[0]["Resistance (%)"] is None
    assert table.iloc[0]["Status"] == "Insufficient Data"


def test_csv_is_aggregate_only_with_filters_dates_revision_and_missing_rates(settings, bundle):
    import_csv_bundle(bundle, settings)
    report = build_report(ResistanceEngine(settings), ResistanceFilters("AMP", "ED", "Escherichia coli", "urine"),
                          date(2026, 9, 27), 1, custom_period=(date(2026, 9, 1), date(2026, 9, 27)))
    content = export_csv(report)
    assert content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert set(row["section"] for row in rows) == {"summary", "weekly", "monthly"}
    first = rows[0]
    assert first["n_resistant"] == "1" and first["n_tested"] == "1"
    assert first["status"] == "insufficient_data" and first["resistance_pct"] == ""
    assert first["start_date"] == "2026-09-01" and first["data_version"] == "1"
    assert first["antibiotic_code"] == "AMP" and first["ward_code"] == "ED"
    assert not {"patient_id", "culture_id", "isolate_id"}.intersection(first)
    assert all(row["resistance_pct"] == "" for row in rows)


def test_report_rejects_revision_mismatch(settings, bundle):
    import_csv_bundle(bundle, settings)
    with pytest.raises(DatasetChangedError):
        build_report(ResistanceEngine(settings), ResistanceFilters("AMP"), date(2026, 9, 27), 0)


def test_report_custom_period_uses_engine_without_replacing_comparison_dates(settings, bundle):
    import_csv_bundle(bundle, settings)
    report = build_report(ResistanceEngine(settings), ResistanceFilters("AMP"), date(2026, 9, 27), 1,
                          custom_period=(date(2026, 9, 20), date(2026, 9, 26)))
    assert report.selected.n_tested == 0
    assert report.recent.n_tested == report.annual.n_tested == 1
    assert report.weekly.start_date == date(2026, 9, 20)
    assert report.monthly.end_date == date(2026, 9, 26)


def test_catalog_defaults_and_choices_come_from_source(settings, bundle):
    import_csv_bundle(bundle, settings)
    catalog = load_catalog(settings)
    assert catalog.available and catalog.default_antibiotic == "AMP"
    assert catalog.default_organism == "Escherichia coli" and catalog.default_specimen == "urine"
    assert catalog.wards == {"ED": "Emergency department (ED)"}
    assert catalog.first_sample == catalog.latest_sample == date(2026, 9, 27)
    assert catalog.version == 1


def test_empty_catalog_has_no_invented_date_or_filter_values(settings):
    catalog = load_catalog(settings)
    assert not catalog.available and not catalog.wards and not catalog.antibiotics
    assert catalog.latest_sample is None and catalog.default_organism is None
