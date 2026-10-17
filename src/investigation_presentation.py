"""Deterministic presentation of existing evidence; no statistical calculations."""

import plotly.graph_objects as go
import streamlit as st
from dataclasses import asdict
from src.design import expert
from src.presentation import limitations,period_note

TRANSITIONS = ("S → S", "S → R", "R → S", "R → R")
TRANSITION_CAVEAT = ("Paired first-isolate results do not establish the same bacterial strain, "
    "continuous infection or antibiotic treatment failure.")
GLOBAL_CAVEAT = ("Selected p-values are exploratory and unadjusted. They never create or upgrade "
    "an original global BY alert. The original source-wide classification remains separate.")


def transition_rows(paired):
    if paired.n_complete_pairs is None:
        return []
    return [{"Baseline → Current": label, "Patients": value} for label, value in zip(TRANSITIONS,
        (paired.s_to_s, paired.s_to_r, paired.r_to_s, paired.r_to_r))]


def transition_chart(paired):
    """None represents unavailable evidence, rather than a manufactured zero plot."""
    if not paired.n_complete_pairs:
        return None
    rows = transition_rows(paired)
    fig = go.Figure(go.Bar(x=[r["Baseline → Current"] for r in rows], y=[r["Patients"] for r in rows],
        text=[r["Patients"] for r in rows], textposition="outside", cliponaxis=False,
        marker_color=["#43859d", "#b85b37", "#43859d", "#43859d"],
        hovertemplate="Baseline → current: %{x}<br>Paired patients: %{y}<extra></extra>"))
    fig.update_layout(height=280, margin=dict(l=15,r=15,t=24,b=30), showlegend=False,
        xaxis_title="Baseline → current S/R", yaxis_title="Paired patients (count)")
    fig.update_yaxes(rangemode="tozero", dtick=1 if paired.n_complete_pairs < 10 else None)
    return fig


def comparison_rows(report):
    full, restricted, paired = report.full_population, report.sensitivity, report.paired
    return [dict(population="Full population", delta_pp=full.difference_pp,
                 denominator=f"Current N={full.current.n_tested}; baseline N={full.baseline.n_tested}",
                 p_value=full.p_value, test="Fisher (unadjusted)", status=full.status),
            dict(population="Restricted population", delta_pp=restricted.difference_pp,
                 denominator=f"Current N={restricted.current.n_tested}; baseline N={restricted.baseline.n_tested}",
                 p_value=restricted.p_value, test="Fisher (unadjusted)", status=restricted.status),
            dict(population="Paired returning patients", delta_pp=None,
                 denominator=(f"Complete pairs={paired.n_complete_pairs}; discordant={paired.discordant_pairs}"
                              if paired.n_complete_pairs is not None else "Unambiguous pairs unavailable"),
                 p_value=paired.p_value, test="Exact McNemar (unadjusted)", status=paired.status)]


def summary_data(advanced, patient=None):
    """Use verified, already computed objects. Never classify an original BY alert."""
    a = advanced.assessment
    return dict(current_pct=advanced.current.percentage, baseline_pct=advanced.baseline.percentage,
        delta_pp=a.difference_pp, current_n=advanced.current.n_observed, baseline_n=advanced.baseline.n_observed,
        preliminary_signal=a.preliminary_signal, status=a.status, limitations=list(a.limitations),
        shared_observed=patient.membership.shared_observed if patient else a.shared_patients,
        shared_tested=patient.membership.shared_tested if patient else None,
        paired_available=bool(patient and patient.paired.n_complete_pairs),
        sensitivity_available=bool(patient and patient.sensitivity.p_value is not None),
        conclusion=("No supported statistical conclusion is available for the full selected comparison."
                    if a.p_value is None else "A selected exploratory test is available; consult the original scan for global BY classification."))


