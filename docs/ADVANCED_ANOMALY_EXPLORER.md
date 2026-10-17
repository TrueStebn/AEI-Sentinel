# Advanced Anomaly Explorer

Open **Explore → Investigation**. Guided provides Resistance Trends, Patient Groups and Interpretation; Expert adds patient/healthcare filters, Evidence Quality and Patient-Aware Analysis. Analysis type selects antibiotic S/R or phenotype monitoring. Both modes retain the same source, cohort, dates and calculations.

## Filters and cohort definition

All filters are optional except the selected antibiotic or phenotype marker. Controls are populated from the active database where applicable. No patient-level records are displayed or exported.

| Filter | Meaning |
|---|---|
| Sex | All / Female (F) / Male (M), from `patients.sex` |
| Age | Age at sampling from `cultures.age_years`; inclusive 0–17, 18–44, 45–64, 65+, or custom inclusive bounds 0–130 |
| Home Area | All or a value present in `patients.home_area` |
| Ward | All or a ward from the active source |
| Ward type | All / inpatient / outpatient, restricted to available metadata |
| Institution | All / hospital / primary care, restricted to available metadata |
| Organism | All or an observed species; phenotype eligibility still applies |
| Specimen | All or available urine / blood / wound values |
| Analysis date | Anchors every window; defaults to the latest source sample and can be pinned |
| Admission timing | Unrestricted, all valid inpatient admissions, hospital days 1–2, or day 3 onward |

Hospital day is the integer calendar-day difference `sample_date - admission_date + 1`: same-day sampling is day 1, the next day is day 2. Admission must be a canonical valid ISO calendar date no later than sampling, on an inpatient ward. Missing, malformed, impossible or future admissions and outpatient observations are excluded when timing is requested. This is an **admission-timing grouping**, not a classification of proven hospital-acquired infection. Incompatible fixed ward/type/institution/timing selections and reversed age bounds report a validation error.

All cohort and reporting-period filters apply **before** selecting the first isolate. Rank by sample date, culture ID, isolate number, isolate ID; partition by patient, organism, specimen and reporting period. A selected ward restricts the population before ranking. Ward comparisons partition by ward as well, preserving established ward-specific deduplication. Each weekly/monthly bucket and displayed group is independently deduplicated. Patient age can cross a boundary between observations; filtering or grouping by age can therefore choose different eligible first observations. Do not sum independently ranked denominators.

## Two separate measurements

**Antibiotic resistance:** `100 × R / (S + R)` among first isolates with a selected-antibiotic S/R result. The susceptibility join occurs after ranking. Eligible first-isolate count includes untested first isolates; missing tests equal eligible first minus S/R tested. A later tested isolate never substitutes for an untested eligible first isolate. Organism-specific testing and urine-only reporting can produce expected missingness; missing tests are not susceptible results.

**Phenotype:** `100 × flag-positive eligible first isolates / all eligible first isolates`. Read the selected 0/1 flag directly from `isolates` after ranking. Negatives enter the denominator, without requiring or joining antibiotic tests. Multiple flags on one isolate do not multiply the denominator. The separate `PhenotypeEngine` delegates shared cohort selection and reporting to the advanced service. Only its resulting binary counts are adapted to the existing temporal Fisher/Wilson methods; susceptibility results never determine phenotype positivity.

The prototype registry deliberately defines a supported analysis scope:

| Marker | Supported organisms |
|---|---|
| ESBL | Escherichia coli; Klebsiella pneumoniae |
| MRSA | Staphylococcus aureus |
| VRE | Enterococcus faecalis; Enterococcus faecium (not present in this dataset) |
| CPE | Citrobacter freundii; Escherichia coli; Klebsiella oxytoca; Klebsiella pneumoniae; Proteus mirabilis |

With All organisms, only supported species enter the marker denominator. An explicitly unsupported pair receives `unsupported`, no denominator percentage and no signal/test. It must never be interpreted as a negative marker result. A supported species absent from a database receives No Data instead.

