"""Compact hospital matrix, native cell selections and aggregate investigation."""

import hashlib
import json
from dataclasses import asdict
from functools import partial

import pandas as pd
import streamlit as st

from src.advanced_analysis import AGE_GROUPS, CohortFilters
from src.analysis_service import DatasetChangedError
from src.database import connect
from src.explorer_interface import _choice, _label, _number
from src.interface import _select, render_analysis_date
from src.investigations import open_map_investigation
from src.map_charts import STATUS, cell_value, matrix_chart, visible_antibiotics
from src.surveillance_map import SurveillanceMapEngine
from src.design import expert
from src.presentation import expert_action,saved_choice,cohort_caption

LABELS = {"level": "Resistance Level", "change": "Resistance Change", "current": "Last 28 days", "annual": "Rolling 365 days",
          "name": "Ward name", "volume": "Eligible isolate volume", "full": "0–100% (full scale)",
          "focused": "0–50% (focused scale)", "show": "Show all columns", "hide_empty": "Hide columns without reportable values"}
choose = partial(_choice, prefix="map_", formatter=lambda value: LABELS.get(value, _label(value)))


def map_metadata(settings, version):
    with connect(settings, read_only=True) as con:
        con.execute("BEGIN")
        if con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0] != version:
            raise DatasetChangedError("Map filter metadata changed. Refresh data.")
        return {key: [row[0] for row in con.execute(query)] for key, query in {
            "classes": "SELECT DISTINCT antibiotic_class FROM antibiotics ORDER BY antibiotic_class",
            "types": "SELECT DISTINCT ward_type FROM wards ORDER BY ward_type",
            "institutions": "SELECT DISTINCT institution FROM wards ORDER BY institution"}.items()}


def accept_selection(report, event, key):
    """Validate selection against current axes; do not replay an old event on rerun."""
    points = event.get("selection", {}).get("points", []) if isinstance(event, dict) else []
    chosen = tuple((point.get("y"), point.get("x")) for point in points)
    signature = (key, chosen)
    if st.session_state.get("_map_last_event") != signature:
        st.session_state["_map_last_event"] = signature
        valid = {(cell.ward_code, cell.antibiotic_code) for cell in report.cells}
        if chosen and chosen[-1] in valid:
            st.session_state["map_selected_ward"], st.session_state["map_selected_antibiotic"] = chosen[-1]


def matrix_rows(report):
    rows = []
    for cell in report.cells:
        row = {"Ward": cell.ward_code, "Antibiotic": cell.antibiotic_code, "Status": STATUS[cell.status],
               "Resistance (%)": cell_value(report, cell) if report.view == "level" else None,
               "Change (pp)": cell.difference_pp if report.view == "change" else None,
               "Indicator": cell.indicator or "None", "Source version": report.data_version}
        for label, result in (("Selected", cell.level), ("Current", cell.current), ("Baseline", cell.baseline)):
            row.update({f"{label} start": result.start_date, f"{label} end": result.end_date,
                f"{label} S": result.n_susceptible, f"{label} R": result.n_resistant,
                f"{label} tested": result.n_tested, f"{label} eligible first": result.n_first_isolates,
                f"{label} missing tests": result.n_first_isolates-result.n_tested, f"{label} (%)": result.resistance_pct, f"{label} status": result.status})
        rows.append(row)
    return rows


