"""Streamlit components; calculations remain in ResistanceEngine."""

from datetime import date, timedelta
import sqlite3

import pandas as pd
import streamlit as st

from src.analysis_service import build_report
from src.anomaly_interface import render_assessment, render_surveillance
from src.signals import surveillance_signals
from src.charts import comparison_chart, trend_chart
from src.chart_states import render_chart
from src.database import check_integrity, import_history
from src.data_loader import import_csv_bundle, import_directory
from src.reporting import STATUS_LABELS, export_csv, period_label, rate_label, result_table, summary_items
from src.resistance import ResistanceEngine, ResistanceFilters

from src.design import expert
from src.presentation import expert_action,limitations

ALL_VALUES = "__all__"


def apply_style():
    from src.design import apply_design
    apply_design()


def _save_filter(name, widget):
    value = st.session_state[widget]
    st.session_state[name] = None if isinstance(value, str) and value == ALL_VALUES else value
    from src.ux_state import ALIASES
    if name in ALIASES:
        st.session_state[ALIASES[name]] = st.session_state[name]
    if name == "analysis_date":
        st.session_state["follow_latest_date"] = False
    from src.workspace_interface import autosave_workspace
    autosave_workspace()


def _select(label, values, default, name, formatter=str):
    if name not in st.session_state or st.session_state[name] not in values:
        st.session_state[name] = default
    widget = "_" + name
    options = [ALL_VALUES if value is None else value for value in values]
    st.session_state[widget] = ALL_VALUES if st.session_state[name] is None else st.session_state[name]
    selected = st.sidebar.selectbox(label, options,
        format_func=lambda value: formatter(None if value == ALL_VALUES else value), key=widget,
        on_change=_save_filter, args=(name, widget))
    return None if selected == ALL_VALUES else selected


def _latest_date(latest, *, persist=False):
    st.session_state["analysis_date"] = latest
    st.session_state["follow_latest_date"] = True
    if persist:
        from src.workspace_interface import autosave_workspace
        autosave_workspace()


def render_filters(catalog):
    st.sidebar.divider()
    st.sidebar.markdown("#### Laboratory filters")
    ward = _select("Ward", [None, *catalog.wards], None, "ward_code",
                   lambda code: catalog.wards[code] if code else "All wards")
    organism = _select("Organism", [None, *catalog.organisms], catalog.default_organism, "organism",
                       lambda value: value or "All organisms")
    antibiotic = _select("Antibiotic", list(catalog.antibiotics), catalog.default_antibiotic,
                         "antibiotic_code", lambda code: catalog.antibiotics[code])
    specimen = _select("Specimen Type", [None, *catalog.specimens], catalog.default_specimen, "specimen",
                       lambda value: value.title() if value else "All specimen types")
    render_analysis_date(catalog)
    return ResistanceFilters(antibiotic, ward, organism, specimen), st.session_state["analysis_date"]


def render_analysis_date(catalog, *, target=None):
    if st.session_state.get('_shared_date_rendered') and target is None:
        return
    if catalog.latest_sample is None:
        return
    current = st.session_state.get("analysis_date")
    if (st.session_state.get("follow_latest_date", True) or not isinstance(current, date)
            or not catalog.first_sample <= current <= catalog.latest_sample):
        _latest_date(catalog.latest_sample)
    st.session_state["_analysis_date"] = st.session_state["analysis_date"]
    ui = target or st.sidebar
    ui.date_input("Analysis Date", min_value=catalog.first_sample, max_value=catalog.latest_sample,
                          format="YYYY-MM-DD", key="_analysis_date", on_change=_save_filter,
                          args=("analysis_date", "_analysis_date"))
    if target is None:
        ui.button("Use latest sample", on_click=_latest_date, args=(catalog.latest_sample,), kwargs={"persist": True})
        ui.caption(f"Latest laboratory sample: {catalog.latest_sample.isoformat()}")
        ui.caption("Filters apply to this session only.")


def render_context(catalog, filters):
    st.caption(" / ".join([catalog.wards.get(filters.ward_code, "All wards"),
                            filters.organism or "All organisms", catalog.antibiotics[filters.antibiotic_code],
                            filters.specimen.title() if filters.specimen else "All specimens"]))


def render_freshness(catalog):
    left, right = st.columns(2)
    left.metric("Dataset Last Updated", catalog.updated_at.split(" ")[0])
    left.caption(f"{catalog.updated_at} UTC · Source-data revision timestamp")
    right.metric("Current Data Version", str(catalog.version))
    right.caption(f"Latest Sample Date: {catalog.latest_sample or 'No samples'}")
    st.caption(f"Last Successful Import: {catalog.last_successful_import or 'No completed imports'} UTC")