The supplied dataset README calibrates E. coli ESBL and S. aureus MRSA, while other patterns are illustrative. Inspection of all source flags found ESBL positives only in E. coli/K. pneumoniae, MRSA in S. aureus, VRE in E. faecalis, and CPE in the five listed Enterobacterales. This informs a conservative prototype registry, not a claim that ESBL or carbapenemase cannot occur outside it. EUCAST's [resistance mechanism detection resources](https://www.eucast.org/bacteria/important-additional-information/resistance-detection/) and [RAST resistance detection scope](https://www.eucast.org/bacteria/rapid-ast-in-blood-culture/resistance-detection-in-rast/) provide context for mechanism and organism-specific interpretation. The [ECDC EARS-Net network](https://www.ecdc.europa.eu/en/about-us/networks/disease-networks-and-laboratory-networks/ears-net-data) provides surveillance context for the selected species and resistance indicators. These are contextual references; this synthetic all-specimen prototype is not an implementation of EARS-Net clinical surveillance or EUCAST confirmatory testing.

Both modes retain actual counts but suppress percentages and intervals below `max(minimum_sample_size, anomaly_minimum_sample_size)`, currently 30. Zero observations yield No Data; 1–29 yield Insufficient Data. A supported 0/30 is a real 0%, unlike 0/13 or an unsupported pair. Unknown/missing percentages remain null, and charts do not connect gaps.

## Windows and statistical interpretation

For analysis date D (inclusive):

| Period | Inclusive dates | Purpose |
|---|---|---|
| Current | D−27 through D | 28-day subgroup result |
| Historical baseline | D−392 through D−28 | Preceding 365 days, excludes current |
| Rolling annual | D−364 through D | Existing descriptive 365-day result, overlaps current |

On 2026-09-27 these are 2026-08-31–2026-09-27, 2025-08-31–2026-08-30, and 2025-09-28–2026-09-27. Weekly and monthly trends cover the baseline plus current period (393 days), with clipped boundary buckets, count bars and window shading. Phenotype trends also show positive counts. Neither bucket sums nor averages reconstruct whole-period results.

The advanced service reuses the unchanged `AnomalyEngine._assess` temporal methods, configured effect/alpha thresholds and patient-dependence policy:

- Marginal Wilson intervals require adequate N and one observed isolate per patient within that period. They are not intervals for the change or proof of between-group differences. Shared patients between periods do not invalidate each marginal interval, but do prevent independent-sample Fisher testing.
- Known shared patients across periods or repeated patients within pooled organism/specimen populations withhold Fisher testing and warning classification when volume is adequate (`analysis_limited`). Inadequate N takes precedence in the reported status; overlap counts remain visible. Observations are not discarded to force independence.
- Otherwise, the selected comparison uses the existing two-sided Fisher test. Its p-value is **unadjusted** for repeated interactive exploration. An exploratory increase/decrease is not a BY-adjusted Statistically Supported Alert.
- Preliminary Signals use the existing rule: sufficient volume in both periods and absolute change **strictly greater than** the configured preliminary threshold (default 10 pp). They can coexist with dependence limitations and are descriptive.
- The unchanged global surveillance scan applies Benjamini–Yekutieli to its full eligible family, including small effects. No new subgroup scan or formal between-group testing is introduced; interactive group differences remain descriptive. Do not infer significance, causation, infection or a treatment recommendation from them.

## Signal investigation and groups

On Overview, choose **Investigate** on a preliminary finding or supported alert. The callback transfers ward, organism, antibiotic, specimen and analysis date, clears extra subgroup restrictions, and opens Investigation. Expert's **Original surveillance signal — original cohort** block retains the original context/counts/windows and BY evidence where applicable; Guided retains its essential scope and limitations. Current subgroup cards and their assessment are separate. Changing patient filters recalculates the subgroup without assigning the original classification to it.

Only a minimal origin locator is saved: context, date, source, version and a database-path fingerprint. Evidence is recomputed for that exact source revision. After a new batch, source switch, unavailable source, or Replay reset, stale original counts are not reused. The locator remains available as historical context; clearing it starts an independent investigation.

Compare one dimension at a time: sex, age, wards, ward types, institutions, home areas or admission timing. Choose the current, historical or rolling period. Only the restriction on that dimension is relaxed; every other filter remains active. Fixed wards can consequently constrain type/institution comparisons to one populated category. Admission comparisons require valid inpatient records and return empty applicable groups for outpatient-only context. Tables retain positive/negative or S/R counts, first/tested denominators, missingness, status and intervals. A reported group can be selected as a descriptive reference for percentage-point differences. The number of patients belonging to multiple displayed groups is shown; groups must not be summed.

