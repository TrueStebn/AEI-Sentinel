"""Advanced investigation UI: source signal, subgroup description and evidence."""

import json
from dataclasses import asdict

import pandas as pd
import streamlit as st

from src.advanced_analysis import AGE_GROUPS, AdvancedAnalysisEngine, CohortFilters
from src.analysis_service import DatasetChangedError
from src.database import connect
from src.explorer_charts import group_chart, period_rows, trend_chart
from src.interface import _save_filter, _select, render_analysis_date
from src.investigations import resolve_origin
from src.phenotype_analysis import MARKER_SPECIES
from src.patient_aware_analysis import PatientAwareEngine
from src.patient_aware_interface import render_patient_aware, validate_matches_advanced
from src.investigation_presentation import render_summary
from src.design import expert
from src.presentation import expert_action,guided_quality,cohort_caption,limitations,saved_choice
from src.ux_state import selected_cohort, investigation_context
from src.chart_states import render_chart

LABELS = {"antibiotic": "Antibiotic resistance", "phenotype": "Phenotype Monitoring",
    "F": "Female", "M": "Male", "all": "All ages", "custom": "Custom age range",
    "in": "Inpatient", "out": "Outpatient", "hospital": "Hospital", "primary_care": "Primary care",
    "all_applicable": "All valid inpatient admissions", "days_1_2": "Hospital days 1–2", "day_3_plus": "Hospital day 3 or later",
    "sex": "Sex", "age": "Age groups", "ward": "Wards", "ward_type": "Ward type", "institution": "Institution",
    "home_area": "Home areas", "admission_timing": "Admission timing", "current": "Current 28 days",
    "baseline": "Historical baseline — preceding 365 days", "annual": "Rolling 365 days (overlapping)",
    "no_data": "No Data", "insufficient_data": "Insufficient Data", "unsupported": "Unsupported combination",
    "analysis_limited": "Analysis Limited", "potential_increase": "Exploratory increase — unadjusted",
    "potential_decrease": "Exploratory decrease — unadjusted", "no_significant_change": "Exploratory criteria not met"}


def _label(value):
    return "All" if value is None else LABELS.get(value, value)


def _choice(label, values, default, name, *, prefix="explorer_", formatter=_label):
    """Body widget with the same canonical state/callback contract as the sidebar."""
    key = prefix + name
    if key not in st.session_state or st.session_state[key] not in values:
        st.session_state[key] = default
    widget = "_" + key
    # Use a sentinel so None remains selectable in all supported Streamlit versions.
    options = ["__all__" if value is None else value for value in values]
    st.session_state[widget] = "__all__" if st.session_state[key] is None else st.session_state[key]
    selected = st.selectbox(label, options, format_func=lambda value: formatter(None if value == "__all__" else value),
        key=widget, on_change=_save_filter, args=(key, widget))
    return None if selected == "__all__" else selected


def _monthly_trend():
    from src.workspace_interface import autosave_workspace
    st.session_state['explorer_frequency']='Monthly'
    autosave_workspace()


def _number(label, name, default, *, prefix="explorer_"):
    key = prefix + name
    st.session_state.setdefault(key, default)
    widget = "_" + key
    st.session_state[widget] = st.session_state[key]
    return st.number_input(label, min_value=0, max_value=130, step=1, key=widget,
                           on_change=_save_filter, args=(key, widget))


def _dimensions(settings, expected_version):
    with connect(settings, read_only=True) as con:
        con.execute("BEGIN")
        if con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0] != expected_version:
            raise DatasetChangedError("Filter metadata changed. Refresh data before continuing.")
        return {key: [row[0] for row in con.execute(query)] for key, query in {
            "home": "SELECT DISTINCT home_area FROM patients ORDER BY home_area",
            "type": "SELECT DISTINCT ward_type FROM wards ORDER BY ward_type",
            "institution": "SELECT DISTINCT institution FROM wards ORDER BY institution"}.items()}


def _rate(period):
    return f"{period.percentage:.2f}%" if period.percentage is not None else LABELS.get(period.status, period.status)