def _counts_caption(result):
    st.caption(f"S: {result.n_susceptible:,} · R: {result.n_resistant:,} · Tested: {result.n_tested:,}")
    st.caption(period_label(result))
    if result.status != "ok":
        st.caption(f"{STATUS_LABELS[result.status]} · Minimum tested first isolates: {result.minimum_sample_size}")


def render_table(items):
    st.dataframe(result_table(items), hide_index=True, width="stretch", column_config={
        "Resistance (%)": st.column_config.NumberColumn(format="%.2f"),
    })


def _series_caption(series):
    insufficient = sum(point.status == "insufficient_data" for point in series.points)
    empty = sum(point.status == "no_data" for point in series.points)
    st.caption(f"{series.start_date:%d %b %Y} – {series.end_date:%d %b %Y} · "
               f"Minimum tested first isolates per point: {series.minimum_sample_size}. "
               f"Insufficient Data: {insufficient} periods; No Data: {empty} periods.")


def render_signals(scan, catalog, threshold, settings=None):
    st.subheader("Surveillance Signals")
    if not expert():
        from types import SimpleNamespace
        from src.overview_interface import finding_card
        chosen=st.session_state.get('signal_threshold',float(threshold))
        signals=surveillance_signals(scan,chosen)
        a,b=st.columns(2)
        a.metric('Preliminary Signals',len(signals));b.metric('Statistically Supported Alerts',len(scan.alerts))
        st.caption('Preliminary findings are observed changes for review. Supported alerts meet the original full statistical criteria. Counts can overlap.')
        for i,signal in enumerate(signals[:10]):
            finding_card(SimpleNamespace(comparison=signal.comparison,kind='BY-alert' if signal.statistically_supported else 'preliminary'),settings,key=f'basic_finding_{i}')
        if len(signals)>10:
            st.caption('The first 10 findings are shown. Reports contains the complete investigation queue.')
        if not scan.alerts:
            st.info('No change met the full statistical alert criteria. This does not mean resistance is absent. Some comparisons lack enough observations or include repeated patients.')
        return
    widget_key = "_signal_threshold"
    st.session_state.setdefault("signal_threshold", float(threshold))
    st.session_state[widget_key] = st.session_state["signal_threshold"]
    chosen = st.number_input("Preliminary signal threshold (pp)", min_value=0.0, max_value=100.0,
        step=1.0, key=widget_key, on_change=_save_filter, args=("signal_threshold", widget_key))
    signals = surveillance_signals(scan, chosen)
    left, right = st.columns(2)
    left.metric("Preliminary Signals", len(signals))
    right.metric("Statistically Supported Alerts", len(scan.alerts))
    st.caption(f"Preliminary Signal: tested >= {max(30, scan.results[0].current.minimum_sample_size) if scan.results else 30} "
               f"in each fixed period and absolute change > {chosen:g} pp. Descriptive only; this does not establish statistical significance. "
               "Statistically Supported Alert: exploratory Fisher criteria and BY correction both met across the full scan.")
    if signals:
        st.warning("Preliminary Signal detected. Review S/R counts and patient independence before interpretation.")
        rows = []
        for signal in signals:
            r = signal.comparison
            rows.append({"Ward": r.filters.ward_code, "Organism": r.filters.organism,
                "Antibiotic": r.filters.antibiotic_code, "Specimen": r.filters.specimen,
                "Baseline S/R/N": f"{r.baseline.n_susceptible}/{r.baseline.n_resistant}/{r.baseline.n_tested}",
                "Current S/R/N": f"{r.current.n_susceptible}/{r.current.n_resistant}/{r.current.n_tested}",
                "Change (pp)": f"{r.difference_pp:+.2f}", "Shared patients": r.shared_patients,
                "Statistical status": "Statistically Supported Alert (exploratory)" if signal.statistically_supported else "Preliminary Signal only",
                "Limitations": " ".join(r.limitations) or "No known patient overlap; other dependencies or confounding remain possible."})
        st.table(pd.DataFrame(rows[:10]))
        st.caption(f"Showing {min(len(rows), 10)} of {len(rows)} signals. Current: last 28 days; baseline: preceding 365 days, "
                   "ending before the current window. The annual Dashboard rate uses a different, overlapping window.")
        with st.expander("All preliminary signals and limitations"):
            st.table(pd.DataFrame(rows))
        if settings is not None:
            from src.investigations import investigation_button
            investigation_button([signal.comparison for signal in signals], "preliminary", settings)
    else:
        st.info("No preliminary signals meet the volume and change thresholds. This does not prove absence of change.")


