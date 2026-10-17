"""Presentation of exploratory comparisons; no statistical calculations here."""

import pandas as pd
import streamlit as st

from src.charts import anomaly_comparison_chart
from src.chart_states import render_chart
from src.reporting import export_anomalies_csv, period_label, rate_label, result_table

ANOMALY_LABELS = {
    "no_data": "No Data", "insufficient_data": "Insufficient Data",
    "no_significant_change": "Warning Criteria Not Met", "potential_increase": "Potential Increase — Review",
    "potential_decrease": "Potential Decrease — Review", "analysis_limited": "Analysis Limited",
}


def render_assessment(result, *, detailed=False):
    st.subheader("Resistance Change Assessment")
    st.caption("Selected filters · Exploratory comparison · Fixed non-overlapping windows anchored to Analysis Date")
    with st.container(border=True):
        st.markdown(f"**{ANOMALY_LABELS[result.status]}**")
        if result.status.startswith("potential_"):
            st.warning("Potential deviation for review. This is not an established clinical danger.")
        st.write(result.explanation)
        for limitation in result.limitations:
            st.warning(limitation)
        left, right = st.columns([3, 2])
        with left:
            render_chart((result.baseline,result.current),lambda:anomaly_comparison_chart(result),key="anomaly_comparison",frequency="comparison")
        with right:
            difference = f"{result.difference_pp:+.2f} pp" if result.difference_pp is not None else "Unavailable"
            st.metric("Change from Historical Baseline", difference)
            st.caption(f"Direction: {result.direction.title()}")
            for name, period, ci in [("Current", result.current, result.current_ci),
                                      ("Baseline", result.baseline, result.baseline_ci)]:
                st.write(f"**{name}: {rate_label(period)}**")
                st.caption(f"{period_label(period)} · S={period.n_susceptible}, R={period.n_resistant}, tested={period.n_tested}")
                if ci:
                    st.caption(f"{100 * ci.confidence_level:g}% Wilson CI: {ci.lower_pct:.2f}%–{ci.upper_pct:.2f}%")
            st.caption(f"Shared tested patients: {result.shared_patients}")
        if result.p_value is not None:
            st.caption(f"{result.test_method} · Unadjusted p={result.p_value:.4g}. "
                       "Selected comparisons are exploratory and not corrected for repeated filter exploration.")
        else:
            st.caption("Fisher test not performed: sample size or patient independence requirements are not met.")
        st.caption(f"Prototype review thresholds: tested >= {result.current.minimum_sample_size} in each period; "
                   f"absolute change >= {result.minimum_change_pp:g} pp; p < {result.alpha:g}. "
                   "Wilson intervals describe each proportion, not a confidence interval for the difference. "
                   "A p-value is not the probability that a hypothesis is true.")
        if detailed:
            render = [("Current 28 Days", result.current), ("Historical Baseline", result.baseline)]
            st.dataframe(result_table(render), hide_index=True, width="stretch",
                          column_config={"Resistance (%)": st.column_config.NumberColumn(format="%.2f")})
            st.caption("These fixed surveillance windows are independent of the custom descriptive period above. "
                       "The historical baseline is not the overlapping annual Dashboard window.")
            st.download_button("Download anomaly assessment (CSV)", export_anomalies_csv([result]),
                               file_name=f"aei-sentinel-assessment-{result.analysis_date}.csv", mime="text/csv",
                               key="anomaly_export", on_click="ignore")


def render_surveillance(scan, catalog, settings=None):
    st.subheader("Surveillance Alerts")
    st.caption("Automatic scan of observed ward / organism / antibiotic / specimen combinations · All wards")
    if scan.results:
        reference = scan.results[0]
        st.caption(f"Current: {period_label(reference.current)} · Baseline: {period_label(reference.baseline)}")
    columns = st.columns(3)
    columns[0].metric("Potential Deviations", len(scan.alerts))
    columns[1].metric("Eligible Comparisons", scan.tested_comparisons)
    columns[2].metric("Observed Combinations", len(scan.results))
    counts = scan.quality_counts
    st.caption(f"Analysis Limited: {counts['analysis_limited']} · Insufficient Data: {counts['insufficient_data']} · "
               f"No Data: {counts['no_data']} · Warning Criteria Not Met: {counts['no_significant_change']}")
    st.caption(f"{scan.correction_method} across all {scan.tested_comparisons} eligible tests, "
               "including those below the effect-size threshold. Ineligible combinations are excluded from ranking.")
    if not scan.alerts:
        st.info("No potential deviations met the scan criteria. This is not evidence of no change; "
                "check sample-size and independence limitations above.")
    else:
        rows = [{"Ward": catalog.wards.get(r.filters.ward_code, r.filters.ward_code), "Organism": r.filters.organism,
                 "Antibiotic": catalog.antibiotics.get(r.filters.antibiotic_code, r.filters.antibiotic_code),
                 "Specimen": r.filters.specimen, "Current (%)": r.current.resistance_pct,
                 "Baseline (%)": r.baseline.resistance_pct, "Current Tested": r.current.n_tested,
                 "Baseline Tested": r.baseline.n_tested, "Change (pp)": r.difference_pp,
                 "Direction": r.direction.title(), "Adjusted p (BY)": r.adjusted_p_value,
                 "Status": ANOMALY_LABELS[r.status]} for r in scan.alerts]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            **{name: st.column_config.NumberColumn(format="%.2f") for name in ("Current (%)", "Baseline (%)", "Change (pp)")},
            "Adjusted p (BY)": st.column_config.NumberColumn(format="%.3g")})
        if settings is not None:
            from src.investigations import investigation_button
            investigation_button(scan.alerts, "BY-alert", settings)
        for result in scan.alerts[:5]:
            with st.expander(f"{result.filters.ward_code} · {result.filters.organism} · "
                             f"{result.filters.antibiotic_code} · {result.difference_pp:+.2f} pp"):
                st.write(result.explanation)
    with st.expander("Scan scope, data quality and export"):
        st.write("Current: last 28 days. Baseline: the preceding 365 days, ending before the current window. "
                 "Repeated patients across periods limit Fisher testing; no rows are discarded to force independence. "
                 "Correlated antibiotic tests motivate the conservative BY correction. Unmeasured confounding "
                 "and other patient dependencies remain possible. Signals require further study.")
        if scan.results:
            st.download_button("Download surveillance scan (CSV)", export_anomalies_csv(scan.results),
                               file_name=f"aei-sentinel-scan-{scan.analysis_date}.csv", mime="text/csv",
                               key="scan_export", on_click="ignore")
