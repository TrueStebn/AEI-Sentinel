"""Aggregate signal navigation; persisted origins contain locators, never results."""

import hashlib
from dataclasses import asdict

import streamlit as st

from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine
from src.resistance import ResistanceFilters
from src.workspaces import EXPLORER_FILTERS


def database_fingerprint(settings):
    return hashlib.sha256(str(settings.database_path.resolve()).casefold().encode()).hexdigest()


def origin_locator(result, kind, settings, source):
    return {"kind": kind, **asdict(result.filters), "analysis_date": result.analysis_date.isoformat(),
            "data_version": result.data_version, "source": source, "database_fingerprint": database_fingerprint(settings)}


def open_investigation(result, kind, settings):
    from src.workspace_interface import autosave_workspace
    for name in EXPLORER_FILTERS:
        st.session_state.pop(name, None)
    st.session_state.update(asdict(result.filters))
    st.session_state.update(navigation="Explore", explore_view="Investigation", explorer_mode="antibiotic",
        analysis_date=result.analysis_date, follow_latest_date=False,
        explorer_origin=origin_locator(result, kind, settings, st.session_state.get("data_source", "Laboratory Dataset")))
    autosave_workspace()


def investigation_button(results, kind, settings):
    selected = st.selectbox("Select surveillance signal" if kind == "preliminary" else "Select supported alert",
        range(len(results)), key=f"_investigate_{kind}", format_func=lambda index:
        f"{results[index].filters.ward_code} / {results[index].filters.organism} / "
        f"{results[index].filters.antibiotic_code} / {results[index].filters.specimen} / {results[index].difference_pp:+.2f} pp")
    st.button("Open Investigation" if kind == "preliminary" else "Open Alert Investigation",
        key=f"_open_{kind}", on_click=open_investigation, args=(results[selected], kind, settings))
    if results[selected].shared_patients:
        st.caption("Additional Exploratory Evidence: this finding has shared tested patients. Review paired and restricted-population analyses separately.")
        st.button("Patient-Aware Investigation",key=f"_patient_aware_{kind}",on_click=open_patient_investigation,
                  args=(results[selected],kind,settings))


def open_patient_investigation(result,kind,settings):
    """Preserve a real original locator; selected ordinary cohorts get no origin."""
    from src.workspace_interface import autosave_workspace
    if kind is not None:
        open_investigation(result,kind,settings)
    else:
        for name in EXPLORER_FILTERS:
            st.session_state.pop(name,None)
        st.session_state.update(asdict(result.filters))
        st.session_state.update(navigation="Explore",explore_view="Investigation",explorer_mode="antibiotic",explorer_origin=None,
            analysis_date=result.analysis_date,follow_latest_date=False)
    st.session_state["explorer_section"]="patient_aware"
    autosave_workspace()


def resolve_origin(origin, settings, source, version):
    """Recompute evidence only for the exact originating source revision."""
    if not origin or origin["source"] != source or origin["data_version"] != version or origin["database_fingerprint"] != database_fingerprint(settings):
        return None
    scan = AnomalyEngine(settings).scan(as_of_date=origin["analysis_date"])
    if scan.data_version != version:
        raise DatasetChangedError("The original signal and current report no longer share one revision. Refresh data.")
    filters = ResistanceFilters(**{key: origin[key] for key in ("antibiotic_code", "ward_code", "organism", "specimen")})
    result = next((item for item in scan.results if item.filters == filters), None)
    if origin["kind"] == "BY-alert" and result not in scan.alerts:
        return None
    return result


def open_map_investigation(report, cell):
    """An ordinary matrix selection transfers a cohort, never an alert origin."""
    from src.workspace_interface import autosave_workspace
    for name in EXPLORER_FILTERS:
        st.session_state.pop(name, None)
    cohort = report.cohort
    st.session_state.update(navigation="Explore", explore_view="Investigation", explorer_mode="antibiotic", explorer_origin=None,
        ward_code=cell.ward_code, antibiotic_code=cell.antibiotic_code, organism=cohort.organism, specimen=cohort.specimen,
        analysis_date=report.analysis_date, follow_latest_date=False, explorer_sex=cohort.sex,
        explorer_age_group="custom" if cohort.age_min is not None or cohort.age_max is not None else "all",
        explorer_age_min=cohort.age_min if cohort.age_min is not None else 0,
        explorer_age_max=cohort.age_max if cohort.age_max is not None else 130,
        explorer_ward_type=cohort.ward_type, explorer_institution=cohort.institution,
        explorer_home_area=cohort.home_area, explorer_admission_timing=cohort.admission_timing,
        explorer_group_period=report.period if report.view == "level" else "current")
    # Source selection and Replay ownership remain untouched.
    autosave_workspace()