def render_surveillance_map(settings, catalog):
    st.subheader("Surveillance Map")
    st.caption("Ward × Antibiotic · Observed laboratory resistance · Select a cell to investigate")
    st.sidebar.divider()
    st.sidebar.markdown("#### Map context")
    organism = _select("Organism", [None, *catalog.organisms], catalog.default_organism, "organism", lambda value: value or "All organisms")
    specimen = _select("Specimen Type", [None, *catalog.specimens], catalog.default_specimen, "specimen", lambda value: value.title() if value else "All specimen types")
    render_analysis_date(catalog)
    metadata = map_metadata(settings, catalog.version)
    a, b, c = st.columns(3)
    with a:
        view = choose("Map view", ["level", "change"], "level", "view")
    with b:
        period = choose("Level reporting period", ["annual", "current"], "annual", "period")
    with c:
        sort = choose("Sort wards by", ["name", "volume"], "name", "sort")
    sex=saved_choice('map_sex',[None,'F','M'],None)
    age=saved_choice('map_age_group',['all',*AGE_GROUPS,'custom'],'all')
    ward_type=saved_choice('map_ward_type',[None,*metadata['types']],None)
    institution=saved_choice('map_institution',[None,*metadata['institutions']],None)
    antibiotic_class=saved_choice('map_antibiotic_class',[None,*metadata['classes']],None)
    scale=saved_choice('map_scale',['full','focused'],'full')
    columns=saved_choice('map_columns',['show','hide_empty'],'show')
    bounds=AGE_GROUPS.get(age,(None,None))
    if age=='custom':
        bounds=st.session_state.get('map_age_min',0),st.session_state.get('map_age_max',130)
    if expert():
        with st.expander("Patient and map filters", expanded=False):
            a, b, c = st.columns(3)
            with a:
                sex = choose("Sex", [None, "F", "M"], None, "sex")
                ward_type = choose("Ward type", [None, *metadata["types"]], None, "ward_type")
            with b:
                age = choose("Age group", ["all", *AGE_GROUPS, "custom"], "all", "age_group")
                institution = choose("Institution", [None, *metadata["institutions"]], None, "institution")
            with c:
                antibiotic_class = choose("Antibiotic class", [None, *metadata["classes"]], None, "antibiotic_class")
                st.caption("Filters apply before first-isolate selection. Ward and class filters restrict the displayed axes.")
            bounds = AGE_GROUPS.get(age, (None, None))
            if age == "custom":
                left, right = st.columns(2)
                with left:
                    low = _number("Minimum age (inclusive)", "age_min", 0, prefix="map_")
                with right:
                    high = _number("Maximum age (inclusive)", "age_max", 130, prefix="map_")
                bounds = low, high
            left, right = st.columns(2)
            with left:
                scale = choose("Resistance color scale",["full","focused"],"full","scale")
            with right:
                columns = choose("Empty antibiotic columns",["show","hide_empty"],"show","columns")
    cohort = CohortFilters(organism=organism, specimen=specimen, sex=sex, age_min=bounds[0], age_max=bounds[1], ward_type=ward_type, institution=institution,
        home_area=st.session_state.get('explorer_home_area'),admission_timing=st.session_state.get('explorer_admission_timing'))
    cohort_caption(cohort)
    if cohort.home_area or cohort.admission_timing:
        st.caption(f'Shared patient context: home area {cohort.home_area or "All"}; admission timing {cohort.admission_timing or "All"}. Edit these in Investigation.')
    report = SurveillanceMapEngine(settings).analyze(cohort,
        as_of_date=st.session_state.get("analysis_date") if catalog.latest_sample else None,
        view=view, period=period, antibiotic_class=antibiotic_class, expected_version=catalog.version,
        signal_threshold=st.session_state.get("signal_threshold", settings.signal_minimum_change_pp))
    st.caption(f"{organism or 'All organisms'} / {specimen or 'All specimens'} · {_label(sex)} / {_label(age)} · "
               f"Analysis date: {report.analysis_date or 'No Data'} · Source: {st.session_state.get('data_source', 'Laboratory Dataset')} · Version: {report.data_version}")
    if not report.cells:
        st.info("No Data. The active source has no available ward × antibiotic matrix for these filters. Use Next Batch or revise the filters.")
        expert_action("map_empty")
        return report,None
    if view == "change":
        st.caption("Change = current 28-day resistance minus preceding non-overlapping 365-day resistance (pp). The level-period control does not change these fixed comparison windows.")
    else:
        selected = report.cells[0].level
        st.caption(f"Resistance level: {selected.start_date} – {selected.end_date}. A high level does not establish a recent increase.")
    signature = {"source": report.source_identity, "version": report.data_version, "date": str(report.analysis_date),
                 "cohort": asdict(cohort), "class": antibiotic_class, "view": view, "period": period, "sort": sort,
                 "scale": scale, "columns": columns}
    key = "_map_plot_" + hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    visible = visible_antibiotics(report,hide_empty=columns=="hide_empty")
    hidden = [code for code,_ in report.antibiotics if code not in dict(visible)]
    if hidden:
        st.caption(f"Hidden columns ({len(hidden)}): {', '.join(hidden)}. No reportable values in this view. Exports and investigation selectors retain all columns.")
    if scale=="focused" and view=="level":
        st.info("Focused color scale: 0–50%. Values above 50% share the darkest color; displayed percentages and counts are unchanged.")
    if view=="change":
        st.caption("Change color scale remains fixed at −100 to +100 pp; the resistance-level scale choice does not apply.")
    event = st.plotly_chart(matrix_chart(report, sort=sort,scale_max=50 if scale=="focused" else 100,hide_empty=columns=="hide_empty"), width="stretch", key=key, theme=None,
        on_select="rerun", selection_mode="points", config={"displayModeBar": False, "scrollZoom": False},
        alt="Selectable ward by antibiotic resistance matrix; numeric values, missing-data labels and matching surveillance indicators.")
    accept_selection(report, event, key)
    st.caption("Legend: numbers = % (Level) or pp (Change); IN = Insufficient Data; ND = No Data; NA = unavailable urine-only combination. "
               "P = matching descriptive Preliminary Signal; A = matching exploratory BY-adjusted alert. Click a square or use the selectors below.")
    st.caption(report.indicator_scope)
    st.caption("Higher resistance does not establish clinical danger; lower resistance does not establish suitability for a patient. "
               "Interpret values with organism, specimen, sampling and denominator context. Colors contain no clinical safe/unsafe thresholds.")
    left, right = st.columns(2)
    with left:
        ward = choose("Ward for investigation", [None, *dict(report.wards)], None, "selected_ward")
    with right:
        antibiotic = choose("Antibiotic for investigation", [None, *dict(report.antibiotics)], None, "selected_antibiotic")
    cell = next((cell for cell in report.cells if (cell.ward_code, cell.antibiotic_code) == (ward, antibiotic)), None)
    if cell:
        st.subheader(f"Selected cell · {dict(report.wards)[ward]} ({ward}) / {dict(report.antibiotics)[antibiotic]} ({antibiotic})")
        value = cell_value(report, cell)
        st.write(f"**{f'{value:.2f}%' if view == 'level' and value is not None else f'{value:+.2f} pp' if value is not None else STATUS[cell.status]}** · {STATUS[cell.status]}")
        for label, item in (("Selected level period", cell.level),) if view == "level" else (("Current 28 days", cell.current), ("Historical baseline", cell.baseline)):
            rate = f"{item.resistance_pct:.2f}%" if item.resistance_pct is not None else STATUS[item.status]
            if expert():
                st.caption(f"{label}: {item.start_date} – {item.end_date} · {rate} · S={item.n_susceptible} · R={item.n_resistant} · Tested={item.n_tested} · "
                    f"Eligible first={item.n_first_isolates} · Missing tests={item.n_first_isolates-item.n_tested} · Minimum N={item.minimum_sample_size} · {STATUS[item.status]}")
            else:
                st.caption(f'{label}: {item.start_date} – {item.end_date} · {rate} · {item.n_tested} tested observations.')
                if item.status=='insufficient_data':
                    st.info('Not enough laboratory observations to calculate a reliable percentage. This is not zero resistance.')
                elif item.status=='no_data':
                    st.info('No tested laboratory results are available for this period. This is not a measured zero.')
        st.caption("Ordinary selected cohort. Opening a cell does not assign an alert classification. Investigation recalculates current, historical and rolling results; changes remain exploratory.")
        st.button("Investigate this cell", on_click=open_map_investigation, args=(report, cell), disabled=report.analysis_date is None)
    else:
        st.info("Select a matrix cell to review counts and open an investigation.")
    if expert():
        with st.expander("Counts, quality and aggregate exports", expanded=False):
            st.caption(f"Minimum tested first isolates: {report.cells[0].current.minimum_sample_size}. "
                "ND means no selected-antibiotic S/R results, even if eligible first isolates exist. NA is based only on explicit urine-only metadata; "
                "other empty combinations are not assumed biologically unsupported. Cells and periods are independently ranked; do not sum denominators.")
            rows = pd.DataFrame(matrix_rows(report))
            st.dataframe(rows, hide_index=True, width="stretch")
            rows["Cohort filters"] = json.dumps(asdict(cohort), sort_keys=True)
            rows["Source"] = st.session_state.get("data_source", "Laboratory Dataset")
            rows["View"], rows["Level period"], rows["Antibiotic class"] = view, period, antibiotic_class
            st.download_button("Download matrix (CSV)", rows.to_csv(index=False).encode("utf-8-sig"), file_name="aei-sentinel-surveillance-map.csv", mime="text/csv", on_click="ignore")
            payload = report.to_dict()
            payload["source"] = st.session_state.get("data_source", "Laboratory Dataset")
            st.download_button("Download matrix (JSON)", json.dumps(payload, indent=2, allow_nan=False), file_name="aei-sentinel-surveillance-map.json", mime="application/json", on_click="ignore")
    else:
        expert_action('map')
    return report,cell
