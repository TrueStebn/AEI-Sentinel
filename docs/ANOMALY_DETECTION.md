# Statistical anomaly detection

## API and unchanged source data

`AnomalyEngine` is independent of Streamlit. It uses the existing read-only SQLite connection factory without initializing a database, importing files or writing records/caches. Patient IDs exist only transiently for independence checks; results and CSV contain counts, never individual identifiers.

```python
from src.anomalies import AnomalyEngine
from src.resistance import ResistanceFilters

engine = AnomalyEngine()
assessment = engine.analyze(
    ResistanceFilters("TMP", "URO", "Escherichia coli", "urine"), as_of_date="2026-09-27",
)
scan = engine.scan(as_of_date="2026-09-27")
print(assessment.to_dict())
print(scan.quality_counts)
```

Omitted dates use the global latest culture sample date. Empty databases return `no_data` without invented dates; missing databases are not created. Dates accept exact ISO strings or Python dates.

## Windows and first isolates

For reference date `d`, Current is `[d - 27, d]` (28 days) and Baseline is `[d - 392, d - 28]` (365 days), both inclusive. For **2026-09-27**: Current **2026-08-31–2026-09-27**, Baseline **2025-08-31–2026-08-30**. They never share dates, including around leap years.

The Resistance Trends annual metric remains its overlapping last 365 days and is not used as this baseline. Detailed Period's arbitrary descriptive range also remains separate from the fixed anomaly windows anchored to Analysis Date.

The original ranking SQL is factored into `cohort_queries.py`, shared with ResistanceEngine. The unchanged rule selects the first isolate per `(patient_id, organism, specimen)` and period, ordered by `(sample_date, culture_id, isolate_no, isolate_id)`. Date/ward/organism/specimen filters precede ranking; antibiotic results join afterwards. Untested first isolates are never replaced by later tests. The scan ranks additionally within each exact ward, equivalent to evaluating that ward separately. All-ward selected comparisons retain the existing cross-ward deduplication policy.

Current and Baseline deduplicate independently. Resistance rates and sample statuses reuse ResistanceEngine's result construction: `100 * R / (S + R)`. No second resistance formula or patient-selection rule is implemented in the UI.

## Patient independence and limitations

Non-overlapping dates do not imply independent patients. The engine counts tested patients shared across windows and detects multiple tested observations from one patient within a window when optional organism/specimen filters pool groups.

With sufficient samples but either detected dependence, status is `analysis_limited`: Fisher testing and potential-warning classification are withheld, p-values are `None`. Descriptive rates/counts remain unchanged. No patients are dropped to force independence. This conservative policy can suppress all warnings in a dataset with recurring patients.

Cross-period overlap still allows descriptive, marginal Wilson intervals when each patient contributes once within each period. Within-period repeated patients suppress that period's interval. Intervals are also absent below the effective sample threshold. Other clustering, case-mix changes, testing practices, seasonality, incomplete coverage and confounding are not evaluated; absence of observed ID reuse does not establish every test assumption. All results remain exploratory.

## Fisher test, intervals and thresholds

For sufficient samples without known patient reuse, SciPy's [two-sided Fisher exact test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.fisher_exact.html) uses `[[R_current, S_current], [R_baseline, S_baseline]]`. The selected-filter p-value is unadjusted and labeled exploratory. It is conditional on table margins under equal odds, not the probability a hypothesis is true. Repeated manual filter exploration is not corrected across the user's history.