def render_dashboard(settings, catalog, filters, as_of_date):
    st.subheader("Resistance overview")
    st.write("Explore recent results alongside the annual surveillance picture.")
    render_context(catalog, filters)
    with st.spinner("Calculating surveillance statistics..."):
        report = build_report(ResistanceEngine(settings), filters, as_of_date, catalog.version,
                              include_anomalies=True, scan_alerts=True)
    recent, annual = report.recent, report.annual
    columns = st.columns(4)
    with columns[0]:
        st.metric("Resistance Rate — Last 28 Days", rate_label(recent))
        _counts_caption(recent)
    with columns[1]:
        st.metric("Resistance Rate — Last 365 Days", rate_label(annual))
        _counts_caption(annual)
    with columns[2]:
        st.metric("Number of Tested Isolates", f"{recent.n_tested:,}")
        st.caption("Last 28 Days · First isolates tested for the selected antibiotic")
    with columns[3]:
        st.metric("Number of Resistant Isolates", f"{recent.n_resistant:,}")
        st.caption(f"Last 28 Days · Susceptible (S): {recent.n_susceptible:,}")
    st.caption("Counts refer to tested first isolates, not all cultures or all antibiotic tests.")
    render_signals(report.surveillance_scan, catalog, settings.signal_minimum_change_pp, settings)
    if expert():
        render_assessment(report.assessment)
        if report.assessment.shared_patients:
            from src.investigations import open_patient_investigation
            st.caption("Additional Exploratory Evidence is available for the selected cohort with shared tested patients.")
            st.button("Review selected patient overlap",on_click=open_patient_investigation,args=(report.assessment,None,settings))
        render_surveillance(report.surveillance_scan, catalog, settings)
    else:
        limitations(report.assessment.limitations)
    st.subheader("Resistance Trend")
    st.caption("Weekly reporting periods · Hover over a measured point for S, R, sample size and dates.")
    render_chart(report.weekly.points,lambda:trend_chart(report.weekly),key="weekly_trend",frequency="weekly",aggregate=recent,
        monthly=report.monthly.points,monthly_factory=lambda:trend_chart(report.monthly))
    historical, comparison = st.columns([3, 2])
    with historical:
        st.subheader("Historical Resistance")
        st.caption("Monthly reporting periods")
        render_chart(report.monthly.points,lambda:trend_chart(report.monthly),key="monthly_trend",frequency="monthly",aggregate=recent)
    with comparison:
        st.subheader("Recent vs Annual")
        render_chart((recent,annual),lambda:comparison_chart(recent,annual),key="recent_annual",frequency="comparison")
        st.caption("28-day and 365-day windows overlap. This comparison is descriptive; "
                   "it is not a statistical test of change.")
    if expert():
        with st.expander("Period counts and reporting rules"):
            render_table(summary_items(report))
            st.write("Each period independently selects the first isolate per patient, organism and specimen. "
                     "Weekly or monthly denominators must not be added to calculate the whole-period rate. "
                     "Gaps indicate No Data or Insufficient Data; they do not indicate 0% resistance.")
    render_freshness(catalog)


def render_detailed(settings, catalog, filters, as_of_date):
    st.subheader("Detailed analysis")
    st.write("Inspect an inclusive reporting period and download aggregate results.")
    render_context(catalog, filters)
    default_start = max(catalog.first_sample, as_of_date - timedelta(days=364))
    if "custom_period" not in st.session_state:
        st.session_state["custom_period"] = (default_start, as_of_date)
    if any(not catalog.first_sample <= day <= catalog.latest_sample for day in st.session_state["custom_period"]):
        st.session_state["custom_period"] = (default_start, as_of_date)
        st.info("The saved reporting period is outside the available dates; a valid period has been selected.")
    st.session_state["_custom_period"] = st.session_state["custom_period"]
    selected = st.date_input("Reporting Period", min_value=catalog.first_sample, max_value=catalog.latest_sample,
                             format="YYYY-MM-DD", key="_custom_period", on_change=_save_filter,
                             args=("custom_period", "_custom_period"))
    st.caption("Both dates are included. This custom period controls the detailed tables and charts. "
               "Analysis Date in the sidebar anchors the 28-day and 365-day comparison windows.")
    if len(selected) != 2:
        st.info("Select both a start date and an end date to calculate this period.")
        return
    with st.spinner("Calculating detailed aggregates..."):
        report = build_report(ResistanceEngine(settings), filters, as_of_date, catalog.version,
                              custom_period=selected, include_anomalies=True)
    cols = st.columns(4)
    for column, label, value in zip(cols, ["Resistance Rate — Selected Period", "Tested Isolates", "Susceptible (S)", "Resistant (R)"],
                                    [rate_label(report.selected), report.selected.n_tested,
                                     report.selected.n_susceptible, report.selected.n_resistant]):
        column.metric(label, value)
    st.caption(f"{period_label(report.selected)} · {STATUS_LABELS[report.selected.status]} · "
               f"Minimum sample size: {report.selected.minimum_sample_size}")
    overview, weekly, monthly = st.tabs(["Period Summary", "Weekly Breakdown", "Monthly Breakdown"])
    with overview:
        if expert():
            render_table(summary_items(report))
        st.caption("The annual window is a rolling 365 days. It overlaps the recent 28-day window. "
                   "Differences are descriptive and do not establish a statistically confirmed change.")
    for tab, series, label in [(weekly, report.weekly, "Week"), (monthly, report.monthly, "Month")]:
        with tab:
            render_chart(series.points,lambda:trend_chart(series),key=f"detail_{label}",frequency="weekly" if label=="Week" else "monthly",
                monthly=report.monthly.points if label=="Week" else None,monthly_factory=lambda:trend_chart(report.monthly))
            if expert():
                render_table([(label, point) for point in series.points])
    st.caption("First-isolate selection restarts in every reporting period. Do not sum bucket denominators "
               "or average bucket percentages to derive the selected-period result.")
    if expert():
        st.download_button("Download aggregated results (CSV)", export_csv(report),
                           file_name=f"aei-sentinel-aggregates-{selected[0]}-{selected[1]}.csv", mime="text/csv",
                           key="aggregate_export", on_click="ignore", type="primary")
        st.caption("Contains calculated counts, filters, period dates, statuses and data version. No individual patient records.")
        render_assessment(report.assessment, detailed=True)
    else:
        limitations(report.assessment.limitations)
        st.caption('The selected period describes laboratory observations. It is separate from the fixed current and historical comparison; no new supported alert is assigned.')
    render_freshness(catalog)


