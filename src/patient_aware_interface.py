"""Aggregate-only presentation of three distinct estimands and unchanged alerts."""

import json

import pandas as pd
import streamlit as st

from src.analysis_service import DatasetChangedError
from src.patient_aware_analysis import validate_patient_revision
from src.investigation_presentation import render_comparison, transition_chart, transition_rows, TRANSITION_CAVEAT

STATUS = {"no_data":"No Data","insufficient_data":"Insufficient Data","analysis_limited":"Analysis Limited",
    "eligible_exploratory":"Eligible selected exploratory test"}


def validate_matches_advanced(report,advanced):
    if (report.cohort,report.antibiotic_code,report.analysis_date,report.data_version)!=(advanced.cohort,advanced.code,advanced.analysis_date,advanced.data_version):
        raise DatasetChangedError("Patient-aware and full-population contexts differ. Refresh data.")
    for name in ("current","baseline"):
        left,right = getattr(report.full_population,name),getattr(advanced,name)
        if (left.start_date,left.end_date,left.n_first_isolates,left.n_susceptible,left.n_resistant,left.n_tested,left.resistance_pct)!=(
            right.start_date,right.end_date,right.n_first_isolates,right.n_negative,right.n_positive,right.n_observed,right.percentage):
            raise DatasetChangedError("Patient-aware and full-population evidence differ. Refresh data.")


def _period_row(label,result,ci=None):
    return {"Population / period":label,"Resistance (%)":result.resistance_pct,
        "Status":"Meets Sample Threshold" if result.status=="ok" else STATUS[result.status],
        "S":result.n_susceptible,"R":result.n_resistant,"N":result.n_tested,
        "Eligible first":result.n_first_isolates,"Missing S/R":result.n_first_isolates-result.n_tested,
        "Start":result.start_date,"End":result.end_date,
        "Wilson lower (%)":ci.lower_pct if ci else None,"Wilson upper (%)":ci.upper_pct if ci else None}