## Quality, exports and persistence

Expert Evidence Quality shows eligible first isolates, observed/tested denominators, missing tests or marker-positive counts, minimum N, distinct observed patient counts, cross-period overlap, limitations, source revision and latest relevant sample. Guided Interpretation retains essential availability and interpretation limits. The relevant date is the latest eligible positive-isolate sample at or before the analysis date under the selected cohort and marker species restrictions; it is not restricted to observed antibiotic tests. The global latest source sample is shown separately. Original CSVs and laboratory tables are read-only during analysis.

JSON exports include the complete aggregate report, parameters, source, preliminary threshold and optional original context. CSV exports include all three main periods, weekly/monthly periods and group aggregates with cohort parameters and version. They contain no patient, culture or isolate IDs. Internal transient sets of patient IDs support dependence checks but never enter reports or workspace state. Phenotype aggregate field names use positive/negative/eligible rather than S/R.

Workspace format remains **1**. New page, mode, marker, subgroup controls, comparison dimension/period and origin locator are explicitly allowlisted and validated. Older format-1 records and snapshots missing optional preferences receive UI defaults; no schema migration is needed. Canonical values are restored before widgets. Laboratory, Historical Replay and Simulated Surveillance Scenario keep separate filter sets. A restored Replay stays paused under existing ownership/lease/recovery rules. Autosave and named snapshots store preferences and minimal descriptors, never analytical results or patient identifiers. Trend frequency is a source-scoped persisted presentation preference; descriptive reference selection does not change any engine calculation.

Every advanced aggregate report uses one read transaction and version. Metadata and optional original evidence are checked against that version, with a refresh message if it changes. The active `Settings.database_path` is always supplied; Replay does not fall back to the primary database. Empty Replay sources and phenotype analysis on partial data without antibiotic references are supported. No global user-specific cache exists.

## Example workflows and actual source results

All following examples use the original synthetic laboratory database, version 1, analysis date **2026-09-27**; they are distinct from the deliberately artificial Replay scenario.

1. **Urology / E. coli / urine / TMP, no extra filters:** current S=26, R=7, N=33 (21.21%); historical S=257, R=81, N=338 (23.96%); rolling S=256, R=81, N=337 (24.04%). Change −2.75 pp; three shared patients prevent Fisher testing. The established baseline calculations are preserved.
2. **Male, 65+, Urology / E. coli / urine / CIP:** current S=2, R=3, N=5, percentage withheld; historical S=37, R=13, N=50 (26.00%); rolling S=31, R=16, N=47 (34.04%). No current percentage or change conclusion is justified.
3. **Female, 65+, Urology / E. coli / all specimens / ESBL:** current positive=0, negative=13, N=13 (withheld); historical 1/113 (0.88%); rolling 1/116 (0.86%). A negative count of 13 is not a reportable 0% estimate.
4. **ICU / S. aureus / all specimens / MRSA, all sexes/ages:** current 1/5 (withheld); historical 14/80 (17.50%); rolling 15/79 (18.99%). This is marker monitoring, not infection classification.
5. **Urology TMP annual age comparison:** 0–17: S=20/R=2/N=22 (withheld); 18–44: 65/32/97 (32.99%); 45–64: 44/11/55 (20.00%); 65+: 127/36/163 (22.09%). Difference 18–44 versus 45–64 is +12.99 pp descriptively; no formal between-age test is asserted.
6. **VRE / E. faecalis / all wards/specimens:** current 1/74 (1.35%); historical 2/764 (0.26%). Ten shared patients and 17 repeated baseline observations limit statistical comparison. Rare positives do not automatically imply an alert.
7. **CPE / E. coli / all wards/specimens:** current 0/293 is a reportable 0.00% (Wilson 0.00%–1.29%); historical 2/2834 (0.07%). Dependence still prevents a temporal test. MRSA / E. coli, by contrast, is unsupported.

## Validation and query design

Tests cover deterministic cohort/phenotype outputs, unavailable percentages, source revisions, groups, patient dependence and workspace restoration. Trends and groups use set-based ranking queries, without per-patient or per-category query loops. No analytical cache is used. See the [presentation contract](UX_REDESIGN.md) for chart availability and mode-specific exports.