def _completed(result):
    st.session_state["import_message"] = (f"Import completed: {result.rows_inserted:,} inserted, "
        f"{result.rows_unchanged:,} unchanged. Dataset version {result.data_version}.")
    st.rerun()


def _run_import(callback):
    try:
        _completed(callback())
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        st.error(str(exc))


def render_management(settings, catalog):
    from src.runtime_policy import public_demo
    if public_demo():
        st.info('Public Demo Mode: synthetic sources are read-only. Imports and persistent storage are disabled.')
        return
    st.title("Data management")
    st.write("Review the shared laboratory dataset and import validated source files.")
    st.warning("Local demonstration only: imports have no authentication and modify the shared laboratory database. "
               "Do not expose unrestricted import access on a publicly accessible server. Keep the app bound to 127.0.0.1.")
    st.success("Database connection status: Connected · SQLite initialized")
    columns = st.columns(3)
    for index, (table, count) in enumerate(catalog.counts.items()):
        columns[index % 3].metric(table.replace("_", " ").title(), f"{count:,}")
    st.caption(f"Number of imported source records: {sum(catalog.counts.values()):,}")
    render_freshness(catalog)
    if "import_message" in st.session_state:
        st.success(st.session_state.pop("import_message"))
    st.subheader("Import laboratory data")
    st.caption("Imports are atomic. Identical records are skipped; conflicting records are rejected.")
    st.info("Incremental ingestion: include new rows and any required references in the six-file bundle. "
            "Tables with no new rows can contain only their CSV header. Existing identical references are accepted. "
            "Existing laboratory results cannot be overwritten. A culture must include all its declared isolates.")
    with st.expander("Import original dataset", expanded=catalog.version == 0):
        st.caption(f"Configured directory: {settings.dataset_path}")
        if st.button("Import original six CSV files", type="primary"):
            with st.spinner("Validating and importing the original dataset..."):
                _run_import(lambda: import_directory(settings.dataset_path, settings, expect_original=True))
    with st.form("csv_import"):
        uploads = st.file_uploader("Select a complete six-file CSV bundle", type=["csv"], accept_multiple_files=True)
        st.caption("wards.csv, antibiotics.csv, patients.csv, cultures.csv, isolates.csv, susceptibility.csv")
        submitted = st.form_submit_button("Validate and import CSV bundle")
    if submitted:
        if len({item.name for item in uploads}) != len(uploads):
            st.error("Each CSV filename must occur exactly once.")
        else:
            with st.spinner("Validating and importing..."):
                _run_import(lambda: import_csv_bundle({item.name: item.getvalue() for item in uploads}, settings))
    if st.button("Check database integrity"):
        checks = check_integrity(settings)
        if checks == {"integrity": ["ok"], "foreign_key_errors": []}:
            st.success("Database integrity: OK. No foreign key violations.")
        else:
            st.error("Database integrity check failed. Review the database locally.")
    st.subheader("Import history")
    history = import_history(settings)
    if history:
        st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")
    else:
        st.info("No imports have been recorded yet.")
