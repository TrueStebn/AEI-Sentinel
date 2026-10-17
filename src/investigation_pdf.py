"""Optional aggregate patient evidence shared by Explorer and briefing PDFs."""

import hashlib
import io
import json
from datetime import datetime, timezone
from functools import partial

import streamlit as st
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, KeepTogether

from src.investigation_presentation import transition_rows, TRANSITION_CAVEAT, GLOBAL_CAVEAT
from src.patient_aware_analysis import validate_patient_revision
from src.patient_aware_interface import validate_matches_advanced


def patient_story(report, p, table, heading, small):
    """All values come directly from the immutable Phase 11 report."""
    m, paired, sensitivity = report.membership, report.paired, report.sensitivity
    story = [p("Patient-aware investigation | additional exploratory evidence", heading),
        p(f"Source: {report.source} | Version: {report.data_version} | Analysis date: {report.analysis_date or 'No Data'}", small),
        table([["Observed patients", "Baseline only", "Current only", "Shared", "Shared tested"],
               [f"Baseline {m.observed_baseline}; current {m.observed_current}",m.baseline_only,m.current_only,m.shared_observed,m.shared_tested]], [177,84,84,84,84]),
        p("Patient counts are distinct people with eligible first isolates, including untested observations. "
          "S/R/N below describes tested first-isolate observations; pooled contexts can contain multiple observations per person.", small)]
    rows = [["Population / period", "S", "R", "N", "Resistance", "Change (pp) / raw p"]]
    for label, result in (("Full",report.full_population),("Restricted",sensitivity)):
        for period in ("current","baseline"):
            r = getattr(result,period)
            evidence = (f"{result.difference_pp:+.2f} pp" if result.difference_pp is not None else "Change unavailable") if period=="current" else (
                f"Fisher p={result.p_value:.6g}" if result.p_value is not None else "Fisher withheld")
            rows.append([f"{label} / {period}",r.n_susceptible,r.n_resistant,r.n_tested,
                f"{r.resistance_pct:.2f}%" if r.resistance_pct is not None else "Unavailable",evidence])
    story += [table(rows,[134,31,31,39,100,178]),
        p(f"Restricted analysis removes all {m.shared_observed} observed shared patients from both periods. "
          "It changes the population and can introduce selection bias. " + sensitivity.explanation, small),
        p(f"Paired returning patients: {paired.n_complete_pairs if paired.n_complete_pairs is not None else 'unavailable'}; "
          f"discordant pairs: {paired.discordant_pairs if paired.discordant_pairs is not None else 'unavailable'}; "
          f"shared without complete S/R: {paired.shared_without_complete_sr}.")]
    rows = transition_rows(paired)
    if paired.n_complete_pairs:
        chart = Drawing(510,85)
        largest = max(row['Patients'] for row in rows) or 1
        for index,row in enumerate(rows):
            x=12+index*126
            height=40*row['Patients']/largest
            from src.briefing_pdf import BLUE, NAVY
            chart.add(Rect(x,23,70,height,fillColor=BLUE,strokeColor=None))
            chart.add(String(x,68,str(row['Patients']),fontName="AEI-Bold",fontSize=9,fillColor=NAVY))
            chart.add(String(x,8,row['Baseline → Current'].replace('→','to'),fontName="AEI",fontSize=9,fillColor=NAVY))
        story.append(KeepTogether([chart,table([["Baseline to current",*[r['Baseline → Current'].replace('→','to') for r in rows]],
            ["Paired patients",*[r['Patients'] for r in rows]]],[177,84,84,84,84])]))
    else:
        story.append(p("No valid paired observations. Transition diagram and paired conclusions are unavailable."))
    story.append(p(f"Exact McNemar p (unadjusted): {paired.p_value:.6g}. Uses only S-to-R and R-to-S discordances."
        if paired.p_value is not None else "Exact McNemar test unavailable: no complete unambiguous pairs."))
    story += [p(paired.explanation,small),p(" ".join(paired.limitations),small),
        p(TRANSITION_CAVEAT,small),p(GLOBAL_CAVEAT,small),
        p(f"Minimum S/R N per full/restricted period: {report.minimum_sample_size}. Missing tests are never treated as S. "
          "Small paired samples have discrete, often low-powered evidence. No paired percentage or difference interval is asserted.",small)]
    return story