[Wilson intervals](https://docs.scipy.org/doc/scipy-1.14.1/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html) use `binomtest(R,N).proportion_ci(method="wilson")`, default 95%. They describe each proportion; they are neither a confidence interval for the difference nor simultaneous family-wide intervals. Observed 0% and 100% retain nonzero uncertainty.

Difference is Current percentage minus Baseline percentage, in percentage points. Direction is increase/decrease/unchanged/unavailable. Potential warnings require sufficient samples, no detected dependence, absolute difference at least the effect threshold and the applicable p-value strictly below alpha. An absolute tolerance of `1e-12` only protects floating-point comparison at the effect boundary.

| Settings field | Environment variable | Default |
| --- | --- | ---: |
| `anomaly_minimum_sample_size` | `AEI_ANOMALY_MINIMUM_SAMPLE_SIZE` | 30 |
| `anomaly_minimum_change_pp` | `AEI_ANOMALY_MINIMUM_CHANGE_PP` | 10 |
| `anomaly_alpha` | `AEI_ANOMALY_ALPHA` | 0.05 |
| `anomaly_confidence_level` | `AEI_ANOMALY_CONFIDENCE_LEVEL` | 0.95 |

The effective sample threshold is the greater of the existing `minimum_sample_size` and the anomaly threshold, each at least 30. Effect size accepts finite values 0–100; alpha/confidence must be finite and strictly between 0 and 1. Invalid settings fail explicitly. These are prototype review parameters, not clinically validated limits.

## Automated scan and multiplicity

Only exact ward/organism/antibiotic/specimen combinations observed in susceptibility data within the two-window span are discovered. No Cartesian product is constructed. Discovery includes tests on later isolates, but these do not replace missing first-isolate tests; such a discovered cohort can legitimately have no eligible result.

Four read statements suffice regardless of combination count: revision, latest date, observed combinations, and one ranked-observation query spanning both periods. Connection pragmas and BEGIN are additional setup statements. Patient sets and IDs are discarded after aggregation; there are no per-combination database queries.

All sufficient comparisons with applicable Fisher p-values enter one correction family, including those with small/zero effects. No outcome-driven filtering precedes correction. No-data, insufficient and analysis-limited cohorts have no p-value and are excluded from statistical ranking but retained in quality summaries and export.

SciPy's [Benjamini–Yekutieli FDR correction](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.false_discovery_control.html), `method="by"`, is used because antibiotic/cohort tests may share observations. It is more conservative than BH and permits arbitrary dependence between valid tests. It cannot repair an invalid individual test; patient checks therefore precede testing. A batch warning requires BY-adjusted p < alpha plus the effect threshold. The family covers one scan/date, not all future scans or manual explorations.

Raw and adjusted values, method and family size are returned. Potential deviations sort first, followed by eligible comparisons ordered by adjusted p and absolute effect, then ineligible quality results, with deterministic cohort tie-breaking.

## Result contract and status precedence

Results contain filters, analysis date, Current/Baseline bounds, S/R/total and first-isolate counts, reportable rates, difference/direction, test method, applicable raw/adjusted p-values, applicable Wilson intervals, shared/distinct patient counts, thresholds, status, explanation, dependence limitations and source revision. `to_dict()` is JSON-safe.

| Status | Meaning |
| --- | --- |
| `no_data` | At least one window has zero eligible S/R results. |
| `insufficient_data` | Both windows have results but one is below the minimum. |
| `analysis_limited` | Samples suffice but detected patient reuse prevents testing. |
| `no_significant_change` | Eligible test did not meet both warning criteria. UI: Warning Criteria Not Met. This does not establish equivalence or absence of change. |
| `potential_increase` | Positive change meets effect and applicable p-value thresholds; review is warranted. |
| `potential_decrease` | Negative change meets those thresholds; review is warranted. |

No-data/insufficient states precede statistical classification; detected reuse is still recorded. Missing rates/p-values are never replaced with zero. A potential signal is not an established clinical danger or prescribing recommendation.

## Original-data results and controlled example

At **2026-09-27**, revision **1**, the scan finds **996** observed combinations: **36 analysis_limited**, **597 insufficient_data**, **363 no_data**. All sufficiently sampled cohorts have cross-period patient overlap: **0 eligible Fisher tests and 0 potential deviations**. Raw/adjusted p-values are absent. This is not evidence of no changes in the source dataset.

URO / E. coli / urine / TMP: Current S=26/R=7/N=33 (**21.21%**), Baseline S=257/R=81/N=338 (**23.96%**), difference **−2.75 pp**. Three tested patients occur in both periods, so status is `analysis_limited` with no Fisher p-value. Marginal Wilson intervals: approximately **10.68–37.75%** and **19.72–28.79%**. The baseline N=338 intentionally differs from the overlapping annual Resistance Trends N=337.

In a **separate temporary synthetic test fixture**, Current R=15/S=15/N=30 and Baseline R=0/S=30/N=30, with distinct patients, yield +50 pp and two-sided Fisher p **5.83213e-06**. A one-test BY scan flags a potential increase. This example is never injected into the original dataset.

## UI and validation

Resistance Trends adds Resistance Change Assessment and Surveillance Alerts while preserving descriptive metrics. Detailed Period adds comparison periods, S/R table, limitations and aggregate-only CSV. Comparison bars show marginal Wilson error bars where applicable; captions expose counts/dates. Selected comparisons are explicitly unadjusted; batch comparisons show adjusted values.

Each engine call uses one read snapshot. UI assembly verifies descriptive results, assessment, scan and catalog share one revision before rendering analytics. There is no analytical warning cache; interaction/Refresh reads committed data and the nonblocking revision watcher refreshes connected idle pages. Preferences are independent and persist through validated token-scoped workspaces; analytical results are recalculated.

Tests cover threshold boundaries, unchanged/increased/decreased outcomes, extreme rates, window bounds, first-isolate rules, patient reuse, Fisher probabilities via independent hypergeometric enumeration, reference Wilson/BY values, full correction families, bounded query count, snapshot commits, deterministic exports and unchanged records. AppTest covers signals, limitations, exports, sessions and refreshed/rejected revisions; all earlier tests remain required.

Production authentication, real hospital integration and clinical validation remain outside this local synthetic prototype. Additional patient-aware estimands are documented separately in [Patient-aware surveillance](PATIENT_AWARE_SURVEILLANCE.md).
