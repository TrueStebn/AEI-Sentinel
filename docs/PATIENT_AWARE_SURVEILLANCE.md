# Patient-Aware Surveillance

Open **Explore → Investigation → Expert → Patient-Aware Analysis** in antibiotic mode. Overview findings open the original context through Investigate. The retained Expert Resistance Trends tool also provides patient-aware actions for shared tested patients, including **Review selected patient overlap**. Expert Briefing queue rows with shared tested patients provide **Patient-aware** next to Investigate. These routes preserve an originating finding's source, date, revision and exact population. Ordinary selected cohorts receive no manufactured alert origin.

The section is labeled **Additional Exploratory Evidence**. It does not change original resistance calculations, preliminary-signal rules, global warning counts, independent-sample safeguards, or the existing full-family BY scan. Aggregate patient-aware evidence is available in the investigation PDF, optional selected Briefing PDF and Expert JSON exports.

## Four distinct result types

1. **Full-population descriptive change**: the original selected population, including returning patients. The existing comparison remains visible above and its counts are repeated in this section. A known patient overlap continues to withhold the original independent-sample Fisher test.
2. **Paired returning-patient analysis**: only patients with one eligible first isolate in each period and usable selected-antibiotic S/R in both. It concerns the repeatedly observed population, not a hospital-wide resistance change.
3. **Independent-patient sensitivity analysis**: exclude every patient observed in both selected periods from both groups, including those missing S/R. It describes different, restricted populations. It never substitutes for the full comparison.
4. **Original global BY classification**: remains the result of the existing complete exact ward/organism/antibiotic/specimen scan. A real originating classification is displayed separately in Explorer's original signal block. For an ordinary cohort, the new section does not rerun the global scan or infer a classification; consult Resistance Trends or Briefing.

There is no new multiple-comparison scan or hypothesis family. The new tests are selected exploratory tests without multiplicity adjustment. Do not combine their p-values, compare them as if they concern the same estimand, or promote a small p-value to an original BY alert. No clinical risk score, outbreak classification or treatment recommendation is created.

## Calendar periods and first isolates

Both analyses use the existing `comparison_windows`:

| Window | Inclusive boundaries for analysis date D | D = 2026-09-27 |
|---|---|---|
| Current | D-27 to D | 2026-08-31 to 2026-09-27 |
| Historical baseline | D-392 to D-28 | 2025-08-31 to 2026-08-30 |

The overlapping rolling 365-day Resistance Trends window is not used for these tests.

The engine reuses `ranked_isolates_sql` and the existing advanced cohort predicates. Apply ward, organism, specimen, sex, age at sampling, home area, ward type, institution and valid admission-timing restrictions before ranking. Select first per patient / organism / specimen / period, ordered by sample date, culture ID, isolate number and isolate ID. The selected ward restricts the cohort; pooled wards deduplicate exactly as the existing selected AdvancedAnalysisEngine does. Global exact ward rows are therefore unchanged.

Look up the selected antibiotic only after ranking. A later tested result cannot replace an earlier untested first isolate. Dates outside either fixed period do not enter membership. "Only in current" means only within the selected cohort and two selected windows, not never observed elsewhere in the hospital. For example, a patient aging into an age-restricted cohort can be current-only in that analysis.

## Membership and missing results

Presence is defined by an eligible first isolate, regardless of S/R availability. The three disjoint membership categories are baseline-only, current-only and observed in both. Each reports distinct patients and baseline/current S/R/N plus missing-first-isolate counts.

Separate aggregate fields report patients lacking any usable S/R in each period, patients lacking usable S/R across both periods, and patients with usable S/R in both. For a pooled cohort, a patient's tested and untested isolates may coexist; observation counts must not be mistaken for distinct patient counts. Missing test observations equal eligible first isolates minus S/R-tested first isolates.

Missing S/R is never treated as susceptible, resistant or a negative marker. A shared patient with an untested baseline and a tested current isolate remains observed in both, is excluded from sensitivity in both periods, and does not form a complete pair. This makes the exclusion rule independent of AST availability.