def render_summary(advanced, patient, ward_label):
    data = summary_data(advanced, patient)
    st.subheader("Investigation Summary")
    c = advanced.cohort
    st.caption(f"{ward_label} / {c.organism or 'All organisms'} / {advanced.code} / {c.specimen or 'All specimens'} · "
        f"Analysis date: {advanced.analysis_date or 'No Data'} · Version: {advanced.data_version}")
    extra={key:value for key,value in asdict(c).items() if key not in ('ward_code','organism','specimen') and value is not None}
    st.caption((f"Source: {patient.source} · " if patient else "") + "Additional patient filters: " +
        ('; '.join(f"{key.replace('_',' ')}: {value}" for key,value in extra.items()) or "None"))
    def rate(value):
        return f"{value:.2f}%" if value is not None else "Unavailable"
    cols = st.columns(3)
    cols[0].metric("Current resistance", rate(data['current_pct']))
    cols[0].caption(f"{'Tested first isolates' if expert() else 'Tested observations'}: {data['current_n']}")
    cols[1].metric("Historical resistance", rate(data['baseline_pct']))
    cols[1].caption(f"{'Tested first isolates' if expert() else 'Tested observations'}: {data['baseline_n']}")
    cols[2].metric("Observed change", f"{data['delta_pp']:+.2f} pp" if data['delta_pp'] is not None else "Unavailable")
    cols[2].caption("Current minus preceding non-overlapping baseline")
    if not expert():
        st.write('A change was observed and may deserve further investigation. This is a preliminary finding, not a supported statistical alert.'
            if data['preliminary_signal'] else 'No preliminary finding meets the current rules for this selected group. This does not mean resistance is absent.')
        st.info('This comparison cannot support a statistical conclusion with the available data. Review the limitations below.'
            if advanced.assessment.p_value is None else
            'The selected comparison is exploratory. An original supported alert must be checked in Overview; changing patient filters does not create a new alert.')
        for period in (advanced.current,advanced.baseline):
            note=period_note(period)
            if note:
                st.warning(note)
        limitations(data['limitations'])
        st.caption('Resistance is the share of tested observations reported resistant. Missing tests are not counted as susceptible. '
            'The historical baseline ends before the current 28-day period. Observed changes do not establish their cause or recommend treatment.')
        return
    st.write(f"**Preliminary Signal: {'Yes — descriptive only' if data['preliminary_signal'] else 'No'}** · "
        f"Shared observed patients: {data['shared_observed']}" +
        (f"; usable S/R in both periods: {data['shared_tested']}." if patient else "."))
    st.info(data['conclusion'])
    if data['limitations']:
        st.caption("Interpretation limited: " + " ".join(data['limitations']))
    if patient:
        st.caption(f"Paired evidence: {'available' if data['paired_available'] else 'unavailable'} · "
            f"Restricted sensitivity test: {'available' if data['sensitivity_available'] else 'withheld'}. "
            "Open Patient-Aware Analysis for transitions and distinct denominators, or Evidence Quality / Reports for PDF export.")
    with st.expander("How to read this investigation"):
        for text in (
            "Resistance percentage is R / (S + R) among tested first isolates. Missing tests are excluded, never assumed susceptible.",
            "A first isolate is the earliest eligible patient / organism / specimen observation in each reporting period, after cohort filters. Later tested observations do not replace it.",
            f"At least {advanced.current.minimum_sample_size} observations per period are required to show a percentage. Counts below that remain visible; unavailable is not zero.",
            "Overlap means some patients occur in both periods. The ordinary independent-sample Fisher test is withheld for known dependence.",
            "Exact McNemar asks whether S → R and R → S discordances are balanced among returning patients. It does not test change in the whole population.",
            "Restricted sensitivity excludes every patient observed in both periods, including untested patients. This changes the population and can introduce selection bias.",
            "A Preliminary Signal flags volume and observed change for review. A Statistically Supported Alert additionally requires the established full-source Fisher and global BY criteria.",
            GLOBAL_CAVEAT, TRANSITION_CAVEAT):
            st.write(text)


def render_comparison(report):
    st.markdown("#### Compare the evidence")
    for col, row in zip(st.columns(3), comparison_rows(report)):
        with col:
            st.markdown(f"**{row['population']}**")
            if row['population'] != "Paired returning patients":
                st.write(f"{row['delta_pp']:+.2f} pp" if row['delta_pp'] is not None else "Change unavailable")
            st.caption(row['denominator'])
            st.caption(f"{row['test']}: p={row['p_value']:.6g}" if row['p_value'] is not None else "Test withheld / unavailable")
    st.caption("Different populations and statistical questions; do not combine their denominators or p-values. " + GLOBAL_CAVEAT)
