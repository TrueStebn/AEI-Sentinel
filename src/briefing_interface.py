"""Compact briefing, original-scope investigations, quality details and safe PDFs."""

import json
from dataclasses import asdict
from functools import partial

import pandas as pd
import streamlit as st

from src.briefing import (BriefingFilters, ORDERINGS, QUALITY_LABELS, build_briefing, finding_rows,
                          freshness, ordered_findings, validate_revision)
from src.briefing_pdf import render_pdf, report_manifest
from src.explorer_charts import trend_chart
from src.chart_states import render_chart
from src.explorer_interface import _choice
from src.interface import _select, render_analysis_date,_save_filter
from src.investigations import open_investigation, open_patient_investigation
from src.signals import surveillance_signals
from src.design import expert
from src.presentation import expert_action,guided_quality,limitations

choose = partial(_choice, prefix="briefing_", formatter=lambda value: value or "All")


def render_quality(report):
    st.subheader("Data Quality Center")
    st.caption("Quality classifications explain analytical availability. They are not a clinical safety certification.")
    global_q, q = report.global_quality, report.selected_quality
    if not expert():
        st.write(f"{q['insufficient_data']} selected comparisons need more laboratory observations; {q['no_data']} have no tested results in at least one period.")
        st.info(f"{q['analysis_limited']} selected comparisons are limited by repeated patients. They cannot support the usual comparison test. "
            'No alert does not establish absence of change or clinical safety.')
        st.caption('Quality counts refer to comparisons, not unique patients. Missing results are not treated as susceptible.')
        if report.selected_analysis:
            guided_quality(report.selected_analysis)
        if freshness(report)['latest_sample_after_analysis']:
            st.info('The source has samples after this reporting date. Those later observations are excluded.')
        return
    rows = [{"Classification": label, "Full source scan": global_q[key], "Filtered exact combinations": q[key]}
            for key,label in QUALITY_LABELS.items()]
    rows += [{"Classification": "Eligible statistical comparisons", "Full source scan": global_q["eligible"], "Filtered exact combinations": q["eligible"]}]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.info(f"{q['analysis_limited']} filtered comparisons meet the configured sample-size requirement but cannot undergo the current independent-sample Fisher test because of known patient dependence. "
            f"{q['dependence_any']} filtered comparisons have dependence limitations across all sample-size categories.")
    st.caption(f"Scan universe: {global_q['combinations']} observed exact ward / organism / antibiotic / specimen combinations in the 393-day comparison span; "
               f"{q['combinations']} match the filters. Empty, never-observed reference combinations are not invented.")
    st.write(f"**Missing susceptibility opportunities:** current {q['current_missing_opportunities']:,} / {q['current_first_opportunities']:,}; "
             f"baseline {q['baseline_missing_opportunities']:,} / {q['baseline_first_opportunities']:,} eligible first-isolate/antibiotic opportunities.")
    st.caption("These counts describe test opportunities across exact combinations, not unique patients. Unavailable or unreported tests cannot be distinguished. "
               "Do not sum denominators across antibiotics, cohorts or periods.")
    f = freshness(report)
    st.caption(f"Global latest sample: {report.latest_sample or 'No Data'} · Calendar age today (UTC): {f['sample_age_days'] if f['sample_age_days'] is not None else 'No Data'} days · "
               f"Last completed import (UTC): {report.last_successful_import or 'None'}")
    if report.source != "Laboratory Dataset":
        st.caption("Replay freshness reflects simulated historical time. It is not a live hospital feed.")
    if report.selected_analysis:
        analysis = report.selected_analysis
        st.write(f"**Selected analysis freshness:** {analysis.latest_relevant_sample or 'No Data'} · {analysis.code}")
        st.caption(f"Selected current S/R/N: {analysis.current.n_negative}/{analysis.current.n_positive}/{analysis.current.n_observed}; "
                   f"baseline: {analysis.baseline.n_negative}/{analysis.baseline.n_positive}/{analysis.baseline.n_observed}; "
                   f"rolling 365 days: {analysis.annual.n_negative}/{analysis.annual.n_positive}/{analysis.annual.n_observed}. "
                   "This selected pooled analysis is separate from the exact-combination quality counts above.")
        st.caption(f"Selected eligible first / missing tests: current {analysis.current.n_first_isolates}/{analysis.current.missing_tests}; "
                   f"baseline {analysis.baseline.n_first_isolates}/{analysis.baseline.missing_tests}; "
                   f"rolling 365 days {analysis.annual.n_first_isolates}/{analysis.annual.missing_tests}.")
    if f["latest_sample_after_analysis"]:
        st.info("The source contains samples after this selected reporting date. Those later observations are excluded from the analyses.")
    with st.expander("Detailed quality explanations and actual sample sizes"):
        st.write(f"No Data: at least one comparison period has N=0 S/R results. Insufficient Data: both periods have observations but at least one N is below {report.thresholds['minimum_sample_size']}. "
                 "Patient-dependence limited: both denominators meet threshold, but patients repeat between periods or within a pooled period. "
                 "Eligible comparisons: the original engine performed Fisher testing; all eligible p-values enter the full-source BY family.")
        rows = []
        for r in report.selected_results:
            rows.append({**asdict(r.filters), "Status": QUALITY_LABELS[r.status], "Current N": r.current.n_tested,
                "Baseline N": r.baseline.n_tested, "Current missing": r.current.n_first_isolates-r.current.n_tested,
                "Baseline missing": r.baseline.n_first_isolates-r.baseline.n_tested,
                "Shared patients": r.shared_patients, "Limitations": " | ".join(r.limitations) or "None detected"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _queue(report, settings):
    st.subheader("Prioritized Investigation Queue")
    left, right = st.columns(2)
    with left:
        ordering = choose("Sort findings by", list(ORDERINGS), ORDERINGS[0], "sort")
    with right:
        status = choose("Finding status", ["All", "Preliminary", "Statistically supported"], "All", "status")
    findings = ordered_findings(report.selected_findings, ordering, status)
    st.caption("Review Priority: supported findings first, then absolute change, then the smaller denominator, then cohort labels. "
               "This is a descriptive ordering, not a clinical risk score. A high observed resistance percentage alone never creates a finding.")
    if not findings:
        st.info("No findings meet the existing surveillance rules and queue filters. This does not prove absence of change.")
        return
    # Paginated actions keep every displayed row directly investigable without hundreds of widgets.
    pages = list(range(1,(len(findings)+9)//10+1))
    page = st.selectbox("Queue page (10 findings)", pages, key="_briefing_queue_page") if len(pages)>1 else 1
    visible = findings[(page-1)*10:page*10]
    if expert():
        st.dataframe(pd.DataFrame(finding_rows(visible)), hide_index=True, width="stretch",
            column_config={name:st.column_config.NumberColumn(format="%.2f") for name in ("Current (%)","Baseline (%)","Change (pp)")})
    for index,item in enumerate(visible):
        r, f = item.comparison, item.comparison.filters
        a,b = st.columns([5,1])
        with a:
            st.caption(f"{(page-1)*10+index+1}. {f.ward_code} / {f.organism} / {f.antibiotic_code} / {f.specimen} · {r.difference_pp:+.2f} pp · "
                       f"{'Statistically supported' if item.kind == 'BY-alert' else 'Preliminary'} · Shared patients: {r.shared_patients}")
            limitations(r.limitations)
        with b:
            st.button("Investigate", key=f"_briefing_investigate_{index}", on_click=open_investigation,
                args=(r,item.kind,settings), help=f"Investigate {f.ward_code} / {f.organism} / {f.antibiotic_code} / {f.specimen}")
            if r.shared_patients and expert():
                st.button("Patient-aware",key=f"_briefing_patient_{index}",on_click=open_patient_investigation,args=(r,item.kind,settings),
                    help="Additional Exploratory Evidence: returning-patient pairs and a restricted-population sensitivity analysis")
    st.caption("Each investigation retains the original exact population, source, analysis date and revision. Patient filters in Explorer are a separate analysis.")


def _selected(report):
    analysis = report.selected_analysis
    if not analysis:
        st.info("No antibiotic references are available for a selected investigation yet.")
        return
    st.subheader(f"Selected descriptive investigation · {analysis.code}")
    columns = st.columns(3)
    for column,(label,period) in zip(columns,(("Current 28 days",analysis.current),("Historical baseline · preceding 365 days",analysis.baseline),("Rolling 365 days · overlapping",analysis.annual))):
        with column:
            st.metric(label, f"{period.percentage:.2f}%" if period.percentage is not None else QUALITY_LABELS.get(period.status,period.status))
            st.caption(f"S={period.n_negative} · R={period.n_positive} · N={period.n_observed}" if expert() else f'{period.n_observed} tested observations')
            st.caption(f"{period.start_date} – {period.end_date}")
            if expert():
                st.caption(f"Eligible first={period.n_first_isolates} · Missing={period.missing_tests}")
    st.caption(f"Latest relevant sample: {analysis.latest_relevant_sample or 'No Data'}. This pooled descriptive context does not inherit alerts from exact ward / organism / specimen scan rows.")
    if expert():
        st.write(analysis.assessment.explanation)
    else:
        guided_quality(analysis)
    render_chart(analysis.monthly,lambda:trend_chart(analysis,analysis.monthly),key="_briefing_selected_trend",
        frequency="monthly",aggregate=analysis.current,supported=analysis.supported,minimum=analysis.current.minimum_sample_size)


def render_briefing(settings,catalog):
    st.subheader("Surveillance Briefing")
    st.caption("Evidence for further review · Original surveillance rules · Aggregate laboratory observations")
    st.sidebar.divider()
    st.sidebar.markdown("#### Briefing scope")
    ward = _select("Ward",[None,*catalog.wards],None,"briefing_ward",lambda value:catalog.wards.get(value,"All wards"))
    organism = _select("Organism",[None,*catalog.organisms],None,"briefing_organism",lambda value:value or "All organisms")
    specimen = _select("Specimen Type",[None,*catalog.specimens],None,"briefing_specimen",lambda value:value.title() if value else "All specimen types")
    render_analysis_date(catalog)
    code = _select("Selected investigation antibiotic",list(catalog.antibiotics),catalog.default_antibiotic,"briefing_antibiotic",catalog.antibiotics.get) if catalog.antibiotics else None
    source = st.session_state.get("data_source","Laboratory Dataset")
    report = build_briefing(settings,filters=BriefingFilters(ward,organism,specimen),
        as_of_date=st.session_state.get("analysis_date") if catalog.latest_sample else None,source=source,
        expected_version=catalog.version,code=code,signal_threshold=st.session_state.get("signal_threshold",settings.signal_minimum_change_pp))
    seen = st.session_state.setdefault("_briefing_versions_seen",{})
    prior = seen.get(report.source_identity)
    if prior is not None and prior != report.data_version:
        st.info(f"New import revision: {prior} → {report.data_version}. Analytics have been recalculated. Import-to-import metric differences are unavailable because earlier revisions are not archived.")
    seen[report.source_identity] = report.data_version
    if expert():
        with st.expander("Source-wide context and quality counts",expanded=False):
            st.caption(f"Source: {source} · Dataset version: {report.data_version} · Reporting date: {report.analysis_date or 'No Data'}")
            st.caption(f"Latest laboratory sample: {report.latest_sample or 'No Data'} · Last successful import (UTC): {report.last_successful_import or 'No completed import'}")
            if source != "Laboratory Dataset":
                st.info(f"{source}: isolated replay / artificial demonstration data. This briefing describes only the active replay revision.")
            st.subheader("Full-source surveillance overview")
            q = report.global_quality
            cards = st.columns(3)
            for column,label,value in ((cards[0],"Preliminary surveillance signals",len(surveillance_signals(report.scan,report.thresholds['signal_change_pp_strictly_greater']))),
                (cards[1],"Statistically supported alerts",sum(item.kind=="BY-alert" for item in report.findings)),
                (cards[2],"Patient-dependence limited",q["analysis_limited"])):
                column.metric(label,value)
            cards = st.columns(3)
            for column,label,value in ((cards[0],"Insufficient Data comparisons",q["insufficient_data"]),(cards[1],"No Data comparisons",q["no_data"]),(cards[2],"Eligible statistical comparisons",q["eligible"])):
                column.metric(label,value)
            st.caption(f"Full-source scan: {q['combinations']} exact combinations; BY family: {report.scan.tested_comparisons}. Global counts remain global when filters change. "
                       "Preliminary signals can include supported alerts; these totals must not be added.")
    else:
        st.caption(f'{source} · v{report.data_version} · Reporting date: {report.analysis_date or "No Data"}. Latest source sample: {report.latest_sample or "No Data"}.')
        a,b=st.columns(2)
        a.metric('Preliminary surveillance signals',len(surveillance_signals(report.scan,report.thresholds['signal_change_pp_strictly_greater'])))
        b.metric('Statistically supported alerts',sum(item.kind=='BY-alert' for item in report.findings))
        st.caption('Source-wide counts stay global when filters change. Preliminary findings and supported alerts may overlap; do not add them.')
        if not report.scan.alerts:
            st.info('No change met the full statistical alert criteria. This does not mean resistance is absent; some comparisons cannot be tested reliably.')
    st.write(f"**Filtered scope:** {ward or 'All wards'} / {organism or 'All organisms'} / {specimen or 'All specimens'} · "
             f"{len(report.selected_results)} exact combinations · {len(report.selected_findings)} findings requiring review.")
    export_first=st.session_state.get('_reports_export_requested',False)
    if expert():
        queue,quality,exports = st.tabs(['Investigation Queue','Data Quality Center','Selected Analysis & PDF Reports'],
            default='Selected Analysis & PDF Reports' if export_first else 'Investigation Queue',key='_briefing_tabs_exports' if export_first else '_briefing_tabs')
    else:
        exports,queue,quality=st.tabs(['PDF Reports','Findings','Data Quality'],key='_briefing_guided_tabs')
    with queue:
        _queue(report,settings)
    with quality:
        render_quality(report)
    with exports:
        with st.expander('Selected analysis and trend') if expert() else st.container():
            _selected(report)
        st.subheader("Download surveillance report")
        scope_label = choose("PDF report scope",["Hospital-wide summary","Selected investigation"],"Hospital-wide summary","report_scope",
            formatter=lambda value:'Department-focused briefing' if value=='Selected investigation' else value)
        scope = "hospital" if scope_label=="Hospital-wide summary" else "selected"
        st.caption('Hospital-wide summary: all source wards and exact surveillance findings. Selected investigation: the ward / organism / specimen briefing scope with the selected antibiotic; patient filters are excluded. Use Current Investigation to include those filters.')
        if scope=='hospital':
            st.info(f'**Selected PDF scope: Hospital-wide summary**\n\nAll wards in {source} · v{report.data_version} · {report.analysis_date or "No Data"}. The selected ward and patient filters do not restrict this export.')
        else:
            st.info(f'**Selected PDF scope: Department / selected briefing investigation**\n\n{source} · v{report.data_version} · {report.analysis_date or "No Data"} · {ward or "All wards"} / {organism or "All organisms"} / {code or "No antibiotic"} / {specimen or "All specimens"}.')
        # A non-widget key retains this export choice when its Expert control disappears.
        st.session_state.setdefault('briefing_include_patient',st.session_state.get('_briefing_include_patient',False))
        requested_patient=st.session_state['briefing_include_patient'] if scope=='selected' else False
        if expert() and scope=='selected':
            st.session_state['_briefing_include_patient']=st.session_state['briefing_include_patient']
            requested_patient=st.checkbox('Include patient-aware evidence',key='_briefing_include_patient',disabled=report.selected_analysis is None,
                on_change=_save_filter,args=('briefing_include_patient','_briefing_include_patient'))
        elif requested_patient and report.selected_analysis is not None:
            st.caption('This selected PDF includes the additional patient-aware evidence previously selected in Expert Mode.')
        # A disabled checkbox can retain True after switching to an empty source.
        include_patient = bool(requested_patient and report.selected_analysis is not None)
        signature = (report.source_identity,report.data_version,str(report.analysis_date),json.dumps(asdict(report.filters),sort_keys=True),code,json.dumps(report.thresholds,sort_keys=True),scope,include_patient)
        stored = st.session_state.get("_briefing_pdf")
        if stored and stored["signature"] != signature:
            st.session_state.pop("_briefing_pdf",None)
            stored = None
        if st.button("Generate PDF report",type="primary"):
            patient = None
            if include_patient:
                from src.patient_aware_analysis import PatientAwareEngine
                a = report.selected_analysis
                patient = PatientAwareEngine(settings).analyze(a.cohort,code=a.code,as_of_date=a.analysis_date,
                    expected_version=report.data_version,source=source)
            data = render_pdf(report,settings,scope=scope,patient_aware=patient)
            stored = {"signature":signature,"pdf":data,"manifest":json.dumps(report_manifest(report,scope,patient),indent=2,allow_nan=False),"generated_at":report.generated_at}
            st.session_state["_briefing_pdf"] = stored
        if stored:
            validate_revision(report,settings)
            st.success(f"Report ready · {source} · v{report.data_version} · {report.analysis_date or 'No Data'} · Generated {stored['generated_at']}")
            st.download_button("Download PDF report",stored["pdf"],file_name=f"aei-sentinel-{scope}-v{report.data_version}.pdf",mime="application/pdf",on_click="ignore")
            if expert():
                st.download_button("Download aggregate report manifest (JSON)",stored["manifest"],file_name=f"aei-sentinel-{scope}-manifest.json",mime="application/json",on_click="ignore")
        st.caption("Exports identify the exact source revision, date, filters, thresholds and denominators. A dated report does not update after another import. "
                   "Synthetic prototype; not intended for patient-specific treatment decisions.")

    expert_action("briefing")
