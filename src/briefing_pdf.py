"""Deterministic aggregate PDF rendering with revision validation at export time."""

import io
import hashlib
import json
from functools import partial
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

from src.briefing import QUALITY_LABELS, quality_summary, validate_revision
from src.chart_states import availability, explanation, percentage

NAVY = colors.HexColor("#173354")
BLUE = colors.HexColor("#247997")
PALE = colors.HexColor("#edf4f8")


def _fonts():
    folder = Path(reportlab.__file__).parent / "fonts"
    # Fonts ship with ReportLab; no host-specific font paths or downloads.
    if "AEI" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("AEI", str(folder / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("AEI-Bold", str(folder / "VeraBd.ttf")))
        pdfmetrics.registerFontFamily("AEI", normal="AEI", bold="AEI-Bold", italic="AEI", boldItalic="AEI-Bold")


def _text(value):
    return escape(str(value).replace("–", "-").replace("—", "-").replace("\u2011", "-"))


def report_manifest(report, scope="hospital", patient_aware=None):
    payload = report.to_dict(scope)
    if patient_aware is not None:
        from src.analysis_service import DatasetChangedError
        from src.patient_aware_interface import validate_matches_advanced
        if scope != "selected" or report.selected_analysis is None:
            raise ValueError("Patient-aware evidence requires a selected antibiotic report")
        validate_matches_advanced(patient_aware, report.selected_analysis)
        if (patient_aware.source, patient_aware.source_identity, patient_aware.data_version, patient_aware.analysis_date) != (
                report.source, report.source_identity, report.data_version, report.analysis_date):
            raise DatasetChangedError("Briefing and patient-aware source contexts differ. Refresh data.")
        payload["patient_aware"] = patient_aware.to_dict()
    return payload


def render_pdf(report, settings, *, scope="hospital", patient_aware=None):
    """Same immutable report + timestamp yields identical PDF bytes. No database writes."""
    validate_revision(report, settings)
    payload = report_manifest(report, scope, patient_aware)  # Validate scope before any output.
    if patient_aware is not None:
        from src.patient_aware_analysis import validate_patient_revision
        validate_patient_revision(patient_aware, settings)
    _fonts()
    body = ParagraphStyle("Body", fontName="AEI", fontSize=9, leading=13, textColor=NAVY, spaceAfter=7)
    small = ParagraphStyle("Small", parent=body, fontSize=7.5, leading=10, spaceAfter=4)
    heading = ParagraphStyle("Heading", parent=body, fontName="AEI-Bold", fontSize=14, leading=18, spaceBefore=10, spaceAfter=9, keepWithNext=True)
    title = ParagraphStyle("Title", parent=heading, fontSize=28, leading=34)
    def p(value, style=body):
        return Paragraph(_text(value), style)
    def table(rows, widths):
        grid = Table([[p(value, small) for value in row] for row in rows], colWidths=widths, repeatRows=1, hAlign="LEFT")
        grid.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0),PALE), ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("BOTTOMPADDING",(0,0),(-1,-1),7), ("TOPPADDING",(0,0),(-1,-1),7),
            ("LINEBELOW",(0,0),(-1,0),1,BLUE), ("LINEBELOW",(0,1),(-1,-1),.3,colors.HexColor("#d4dfe8"))]))
        return grid
    results = report.scan.results if scope == "hospital" else report.selected_results
    findings = report.findings if scope == "hospital" else report.selected_findings
    q = quality_summary(results)
    preliminary = sum(item.kind == "preliminary" for item in findings)
    supported = sum(item.kind == "BY-alert" for item in findings)
    scope_label = "Hospital-wide surveillance summary (all source wards, including primary care)" if scope == "hospital" else "Selected ward / organism / specimen investigation"
    story = [p("AEI Sentinel", title), p("SURVEILLANCE BRIEFING", heading), p("Synthetic Demo Data", heading),
        p(scope_label), p(f"SOURCE: {report.source} | Dataset version: {report.data_version}"),
        p(f"Generated (UTC): {report.generated_at} | Analysis date: {report.analysis_date or 'No Data'}", small),
        p(f"Latest laboratory sample: {report.latest_sample or 'No Data'} | Last successful import (UTC): {report.last_successful_import or 'No completed import'}", small)]
    if scope == "selected":
        story.append(p("Scope: " + " / ".join(str(value or "All") for value in (report.filters.ward_code, report.filters.organism, report.filters.specimen))))
    if report.source != "Laboratory Dataset":
        story.append(p("REPLAY / ARTIFICIAL DEMONSTRATION SOURCE. Results describe this isolated replay database only.", heading))
    story += [p("Surveillance overview", heading),
        table([["Observed exact combinations", "Preliminary only findings", "Statistically supported alerts"],
            [q["combinations"], preliminary, supported]], [171,171,171]),
        p(f"Global BY family: {report.scan.tested_comparisons} eligible comparisons. Filtering selects descriptive rows; it never recalculates the correction family. Preliminary-only and supported queue categories are mutually exclusive. The UI total preliminary signal count may also include supported signals.", small),
        p("Important findings requiring review", heading)]
    if not findings:
        story.append(p("No findings satisfy the established preliminary or BY-adjusted alert rules in this scope. This does not prove absence of change."))
    for index, item in enumerate(findings[:20], 1):
        r, f = item.comparison, item.comparison.filters
        def rate(result):
            return f"{result.resistance_pct:.2f}%" if result.resistance_pct is not None else QUALITY_LABELS[result.status]
        story.append(KeepTogether([p(f"{index}. {f.ward_code} / {f.organism} / {f.antibiotic_code} / {f.specimen}", heading),
            p(f"{'Statistically supported (BY)' if item.kind == 'BY-alert' else 'Preliminary (descriptive)'} | Change: {r.difference_pp:+.2f} pp"),
            p(f"Current {r.current.start_date} to {r.current.end_date}: {rate(r.current)}; S={r.current.n_susceptible}, R={r.current.n_resistant}, N={r.current.n_tested}."),
            p(f"Baseline {r.baseline.start_date} to {r.baseline.end_date}: {rate(r.baseline)}; S={r.baseline.n_susceptible}, R={r.baseline.n_resistant}, N={r.baseline.n_tested}."),
            p(f"Shared patients: {r.shared_patients}. Last relevant observation: {item.latest_relevant_sample or 'No Data'}. " + (" ".join(r.limitations) or "No patient dependence detected in this comparison."), small),
            p(f"BY-adjusted p: {r.adjusted_p_value if r.adjusted_p_value is not None else 'withheld'}; original global family: {r.correction_family_size}.", small)]))
    if len(findings) > 20:
        story.append(p(f"Showing the first 20 of {len(findings)} findings using documented Review Priority. The accompanying JSON manifest contains every finding."))
    analysis = report.selected_analysis if scope == "selected" else None
    if analysis:
        story += ([] if patient_aware is not None else [PageBreak()]) + [p(f"Selected resistance evidence | {analysis.code}", heading)]
        rows = [["Period / dates", "Resistance", "S", "R", "N", "First / missing"]]
        for label, item in (("Current 28 days", analysis.current), ("Preceding non-overlapping 365 days", analysis.baseline), ("Rolling 365 days (overlapping)", analysis.annual)):
            rows.append([f"{label}\n{item.start_date} to {item.end_date}", f"{item.percentage:.2f}%" if item.percentage is not None else QUALITY_LABELS.get(item.status, item.status),
                item.n_negative, item.n_positive, item.n_observed, f"{item.n_first_isolates} / {item.missing_tests}"])
        story += [table(rows, [183,100,35,35,45,115]),
            p(f"Latest relevant sample: {analysis.latest_relevant_sample or 'No Data'}. Patient overlap: {analysis.assessment.shared_patients}. " + analysis.assessment.explanation),
            p(" ".join(analysis.assessment.limitations) or "No known dependence detected. Selected Fisher results, when available, are exploratory and unadjusted; they are not global BY alerts.", small)]
        # Empty comparisons become text before allocating a vector chart.
        chart = Drawing(510, 158)
        for index, (label, item) in enumerate((("Current 28d", analysis.current), ("Baseline 365d", analysis.baseline), ("Rolling 365d", analysis.annual))):
            y = 120-index*44
            chart.add(String(0,y+3,label,fontName="AEI",fontSize=9,fillColor=NAVY))
            chart.add(Line(118,y,318,y,strokeColor=colors.lightgrey))
            if item.percentage is not None:
                chart.add(Rect(118,y,2*item.percentage,15,fillColor=BLUE,strokeColor=None))
            label_value = f"{item.percentage:.2f}%" if item.percentage is not None else QUALITY_LABELS.get(item.status,item.status)
            chart.add(String(325,y+3,f"{label_value}; S/R/N {item.n_negative}/{item.n_positive}/{item.n_observed}",fontName="AEI",fontSize=7.5,fillColor=NAVY))
        chart.add(String(118,0,"Fixed chart scale: 0-100%. Rates are not clinical thresholds.",fontName="AEI",fontSize=8,fillColor=NAVY))
        if patient_aware is None:
            state = availability((analysis.current,analysis.baseline,analysis.annual),supported=analysis.supported)
            if state.valid:
                story.append(chart)
            else:
                title, message = explanation(state,'comparison')
                story.append(p(title + '. ' + message))
        else:
            from src.investigation_pdf import patient_story
            story += [PageBreak()] + patient_story(patient_aware,p,table,heading,small)
    story += [PageBreak(), p("Data Quality Center", heading),
        table([["Category", "Comparisons"], *[[label,q[key]] for key,label in QUALITY_LABELS.items()], ["Eligible statistical comparisons",q["eligible"]]], [400,113]),
        p(f"{q['analysis_limited']} comparisons meet the configured sample-size requirement but cannot undergo the current independent-sample Fisher test because of known patient dependence. {q['dependence_any']} comparisons have dependence limitations in any sample-size category."),
        p(f"Missing susceptibility opportunities: current {q['current_missing_opportunities']} of {q['current_first_opportunities']} eligible first-isolate/antibiotic opportunities; baseline {q['baseline_missing_opportunities']} of {q['baseline_first_opportunities']}. Tested opportunities: current {q['current_tested_opportunities']}, baseline {q['baseline_tested_opportunities']}.", small),
        p("These are test opportunities across observed exact scan combinations, not unique patients or totals of independent observations. Missing results include unavailable or unreported tests; the data cannot distinguish the cause. The scan is not an exhaustive Cartesian product of all references. Quality is not a clinical safety certification.", small),
        p("Methodology and interpretation", heading),
        p(f"Cohort and period filters precede first-isolate selection by patient / organism / specimen (separately within ward for the global scan). Susceptibility is looked up after selection. Resistance = R/(S+R) x 100; minimum N={report.thresholds['minimum_sample_size']}. Counts remain visible below threshold; unavailable percentages are never replaced by zero."),
        p(f"Current window: 28 inclusive calendar days. Anomaly baseline: preceding non-overlapping 365 days. Rolling dashboard period: 365 days ending at the analysis date, overlapping the current window. These periods are independently deduplicated and must not be summed."),
        p(f"Preliminary signals require both adequate denominators and absolute change > {report.thresholds['signal_change_pp_strictly_greater']:g} pp. Supported alerts require independent samples, two-sided Fisher testing, global Benjamini-Yekutieli correction, adjusted p < {report.thresholds['alpha']:g}, and absolute change >= {report.thresholds['statistical_change_pp_at_least']:g} pp. Marginal intervals use {100*report.thresholds['confidence_level']:g}% Wilson where supported."),
        p("Review Priority orders supported findings first, then absolute change, then the smaller of the two denominators, then stable cohort labels. It is a descriptive ordering, not a clinical risk score. High observed resistance alone does not create a finding."),
        p("Reproducibility and export integrity", heading),
        p(f"Algorithm: {payload['algorithm']}\nSource identity (hashed path): {report.source_identity}\nAggregate manifest SHA-256: " +
          (hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest() if patient_aware is not None else report.digest(scope)), small),
        p("Source version is checked before and after rendering. This is a dated artifact of its stated revision; it does not automatically update. Reproduction requires that same source revision, filters, thresholds and generation timestamp. Old database revisions are not archived by this prototype. No historical import-to-import metric changes are inferred."),
        p("Synthetic prototype for demonstration only. Not intended for patient-specific treatment decisions. Observed changes do not establish an outbreak, clinical infection, causality or treatment suitability.", heading)]
    output = io.BytesIO()
    def page(canvas, doc):
        canvas.setFillColor(NAVY)
        canvas.setFont("AEI-Bold",8)
        canvas.drawString(40, A4[1]-28, "AEI SENTINEL  /  SYNTHETIC DEMO DATA")
        canvas.setFont("AEI",7)
        canvas.drawString(40,24,f"{report.source} | v{report.data_version} | {report.analysis_date or 'No Data'}")
        canvas.drawRightString(A4[0]-40,24,f"Page {doc.page}")
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=41,rightMargin=41,topMargin=48,bottomMargin=43,invariant=1,
        title="AEI Sentinel - Surveillance Briefing", author="AEI Sentinel", subject="Synthetic Demo Data - aggregate surveillance")
    doc.build(story,onFirstPage=page,onLaterPages=page,canvasmaker=partial(Canvas,invariant=1))
    validate_revision(report, settings)
    if patient_aware is not None:
        validate_patient_revision(patient_aware, settings)
    return output.getvalue()