def _period_caption(period, phenotype):
    if phenotype:
        st.caption(f"Positive: {period.n_positive:,} · Negative: {period.n_negative:,} · Eligible: {period.n_observed:,}")
    else:
        st.caption(f"S: {period.n_negative:,} · R: {period.n_positive:,} · Tested: {period.n_observed:,}")
    st.caption(f"{period.start_date} – {period.end_date}")
    if period.confidence_interval:
        ci = period.confidence_interval
        st.caption(f"{100*ci.confidence_level:g}% Wilson CI: {ci.lower_pct:.2f}%–{ci.upper_pct:.2f}%")


def _original(origin, settings, catalog):
    if not origin:
        return
    st.caption(f"Original finding: {origin['kind']} · {origin['ward_code']} / {origin['organism']} / {origin['antibiotic_code']} / {origin['specimen']}. Subgroup evidence is separate.")
    if not expert():
        result=resolve_origin(origin,settings,st.session_state.get('data_source','Laboratory Dataset'),catalog.version)
        if result is None:
            st.info('The original finding is unavailable in this source revision. Its saved context remains; old counts are not applied to this report.')
        else:
            cohort=selected_cohort(st.session_state)
            matches=all(getattr(cohort,key)==origin[key] for key in ('ward_code','organism','specimen')) and st.session_state.get('antibiotic_code')==origin['antibiotic_code']
            restricted=any(getattr(cohort,key) is not None for key in ('sex','age_min','age_max','home_area','ward_type','institution','admission_timing'))
            if not matches or restricted or str(st.session_state.get('analysis_date'))!=origin['analysis_date']:
                limitations(result.limitations)
        st.caption('The original finding applies to its original group. Any patient filters below describe a separate investigation.')
        st.button('Clear original signal context',on_click=_clear_origin)
        return
    with st.expander("Original surveillance signal — original cohort"):
        st.caption(f"{origin['kind']} · {origin['ward_code']} / {origin['organism']} / {origin['antibiotic_code']} / {origin['specimen']} · "
                   f"Analysis date: {origin['analysis_date']} · Source: {origin['source']} · Version: {origin['data_version']}")
        result = resolve_origin(origin, settings, st.session_state.get("data_source", "Laboratory Dataset"), catalog.version)
        if result is None:
            st.info("Original evidence is unavailable in this source revision. Its saved context is retained; no old counts are applied to this report.")
        else:
            st.write(f"Original current S/R/N: {result.current.n_susceptible}/{result.current.n_resistant}/{result.current.n_tested}; "
                     f"baseline S/R/N: {result.baseline.n_susceptible}/{result.baseline.n_resistant}/{result.baseline.n_tested}. "
                     f"Original change: {result.difference_pp:+.2f} pp; shared patients: {result.shared_patients}.")
            st.caption(f"Original current: {result.current.start_date} – {result.current.end_date}; "
                       f"baseline: {result.baseline.start_date} – {result.baseline.end_date}.")
            if origin["kind"] == "BY-alert":
                st.caption(f"Original BY-adjusted p={result.adjusted_p_value:.4g}; family={result.correction_family_size} eligible comparisons.")
            else:
                st.caption("Original Preliminary Signal is descriptive; it does not establish statistical significance.")
        st.info("This original signal does not classify the currently selected subgroup. The subgroup evidence below is recalculated separately.")
        st.button("Clear original signal context", on_click=_clear_origin)


def _clear_origin():
    from src.workspace_interface import autosave_workspace
    st.session_state["explorer_origin"] = None
    autosave_workspace()