def investigation_manifest(advanced, patient, *, generated_at=None, signal_threshold_pp=None):
    validate_matches_advanced(patient,advanced)
    return dict(algorithm="investigation-presentation-v1", generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
        source=patient.source, source_identity=patient.source_identity, data_version=patient.data_version,
        analysis_date=str(patient.analysis_date) if patient.analysis_date else None,
        signal_threshold_pp=signal_threshold_pp,
        selected_analysis=advanced.to_dict(),patient_aware=patient.to_dict(),
        global_classification="Not recalculated or assigned by this selected investigation; consult the original source-wide scan.")


def render_investigation_pdf(advanced,patient,settings,*,generated_at=None,signal_threshold_pp=None):
    from src.briefing_pdf import _fonts, _text, NAVY, BLUE, PALE
    validate_patient_revision(patient,settings)
    threshold=settings.signal_minimum_change_pp if signal_threshold_pp is None else signal_threshold_pp
    payload=investigation_manifest(advanced,patient,generated_at=generated_at,signal_threshold_pp=threshold)
    _fonts()
    body=ParagraphStyle("InvestigationBody",fontName="AEI",fontSize=9,leading=12,spaceAfter=5,textColor=NAVY)
    small=ParagraphStyle("InvestigationSmall",parent=body,fontSize=7.5,leading=10,spaceAfter=3)
    heading=ParagraphStyle("InvestigationHeading",parent=body,fontName="AEI-Bold",fontSize=13,leading=17,spaceBefore=8,spaceAfter=8,keepWithNext=True)
    def p(text,style=body):
        return Paragraph(_text(text),style)
    def table(rows,widths):
        grid=Table([[p(value,small) for value in row] for row in rows],colWidths=widths,repeatRows=1,hAlign="LEFT")
        grid.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),PALE),("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LINEBELOW",(0,0),(-1,0),1,BLUE),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
        return grid
    c=advanced.cohort
    extra={key:value for key,value in patient.to_dict()['cohort'].items()
           if key not in ('ward_code','organism','specimen') and value is not None}
    story=[p("AEI SENTINEL | INVESTIGATION REPORT",heading),p("Synthetic Demo Data | Aggregate results only | Not for patient-specific treatment decisions",small),
        p(f"{c.ward_code or 'All wards'} / {c.organism or 'All organisms'} / {advanced.code} / {c.specimen or 'All specimens'}"),
        p("Additional patient filters: "+('; '.join(f"{key.replace('_',' ')}: {value}" for key,value in extra.items()) or "None"),small),
        p(f"Generated (UTC): {payload['generated_at']}",small),
        p(f"Current: {advanced.current.start_date} to {advanced.current.end_date}; baseline: {advanced.baseline.start_date} to {advanced.baseline.end_date}.",small),
        p(f"Preliminary Signal: {'Yes, descriptive only' if advanced.assessment.preliminary_signal else 'No'}; descriptive threshold > {threshold:g} pp. "+advanced.assessment.explanation,small)]
    if patient.source!="Laboratory Dataset":
        story.append(p("REPLAY / ARTIFICIAL DEMONSTRATION SOURCE. This isolated database is not a live hospital feed.",heading))
    from src.chart_states import availability, explanation
    weekly_state=availability(advanced.weekly,supported=advanced.supported,minimum=advanced.current.minimum_sample_size)
    if weekly_state.status != 'available':
        title,message=explanation(weekly_state,'weekly',aggregate=advanced.current)
        if weekly_state.status in ('no_data','insufficient_data'):
            message=(f'{weekly_state.valid}/{weekly_state.total} reportable periods; minimum N={weekly_state.minimum}. '
                f'Insufficient Data: {weekly_state.insufficient}; No Data: {weekly_state.empty}.')
            if advanced.current.percentage is not None:
                message+=f' Overall 28-day result remains available: {advanced.current.percentage:.2f}%.'
        story.append(p(title+'. '+message,small))
    story += patient_story(patient,p,table,heading,small)
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    story += [p("Method and reproducibility",heading),
        p("Filters precede first-isolate selection by patient / organism / specimen / period. AST lookup follows selection. "
          "Current 28 days and preceding 365 days are non-overlapping and independently deduplicated. Rates require adequate N. "
          "The paired and restricted analyses address different questions; neither changes the original global BY classification.",small),
        p("Pairs are assumed independent across patients; other clustering and confounding cannot be excluded. "
          "Repeated filter exploration is not multiplicity-adjusted. Results do not establish an outbreak, clinical infection or causality.",small),
        p(f"Algorithms: {payload['algorithm']} / {patient.algorithm}. Source identity (hashed path): {patient.source_identity}. "
          f"Aggregate manifest SHA-256: {digest}. Source revision is checked before and after rendering.",small)]
    output=io.BytesIO()
    def page(canvas,doc):
        canvas.setFont("AEI",7)
        canvas.drawString(41,24,f"{patient.source} | v{patient.data_version} | {patient.analysis_date or 'No Data'}")
        canvas.drawRightString(A4[0]-41,24,f"Page {doc.page}")
    doc=SimpleDocTemplate(output,pagesize=A4,leftMargin=41,rightMargin=41,topMargin=36,bottomMargin=40,invariant=1,
        title="AEI Sentinel - Patient-aware investigation",author="AEI Sentinel")
    doc.build(story,onFirstPage=page,onLaterPages=page,canvasmaker=partial(Canvas,invariant=1))
    validate_patient_revision(patient,settings)
    return output.getvalue(),payload