## Paired table and exact McNemar

The table is oriented **baseline -> current**:

| Cell | Meaning |
|---|---|
| S -> S | Susceptible in both selected periods |
| S -> R | Susceptible baseline, resistant current |
| R -> S | Resistant baseline, susceptible current |
| R -> R | Resistant in both periods |

For `b = S->R`, `c = R->S`, the exact two-sided McNemar p-value is `min(1, 2 * P[Binomial(b+c, 0.5) <= min(b,c)])`. The implementation uses SciPy's exact two-sided `binomtest` on discordant pairs. It uses neither asymptotic chi-square nor a continuity correction. Independent hand-binomial test cases verify this formula and its symmetry, including 6 vs 0 discordances yielding 0.03125 and 13 vs 7 yielding 0.26317596435546875.

No complete pairs means no paired test and a null p-value. With at least one complete pair but zero discordances, the engine reports the conventional p=1 and explicitly states that this is uninformative about equivalence or population-wide change. Small positive paired samples are labeled as discrete, unstable and potentially low-powered. An exact probability can be mathematically computed with few discordances; it does not gain clinical or confirmatory meaning from being exact. The existing N>=30 rule for resistance percentages and independent Fisher testing is unchanged. No paired resistance percentages or paired-difference confidence intervals are introduced.

Pairing requires an **explicit exact organism and specimen** and one eligible first isolate per shared patient in each period. If pooled organisms/specimens yield multiple first isolates for a shared patient, or the explicit context is pooled, the paired table and test are unavailable, not zeros. Even a single baseline and single current observation are not paired across different organisms/specimens. Refine the filters rather than choosing an arbitrary result or treating multiple pairs from one patient as independent. Pooled membership and sensitivity counts remain available with their own safeguards.

The conditional null concerns equal discordant transition probabilities among the repeatedly observed, complete-case patients. Pairs must be independent across patients. The source cannot establish absence of household, ward, temporal or other clustering. Returning patients and complete cases are not randomly selected. Differences can reflect sampling, patient composition, testing, strain or guideline changes. Matching a patient ID and organism does not establish the same strain or continuous infection. The results do not establish an outbreak or treatment failure.