def render_explorer(settings, catalog):
    st.subheader("Focused investigation")
    if expert():
        st.write("Investigate laboratory patterns, compare patient groups and review the strength of the evidence.")
        mode = _choice("Investigation mode", ["antibiotic", "phenotype"], "antibiotic", "mode")
    else:
        with st.popover('Analysis type · '+LABELS.get(st.session_state.get('explorer_mode','antibiotic'),'Antibiotic resistance')):
            mode = _choice("Investigation mode", ["antibiotic", "phenotype"], "antibiotic", "mode")
    phenotype = mode == "phenotype"
    st.sidebar.divider()
    st.sidebar.markdown("#### Investigation context")
    ward = _select("Ward", [None, *catalog.wards], None, "ward_code", lambda value: catalog.wards.get(value, "All wards"))
    organism = _select("Organism", [None, *catalog.organisms], catalog.default_organism, "organism", lambda value: value or "All organisms")
    specimen = _select("Specimen Type", [None, *catalog.specimens], catalog.default_specimen, "specimen", lambda value: value.title() if value else "All specimen types")
    if phenotype:
        code = _choice("Phenotype marker", list(MARKER_SPECIES), "ESBL", "marker")
        st.caption(f"{code} percentage = marker-positive eligible first isolates / all eligible first isolates × 100. "
                   "Negative flags remain in the denominator. No susceptibility test is required.")
        st.caption("Prototype supported organisms: " + "; ".join(MARKER_SPECIES[code]) + ".")
    else:
        if not catalog.antibiotics:
            st.info("No antibiotic reference data yet. Phenotype analysis remains available as isolates arrive.")
            code = "CIP"  # Placeholder parameter on an empty source; no percentage can be reported.
        else:
            code = _select("Antibiotic", list(catalog.antibiotics), catalog.default_antibiotic, "antibiotic_code", catalog.antibiotics.get)
        if expert():
            st.caption("Resistance (%) = R / (S + R) × 100 among tested first isolates. Untested first isolates are counted separately.")
    render_analysis_date(catalog)
    dimensions = _dimensions(settings, catalog.version)
    # Canonical preferences survive cleanup of Expert-only widget keys.
    for name,value in {'sex':None,'age_group':'all','home_area':None,'ward_type':None,'institution':None,
                       'admission_timing':None,'group_dimension':'age','group_period':'annual'}.items():
        st.session_state.setdefault('explorer_'+name,value)
    for name,values,default in (('sex',[None,'F','M'],None),('age_group',['all',*AGE_GROUPS,'custom'],'all'),
        ('home_area',[None,*dimensions['home']],None),('ward_type',[None,*dimensions['type']],None),
        ('institution',[None,*dimensions['institution']],None),('admission_timing',[None,'all_applicable','days_1_2','day_3_plus'],None)):
        saved_choice('explorer_'+name,values,default)
    current=selected_cohort(st.session_state)
    sex,home,ward_type,institution,admission=current.sex,current.home_area,current.ward_type,current.institution,current.admission_timing
    age_group=st.session_state['explorer_age_group']
    ages=current.age_min,current.age_max
    if expert():
        with st.expander("Patient and healthcare filters", expanded=False):
            a, b, c = st.columns(3)
            with a:
                sex = _choice("Sex", [None, "F", "M"], None, "sex")
                ward_type = _choice("Ward type", [None, *dimensions["type"]], None, "ward_type")
            with b:
                age_group = _choice("Age group", ["all", *AGE_GROUPS, "custom"], "all", "age_group")
                institution = _choice("Institution", [None, *dimensions["institution"]], None, "institution")
            with c:
                home = _choice("Home Area", [None, *dimensions["home"]], None, "home_area")
                admission = _choice("Admission timing", [None, "all_applicable", "days_1_2", "day_3_plus"], None, "admission_timing")
            ages = AGE_GROUPS.get(age_group, (None, None))
            if age_group == "custom":
                low, high = st.columns(2)
                with low:
                    lower = _number("Minimum age (inclusive)", "age_min", 0)
                with high:
                    upper = _number("Maximum age (inclusive)", "age_max", 130)
                ages = lower, upper
            st.caption("Age at sampling. Hospital day = sample date − admission date + 1 (calendar days). "
                       "Timing filters include only valid inpatient admissions; this grouping does not establish hospital-acquired infection.")
    group_by=saved_choice('explorer_group_dimension',["sex", "age", "ward", "ward_type", "institution", "home_area", "admission_timing"],'age')
    group_period=saved_choice('explorer_group_period',['current','baseline','annual'],'annual')
    if expert():
        with st.expander("Group comparison options"):
            group_column, period_column = st.columns(2)
            with group_column:
                _choice("Compare groups by", ["sex", "age", "ward", "ward_type", "institution", "home_area", "admission_timing"], "age", "group_dimension")
            with period_column:
                _choice("Group comparison period", ["current", "baseline", "annual"], "annual", "group_period")
    cohort = CohortFilters(ward, organism, specimen, sex, *ages, home, ward_type, institution, admission)
    cohort_caption(cohort)
    with st.spinner("Calculating one consistent source revision..."):
        report = AdvancedAnalysisEngine(settings).analyze(cohort, mode=mode, code=code,
            as_of_date=st.session_state.get("analysis_date") if catalog.latest_sample else None,
            group_by=group_by, group_period=group_period, expected_version=catalog.version,
            signal_threshold=st.session_state.get("signal_threshold", settings.signal_minimum_change_pp))
    st.session_state["_active_investigation"] = investigation_context(st.session_state) if report.supported and report.analysis_date and not phenotype else None
    aware = None
    if not phenotype:
        aware = PatientAwareEngine(settings).analyze(cohort,code=code,as_of_date=report.analysis_date,
            expected_version=report.data_version,source=st.session_state.get("data_source","Laboratory Dataset"))
        validate_matches_advanced(aware,report)
        render_summary(report,aware,catalog.wards.get(ward,'All wards'))
    _original(st.session_state.get("explorer_origin"), settings, catalog)
    if expert():
        with st.expander("Period counts and full-population method",expanded=False) if not phenotype else st.container():
            st.subheader("Currently selected subgroup")
            st.caption(f"{catalog.wards.get(ward, 'All wards')} / {organism or 'All organisms'} / {code} / {specimen or 'All specimens'} · "
                       f"{_label(sex)} · {_label(age_group)} · {home or 'All home areas'} · Analysis date: {report.analysis_date}")
            if not report.supported:
                st.warning("Unsupported organism-marker combination. This is not a negative marker result or a 0% observation.")
            cards = st.columns(3)
            for column, name in zip(cards, ("current", "baseline", "annual")):
                with column:
                    period = getattr(report, name)
                    st.metric(LABELS[name], _rate(period))
                    _period_caption(period, phenotype)
            st.caption("Historical baseline ends before the current window. Rolling 365 days overlaps the current window and is descriptive.")
            assessment = report.assessment
            st.subheader("Subgroup change assessment")
            st.markdown(f"**{LABELS.get(assessment.status, assessment.status)}**")
            st.write(assessment.explanation)
            st.metric("Subgroup change from historical baseline", f"{assessment.difference_pp:+.2f} pp" if assessment.difference_pp is not None else "Unavailable")
            if assessment.preliminary_signal:
                st.warning("Preliminary Signal for this subgroup: volume and absolute change thresholds met. Descriptive only; no adjusted alert is asserted.")
            if assessment.p_value is not None:
                st.caption(f"{assessment.test_method} · Unadjusted p={assessment.p_value:.4g}. No multiplicity adjustment for interactive filter exploration. "
                           "This is not a Statistically Supported Alert from the BY surveillance scan.")
            else:
                st.caption("Fisher test withheld: sample size, support or patient independence conditions are not satisfied.")
            for limitation in assessment.limitations:
                st.warning(limitation.replace("tested patients", "observed patients") if phenotype else limitation)
    elif phenotype:
        st.subheader('Investigation Summary')
        st.caption(f'{catalog.wards.get(ward,"All wards")} / {organism or "All organisms"} / {code} / {specimen or "All specimens"} · {report.analysis_date or "No Data"}')
        for column,name in zip(st.columns(3),('current','baseline','annual')):
            column.metric(LABELS[name],_rate(getattr(report,name)))
        st.write('A preliminary change was observed; this describes a pattern for review, not a supported alert.' if report.assessment.preliminary_signal
                 else 'No preliminary finding meets the current rules for this group. This does not establish absence of change.')
        guided_quality(report)
    section = st.session_state.get("explorer_section","overview")
    names=['Resistance Trends','Patient Groups','Evidence Quality','Patient-Aware Analysis'] if expert() else ['Resistance Trends','Patient Groups','Interpretation']
    tabs=st.tabs(names,default='Patient-Aware Analysis' if expert() and section=='patient_aware' else 'Resistance Trends',
        key=('_explorer_tabs_'+section) if expert() else '_explorer_guided_tabs')
    trends,comparisons,evidence=tabs[:3]
    patient_aware=tabs[3] if expert() else None
    if expert() and phenotype:
        with patient_aware:
            st.subheader('Patient-Aware Investigation')
            st.info('Patient-aware investigation uses antibiotic S/R outcomes. Select Antibiotic resistance to examine returning patients.')
    with trends:
        saved_choice('explorer_frequency',['Monthly','Weekly'],'Monthly')
        st.session_state['_explorer_frequency']=st.session_state['explorer_frequency']
        frequency = st.radio("Trend frequency", ["Monthly", "Weekly"], horizontal=True, key="_explorer_frequency",
            on_change=_save_filter,args=('explorer_frequency','_explorer_frequency'))
        points = report.monthly if frequency == "Monthly" else report.weekly
        render_chart(points,lambda:trend_chart(report,points),key="explorer_trend",frequency=frequency.lower(),
            aggregate=report.current,supported=report.supported,minimum=report.current.minimum_sample_size,
            monthly=report.monthly if frequency=='Weekly' else None,monthly_action=_monthly_trend,
            denominator='eligible first isolates' if phenotype else 'eligible tested first isolates')
    with comparisons:
        if not expert():
            a,b=st.columns(2)
            with a:
                _choice('Compare groups by',["sex", "age", "ward", "ward_type", "institution", "home_area", "admission_timing"],'age','group_dimension')
            with b:
                _choice('Group comparison period',['current','baseline','annual'],'annual','group_period')
        st.caption((f"Descriptive comparison by {_label(group_by).lower()}. Only the restriction for this dimension is relaxed; all other filters remain. "
                   "Each group selects its own first isolates. Ward comparisons deduplicate separately within each ward.") if expert() else
                   f'Compare observed percentages by {_label(group_by).lower()}. Other patient filters remain active. Each group is calculated separately.')
        if group_by == "admission_timing":
            st.caption("Only valid inpatient admissions enter these groups. An outpatient-only context has no applicable groups.")
        render_chart([p for _,p in report.groups],lambda:group_chart(report),key="explorer_groups",frequency="group comparison",supported=report.supported,minimum=report.current.minimum_sample_size,
            denominator='eligible first isolates' if phenotype else 'eligible tested first isolates')
        rows = period_rows(report, report.groups)
        available = [(label, period) for label, period in report.groups if period.percentage is not None]
        if available:
            reference = st.selectbox("Compare with group", [label for label, _ in available], key="_explorer_reference")
            reference_rate = next(period.percentage for label, period in available if label == reference)
            for row, (_, period) in zip(rows, report.groups):
                row["Difference vs reference (pp)"] = period.percentage-reference_rate if period.percentage is not None else None
        rate_column = "Phenotype (%)" if phenotype else "Resistance (%)"
        count_columns = ["Marker positive", "Marker negative", "Eligible denominator"] if phenotype else ["S", "R", "Tested"]
        columns = ["Period", rate_column]
        if available:
            columns.append("Difference vs reference (pp)")
        columns.extend([*count_columns, "Eligible first isolates", *([] if phenotype else ["Missing tests"]),
                        "Status", "Wilson lower (%)", "Wilson upper (%)", "Distinct observed patients"])
        if not expert():
            columns=['Period',rate_column,*(['Difference vs reference (pp)'] if available else []),'Eligible denominator' if phenotype else 'Tested','Status']
            for row in rows:
                row['Status']={'ok':'Available','no_data':'No Data','insufficient_data':'Insufficient Data','unsupported':'Unsupported combination'}.get(row.get('Status'),row.get('Status'))
        frame = pd.DataFrame(rows, columns=columns)
        if expert():
            st.dataframe(frame, hide_index=True, width="stretch", column_config={name: st.column_config.NumberColumn(format="%.2f")
                for name in (rate_column, "Difference vs reference (pp)", "Wilson lower (%)", "Wilson upper (%)") if name in columns})
        else:
            # A simple table has no technical dataframe-download toolbar.
            formats={key:'{:.2f}' for key in (rate_column,'Difference vs reference (pp)') if key in columns}
            st.table(frame.style.format(formats,na_rep='Unavailable').hide(axis='index'))
        if report.groups:
            period = report.groups[0][1]
            st.caption(f"Group period: {period.start_date} – {period.end_date}. The full parameters are included in aggregate exports.")
        st.caption(f"Patients appearing in multiple displayed groups: {report.patients_in_multiple_groups}. " +
                   ("Group denominators must not be summed. Differences and Wilson intervals do not establish between-group significance or causality; no between-group tests are performed." if expert() else
                    "Do not add group counts: a patient may appear in more than one group. Differences describe observed patterns, not a proven effect or cause."))
    with evidence:
        if expert():
            st.subheader("Investigation quality and reliability")
            for name in ("current", "baseline", "annual"):
                period = getattr(report, name)
                st.write(f"**{LABELS[name]}:** eligible first isolates={period.n_first_isolates}; "
                         f"{'eligible denominator' if phenotype else 'tested'}={period.n_observed}; "
                         f"{'marker-positive' if phenotype else 'missing/unavailable tests'}={period.n_positive if phenotype else period.missing_tests}; "
                         f"distinct observed patients={period.distinct_observed_patients}.")
                if period.percentage is None:
                    st.caption(f"{_rate(period)}: {period.n_observed} observed first isolates; at least {period.minimum_sample_size} are required for a percentage. No statistical conclusion follows from this period.")
            st.caption(f"Shared observed patients between current and baseline: {assessment.shared_patients}. "
                       f"Minimum N: {report.current.minimum_sample_size}. Marginal Wilson intervals are withheld for repeated patients within a period; "
                       "they are not intervals for the difference. Fisher testing is withheld for known patient dependence.")
            st.caption("All cohort filters precede first-isolate ranking (patient / organism / specimen / reporting period). "
                       "Susceptibility lookup and marker positivity follow selection. A later tested or marker-positive isolate cannot replace an earlier eligible isolate.")
            st.caption(f"Source: {st.session_state.get('data_source', 'Laboratory Dataset')} · Data version: {report.data_version} · "
                       f"Latest relevant laboratory sample: {report.latest_relevant_sample or 'No Data'} · Latest source sample: {catalog.latest_sample or 'No Data'}")
            st.caption("Observed associations do not identify the cause of a change, establish clinical infection, or provide treatment recommendations. "
                       "This prototype registry is a supported analysis scope, not a universal microbiological classification.")
            st.dataframe(pd.DataFrame(period_rows(report, [(LABELS[key], getattr(report, key)) for key in ('current', 'baseline', 'annual')])), hide_index=True, width="stretch")
            payload = report.to_dict()
            payload["source"] = st.session_state.get("data_source", "Laboratory Dataset")
            payload["signal_threshold_pp"] = st.session_state.get("signal_threshold", settings.signal_minimum_change_pp)
            payload["original_signal_context"] = st.session_state.get("explorer_origin")
            st.download_button("Download investigation (JSON)", json.dumps(payload, indent=2, allow_nan=False),
                file_name="aei-sentinel-investigation.json", mime="application/json", on_click="ignore")
            export_periods = [(LABELS[key], getattr(report, key)) for key in ("current", "baseline", "annual")]
            export_periods += [(f"Week {i+1}", item) for i, item in enumerate(report.weekly)]
            export_periods += [(f"Month {i+1}", item) for i, item in enumerate(report.monthly)]
            export_periods += [(f"Group {label}", item) for label, item in report.groups]
            export = pd.DataFrame(period_rows(report, export_periods))
            export["Mode"], export["Code"], export["Source"] = mode, code, payload["source"]
            export["Cohort filters"] = json.dumps(asdict(cohort), sort_keys=True)
            export["Group dimension"], export["Group period"] = group_by, group_period
            st.download_button("Download investigation periods (CSV)", export.to_csv(index=False).encode("utf-8-sig"),
                file_name="aei-sentinel-investigation-periods.csv", mime="text/csv", on_click="ignore")
            st.caption("Exports contain aggregate results and analysis parameters only. No patient identifiers or patient-level records.")
            if aware is not None:
                from src.investigation_pdf import render_investigation_export
                render_investigation_export(report,aware,settings)
        else:
            st.subheader('What limits this comparison?')
            guided_quality(report)
            st.caption(f'Current tested observations: {report.current.n_observed}; historical: {report.baseline.n_observed}. '
                'Periods select observations separately; trend and group counts must not be added.')
            if aware is not None:
                from src.investigation_pdf import render_investigation_export
                render_investigation_export(report,aware,settings)
            else:
                st.caption('A selected antibiotic is required for an investigation PDF. Phenotype aggregate downloads are available in Expert Mode.')
    if not phenotype and expert():
        with patient_aware:
            render_patient_aware(aware,settings)

    expert_action("investigation")