def render_patient_aware(report,settings):
    st.subheader("Patient-Aware Investigation")
    st.caption("Additional Exploratory Evidence · Selected antibiotic S/R · Three distinct populations")
    st.caption(f"Source: {report.source} · Version: {report.data_version} · Analysis date: {report.analysis_date or 'No Data'} · "
               f"{report.cohort.ward_code or 'All wards'} / {report.cohort.organism or 'All organisms'} / {report.antibiotic_code} / {report.cohort.specimen or 'All specimens'}")
    membership,paired,sensitivity = report.membership,report.paired,report.sensitivity
    render_comparison(report)
    with st.expander("Patient membership and full-population counts"):
        st.info(f"{membership.shared_tested} patients had usable S/R in both periods; {membership.shared_observed} were observed in both periods, including missing tests. "
                "The paired analysis evaluates returning patients. A separate sensitivity analysis compares patients not shared between periods. "
                "These analyses target different patient groups. When no patients are shared, the sensitivity comparison equals the full comparison.")
        cards = st.columns(3)
        cards[0].metric("Observed only in baseline",membership.baseline_only)
        cards[1].metric("Observed only in current",membership.current_only)
        cards[2].metric("Observed in both periods",membership.shared_observed)
        st.caption("Observed = an eligible first isolate after the selected cohort filters, whether tested or untested. "
                   "Patients lacking usable S/R: baseline " + str(membership.baseline_without_usable_sr) + ", current " + str(membership.current_without_usable_sr) +
                   f"; neither period has usable S/R for {membership.without_usable_sr_either_period} patients. Missing is never treated as S.")
        rows = []
        for category in report.categories:
            rows.append({"Patient membership":category.category,"Patients":category.n_patients,
                "Baseline S":category.baseline.n_susceptible,"Baseline R":category.baseline.n_resistant,"Baseline N":category.baseline.n_tested,
                "Baseline missing":category.baseline.n_first_isolates-category.baseline.n_tested,
                "Current S":category.current.n_susceptible,"Current R":category.current.n_resistant,"Current N":category.current.n_tested,
                "Current missing":category.current.n_first_isolates-category.current.n_tested})
        st.dataframe(pd.DataFrame(rows),hide_index=True,width="stretch")
        st.caption("S/R/N counts refer to selected first-isolate observations; pooled species/specimens can contain more than one observation per patient. Patient membership counts are distinct patients.")
        st.markdown("#### 1. Full-population descriptive change")
        original = report.full_population
        full_rows = [_period_row("Full / current 28 days",original.current,original.current_ci),
                     _period_row("Full / preceding 365 days",original.baseline,original.baseline_ci)]
        st.dataframe(pd.DataFrame(full_rows),hide_index=True,width="stretch")
        change = f"{original.difference_pp:+.2f} pp" if original.difference_pp is not None else "Unavailable"
        st.write(f"Original full-population change: **{change}**. Shared tested patients: {original.shared_patients}. "
                 "The original selected comparison above remains unchanged.")
    st.markdown("#### 2. Paired returning-patient analysis")
    st.write(paired.explanation)
    if paired.n_complete_pairs is not None:
        st.caption(f"Complete paired patients: {paired.n_complete_pairs}; discordant pairs: {paired.discordant_pairs}. "
                   f"Shared patients missing usable S/R: {paired.shared_without_complete_sr}.")
        chart = transition_chart(paired)
        if chart is not None:
            st.plotly_chart(chart,width="stretch",key="patient_transitions")
        else:
            st.info("No valid paired observations. No transition chart or paired conclusion is available.")
        st.dataframe(pd.DataFrame(transition_rows(paired)),hide_index=True,width="stretch")
    else:
        st.info("No unambiguous paired observations. Transition counts are unavailable, not zero.")
        st.caption(f"Ambiguous shared patients: {paired.ambiguous_shared_patients}. The paired table is unavailable, not all zeros.")
    st.caption(TRANSITION_CAVEAT)
    if paired.p_value is not None:
        st.write(f"**Exact McNemar p (unadjusted): {paired.p_value:.6g}**")
        st.caption("Uses only S → R and R → S. Small discordant counts yield discrete, often low-powered evidence. "
                   "No continuity correction or asymptotic chi-square approximation is used. No paired resistance percentage or difference CI is asserted.")
    else:
        st.caption("Paired test unavailable: no complete pairs or no unambiguous binary patient outcomes.")
    for limitation in paired.limitations:
        st.warning(limitation)
    st.markdown("#### 3. Independent-patient sensitivity analysis")
    st.warning(sensitivity.explanation)
    st.caption(f"Removed from both periods: {membership.shared_observed} observed shared patients, including untested patients. "
               f"Status: {STATUS[sensitivity.status]}. Minimum S/R N per period: {report.minimum_sample_size}.")
    sensitivity_rows = [_period_row("Restricted / current 28 days",sensitivity.current,sensitivity.current_ci),
                        _period_row("Restricted / preceding 365 days",sensitivity.baseline,sensitivity.baseline_ci)]
    st.dataframe(pd.DataFrame(sensitivity_rows),hide_index=True,width="stretch")
    delta = f"{sensitivity.difference_pp:+.2f} pp" if sensitivity.difference_pp is not None else "Unavailable"
    st.write(f"Restricted-population change: **{delta}**")
    if sensitivity.p_value is not None:
        st.write(f"**Sensitivity Fisher p (unadjusted): {sensitivity.p_value:.6g}**")
    else:
        st.caption("Sensitivity Fisher test withheld: insufficient observations or remaining within-period patient dependence.")
    for limitation in sensitivity.limitations:
        st.warning(limitation)
    st.caption(f"Available marginal Wilson intervals use {100*report.confidence_level:g}% confidence, require adequate N and one tested observation per patient within that period. "
               "They are not confidence intervals for a difference. Exclusion does not establish absence of other clustering or confounding.")
    st.markdown("#### 4. Existing original statistically supported alert classification")
    st.write("The original full-source BY scan is unchanged. Its classification is shown in the original signal context above when an investigation was opened from a finding. "
             "For an ordinary selected cohort, consult Overview or Reports / Surveillance Briefing for original scan classifications. "
             "Neither test in this section creates or upgrades an original alert, including when an unadjusted p-value is small.")
    with st.expander("Populations, assumptions and export"):
        for assumption in report.assumptions:
            st.write(assumption)
        st.caption("Baseline and current calendar periods do not overlap; the rolling 365-day period is not used here. "
                   "All current Explorer patient filters apply before first-isolate selection and membership determination.")
        validate_patient_revision(report,settings)
        st.download_button("Download patient-aware investigation (JSON)",json.dumps(report.to_dict(),indent=2,allow_nan=False),
            file_name="aei-sentinel-patient-aware.json",mime="application/json",on_click="ignore")