Primary method references: [statsmodels exact McNemar documentation](https://www.statsmodels.org/dev/generated/statsmodels.stats.contingency_tables.mcnemar.html), [SciPy binomtest](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html). No statsmodels dependency is needed.

## Restricted sensitivity analysis

After determining membership from eligible first isolates, remove the complete observed-shared patient set from both periods. Keep the original first-isolate rule and all current patient filters for the remaining patients. Recalculate eligible-first, missing tests, S, R, N and resistance `100*R/(S+R)` for both periods; report current minus baseline only when both rates are available.

The established `AnomalyEngine._assess` supplies counting, sample-size safeguards, Fisher and supported marginal Wilson intervals. Its unadjusted `potential_*` classification is deliberately not propagated into sensitivity results. Statuses are only No Data, Insufficient Data, Analysis Limited, or Eligible selected exploratory test. No new warning counts are generated.

Fisher requires both denominators to meet `max(minimum_sample_size, anomaly_minimum_sample_size)`, currently 30, and no known within-period patient repetition. Excluding cross-period patients does not solve repeated observations across pooled organism/specimen groups within a period. In that case Fisher remains withheld. Marginal Wilson intervals are shown only for adequate N with one tested observation per patient in that period; they are not intervals for a change or a paired effect. Remaining unobserved dependencies and confounding cannot be excluded.

Removing returning patients changes the target population and may introduce selection bias. Patients who appear only once can differ systematically from returning patients. The original estimate remains visible and is not "corrected" by replacing it with this restricted estimate. Paired and sensitivity analyses need not agree.

## Read-only integrity, source isolation and exports

`PatientAwareEngine` uses one SQLite read snapshot with the current source version and latest sample. One ranked SQL query returns all required first-isolate outcomes; transient Python grouping constructs patient sets and transitions. There are no patient-by-patient SQL calls. Public dataclasses contain only counts, statistics and cohort/settings metadata; no patient, culture or isolate IDs.

The result carries the active source label, hashed database identity, revision, reporting date, full cohort, selected antibiotic, minimum N, confidence level and algorithm version. Source/revision is validated after calculation and before aggregate JSON rendering. Explorer also verifies that its existing advanced report and new full-population counts match the same context, revision, dates and S/R/N. A changed revision produces a refresh message rather than mixing evidence. Results are recalculated; no new analytical cache, database writes, schema changes or background service is introduced.

Laboratory Dataset, Historical Replay and Simulated Surveillance Scenario pass their own settings. Each source computes its own membership and tests. Recovered Replay remains paused. The existing artificial scenario uses distinct patients in both periods and thus has no complete returning-patient pairs; its original BY alert remains unchanged, and its selected sensitivity test is explicitly separate.

JSON contains only aggregate outputs, methods, assumptions and complete filter context. No restore token, workspace URL or filesystem source path is included. The hashed source identity identifies the configured path, not a cryptographic archive of old database revisions. A revision must remain available for recalculation; previous revisions are not archived automatically.

Workspace format stays 1. The optional `explorer_section` choice (`overview` / `patient_aware`) is allowlisted, autosaved and source-specific. Patient-aware routes save that focus along with existing filter/origin preferences. Old workspaces lacking the field remain valid. No patient sets, results or p-values are persisted in workspace state. Patient-Aware Analysis is an Expert Investigation tab. Guided retains summary limitations and ordinary PDF access. Patient analysis applies to antibiotic S/R; phenotype mode does not treat marker results as S/R.

## Original synthetic-source examples

| Exact cohort | Baseline-only / current-only / shared patients | SS / SR / RS / RR | McNemar p (raw) | Restricted current S/R/N; % | Restricted baseline S/R/N; % | Restricted delta (pp) | Fisher p (raw) |
|---|---|---|---:|---|---|---:|---:|
| PHC-S AMP / E. coli / urine | 587 / 41 / 33 | 8 / 13 / 7 / 5 | 0.26317596 | 25/16/41; 39.02% | 409/178/587; 30.32% | +8.70 | 0.29365017 |
| PHC-N AMP / E. coli / urine | 593 / 49 / 18 | 11 / 3 / 3 / 1 | 1.00000000 | 40/9/49; 18.37% | 391/202/593; 34.06% | -15.70 | 0.02632987 |
| URO TMP / E. coli / urine | 335 / 30 / 3 | 0 / 1 / 2 / 0 | 1.00000000 | 24/6/30; 20.00% | 256/79/335; 23.58% | -3.58 | 0.82252982 |

Laboratory source v1, D=2026-09-27. All three have no missing S/R among their eligible first isolates. SS/SR/RS/RR are baseline-to-current transitions. An independent anti-join SQL query (not the shared ROW_NUMBER helper) verified membership, transitions and restricted S/R counts. Hand binomial and hypergeometric calculations independently reproduced McNemar and two-sided Fisher p-values. Full current/baseline results also match the unchanged AdvancedAnalysisEngine across five representative contexts.

The PHC-N restricted comparison has a nominal unadjusted p=0.02632987, while its returning-patient discordances are balanced (3 vs 3; p=1). These are different target populations. The restricted result is exploratory, vulnerable to selection and repeated-investigation effects, and is not a new global BY alert. No confirmatory population-wide or clinical conclusion is established by these additional analyses. The original full scan remains 996 combinations, 36 analysis-limited, 597 insufficient, 363 no-data and zero BY alerts.

## Validation and query design

One ranked read query serves all patients/categories; transient patient sets never enter public results. Tests cover all-shared/independent/partial populations, all four transitions, missing tests, first-isolate boundaries, exact probabilities, ambiguity, remaining dependence, privacy, revision races and Replay isolation.