def render_investigation_export(advanced,patient,settings):
    from src.design import expert
    st.subheader("Investigation PDF export")
    st.caption("Includes the selected full, paired and restricted evidence. Original global BY classification is separate." if expert() else
        'Download the selected investigation with its results, patient filters and interpretation limits. The report does not create a new alert.')
    threshold=st.session_state.get('signal_threshold',settings.signal_minimum_change_pp)
    signature=hashlib.sha256(json.dumps(investigation_manifest(advanced,patient,generated_at="signature",signal_threshold_pp=threshold),sort_keys=True,allow_nan=False).encode()).hexdigest()
    stored=st.session_state.get("_investigation_pdf")
    if stored and stored['signature']!=signature:
        st.session_state.pop("_investigation_pdf",None)
        stored=None
    from src.presentation import cohort_caption
    c=advanced.cohort
    st.info(f'**Selected PDF scope: Current Investigation**\n\n{patient.source} · v{patient.data_version} · {patient.analysis_date or "No Data"} · {c.ward_code or "All wards"} / {c.organism or "All organisms"} / {advanced.code} / {c.specimen or "All specimens"}.')
    cohort_caption(c)
    if st.button("Generate investigation PDF",type="primary"):
        pdf,payload=render_investigation_pdf(advanced,patient,settings,signal_threshold_pp=threshold)
        stored=dict(signature=signature,pdf=pdf,manifest=json.dumps(payload,indent=2,allow_nan=False))
        st.session_state['_investigation_pdf']=stored
    if stored:
        validate_patient_revision(patient,settings)
        st.download_button("Download investigation PDF",stored['pdf'],file_name=f"aei-sentinel-investigation-v{patient.data_version}.pdf",mime="application/pdf",on_click="ignore")
        if expert():
            st.download_button("Download investigation PDF manifest (JSON)",stored['manifest'],file_name="aei-sentinel-investigation-manifest.json",mime="application/json",on_click="ignore")
