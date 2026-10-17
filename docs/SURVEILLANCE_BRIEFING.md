# Surveillance Briefing and Data Quality Center

Open **Reports → Surveillance Briefing**. The page identifies the source, revision, selected reporting date, latest global laboratory sample and last completed import. All observations are synthetic. The prototype is not intended for patient-specific treatment decisions.

## Scopes and calculation sources

The six overview cards always describe the **full active source**: preliminary signals, statistically supported alerts, patient-dependence limited comparisons, insufficient comparisons, no-data comparisons and eligible statistical comparisons. Preliminary signals can include supported alerts; these counts must not be added. "Hospital-wide" includes every ward present in that source, including its primary-care wards. It is not a pooled hospital resistance percentage.

Ward, organism and specimen filters select exact comparison rows and queue findings from the original complete `AnomalyEngine.scan`. They never redefine or rerun the BY correction family. The page performs that full scan once per rerun. No new anomaly algorithm, clinical score, threshold or first-isolate rule is introduced.

The selected descriptive investigation uses `AdvancedAnalysisEngine` once for the selected antibiotic and complete ward/organism/specimen context. It can pool wards, organisms or specimens, so its denominator and independence limitations can differ from an individual exact scan row. It does not inherit statistical alert classifications from those rows. The three displayed periods are:

| Period | Inclusive boundaries relative to D | D = 2026-09-27 |
|---|---|---|
| Current | D-27 to D | 2026-08-31 to 2026-09-27 |
| Historical anomaly baseline | D-392 to D-28 | 2025-08-31 to 2026-08-30 |
| Rolling dashboard annual period | D-364 to D | 2025-09-28 to 2026-09-27 |

The annual period overlaps current. All periods and groups select first isolates independently and must not be added. Cohort and date restrictions precede ranking; S/R lookup follows ranking. An untested first isolate cannot be replaced by a later tested isolate. Resistance is `100*R/(S+R)`; counts remain visible when the percentage is suppressed below the configured minimum, currently 30.

## Investigation queue

Only the union of established descriptive signals and established full-family BY alerts enters the queue. This union retains supported alerts even if the descriptive signal threshold was configured above the statistical effect threshold. Each row belongs to exactly one queue category: supported takes precedence, otherwise preliminary. The full preliminary card still counts the original descriptive rule, including overlapping supported results.

**Review Priority** orders supported findings first, then descending absolute percentage-point change, then descending smaller current/baseline denominator, then stable ward/organism/antibiotic/specimen labels. It is a transparent descriptive ordering, not a clinical risk score. Other orderings are absolute change, current sample size, ward and latest observation. Percentage level alone is never an ordering criterion. Both increases and decreases remain eligible under the original rules.

Filters select All, Preliminary or Statistically supported findings. The table supports native column sorting and scrolling. Each displayed finding has a corresponding cohort-labeled **Investigate** action below the table. Ten findings are displayed per page. Actions retain the original exact population, source identity, data version and analysis date through the existing origin locator. Explorer separately recalculates patient subgroups; changing those filters does not alter the original evidence. If the originating revision is unavailable, Explorer retains the locator and explicitly withholds old evidence.

Latest relevant observation dates come from one grouped SQL query over positive-isolate cultures at or before the reporting date. They describe that ward/organism/specimen cohort, not antibiotic-specific AST freshness. The selected analysis uses the existing advanced engine's equivalent eligible-isolate freshness calculation. Global source freshness includes all cultures and can be later than either.

## Data Quality Center

Expert Data Quality Center displays full-source and filtered counts, actual denominators, missing test opportunities, source freshness and selected-analysis freshness. Optional details list comparison sample sizes and dependence explanations. Guided Data Quality retains a concise account of availability and essential limitations.

| Category | Meaning |
|---|---|
| No Data | At least one period has no S/R observations for that antibiotic |
| Insufficient Data | Both periods have observations; at least one N is below the minimum |
| Patient-dependence limited | Both N meet minimum but cross-period or within-period patient dependence prevents the independent-sample Fisher test |
| Eligible statistical comparisons | Fisher was performed; its p-value enters the original full-source BY family |
| Alert criteria not met | Eligible, but adjusted significance and effect-size rules are not both met |

The limited category is mutually exclusive with insufficient/no-data statuses. A separate **dependence in any category** count includes low-volume comparisons with known overlap. This avoids treating the limited category as every occurrence of repeated patients.

Missing susceptibility is `eligible first isolates - S/R-tested first isolates`, summed over **observed exact scan combinations**, separately per period. These are first-isolate/antibiotic opportunities, not unique patients or independent observations. Never sum them into a population denominator. The scan discovers combinations with any S/R observation in the 393-day span; it is not a full Cartesian product of reference tables and cannot measure missing tests for combinations absent throughout the span. Unavailable, unperformed and unreported tests cannot be distinguished. A zero missing count in this universe does not certify laboratory completeness. Data quality is not a clinical safety certification.

Global freshness is the exact latest sample date plus calendar age relative to today's UTC date, without an invented acceptable-age threshold. A pinned earlier report excludes subsequent samples; the UI explains when newer source samples exist. Replay freshness reflects simulated historical time, not a live feed.

## PDF reports and aggregate manifests

Open **Reports → Surveillance Briefing**. Guided prioritizes PDF Reports; Expert offers Selected Analysis & PDF Reports. Choose Hospital-wide summary or Department-focused briefing, verify the visible selected scope, then Generate PDF report. Ordinary PDFs are available in both modes; JSON manifests are Expert controls. For the full active patient-filtered cohort use Reports → Current Investigation. ReportLab creates vector text, tables and the selected three-period comparison chart; no external rendering process is needed by the running application. Long paragraphs wrap; tables repeat headers across page breaks. The library's bundled Vera fonts avoid host-specific font dependencies.

Every PDF includes the synthetic label, prominent active source, version, scope, reporting and generation dates, latest sample/import, actual S/R/N, available findings, quality, limitations, thresholds, methodology and an explicit treatment-decision limitation. Replay/artificial sources are prominently labeled on the first page and every footer. Selected investigations preserve insufficient/no-data labels. A PDF shows at most 20 findings in documented Review Priority and explicitly states truncation; the JSON contains all findings and comparison aggregates. The selected PDF includes the exact three-period calculation, denominators, missing tests and a vector comparison chart when reportable values exist; otherwise a compact availability note. The hospital PDF reports exact comparison results instead of inventing an overall pooled resistance rate.

The manifest includes algorithm version, hashed source-path identity (no filesystem path), source revision, dates, complete scope filters, thresholds, actual statistics, original global BY family and generation timestamp. The selected-analysis object additionally records its full cohort, code, periods, trend and grouping settings. Each PDF embeds the SHA-256 of its canonical manifest. JSON serialization preserves full precision; PDF rates are rounded to two decimals for presentation. With the same report object and generation timestamp, invariant PDF generation produces identical bytes; independent test cases check this contract. Reproduction from source requires that same revision to remain available.

Exports contain aggregates only. Patient, culture and isolate IDs, workspace identities, restore tokens and private URLs are never passed into the renderer. Report metadata and footers contain no workspace URL. Paragraph content is XML-escaped before ReportLab formatting. The source fingerprint identifies the resolved database path; it is not a claim of a cryptographic source-file snapshot. The aggregate-manifest hash verifies report inputs, not data provenance or clinical certification.

## Refresh, revisions, Replay and workspaces

All engines use read snapshots. The service validates catalog, scan, latest-observation and selected-analysis revisions, then checks source identity/version again. PDF export validates before and after rendering. A concurrent import causes a refresh message and no mixed-version PDF is returned. Every downloaded artifact remains explicitly dated at its original revision; it does not silently become current after another import.

PDF bytes are held only in browser-session memory, alongside an input signature. Source, revision, reporting date, filters, antibiotic, thresholds or report scope changes invalidate that download. The JSON download is stored alongside its PDF, preserving the exact generation timestamp and hash. Nothing is added to the laboratory database or original CSV files. There is no analytical result cache or new background service.

The page remembers the last observed version per source in this browser session and announces a new import revision after recalculation. It does **not** infer metric differences between database revisions: earlier laboratory revisions are not archived. Resistance differences between reporting periods are a separate concept. Reopening a session can show freshness and the current revision but cannot reconstruct an unobserved previous state.

Laboratory Dataset, Historical Replay and Simulated Surveillance Scenario use their own settings, source identity and scans. Existing paused-recovery/ownership controls are preserved. Workspace format remains 1; the new navigation, queue/scope and map appearance preferences are allowlisted and source-specific. Older format-1 states remain valid when new fields are absent. Analytical results/PDFs are never persisted in a workspace. Restart a development server after changing Python preference allowlists so old imported class instances do not retain previous validation constants.

## Surveillance Map appearance

The existing map now offers **0-100% full** and **0-50% focused** resistance-level scales. The focused scale labels itself and explains saturation above 50%; exact displayed values and exported statistics remain unchanged. Text contrast depends on the selected visual scale, with explicit selected/unselected text colors. Change keeps its fixed -100/+100 pp diverging scale, regardless of the Level scale preference.

An optional setting hides antibiotic columns with no reportable value in the displayed view. Actual zero is reportable. The hidden count and column codes are displayed. Exports and accessible investigation selectors retain all columns. ND / IN / NA remain expanded in the legend and detailed hover text. Map selections still open an ordinary Explorer cohort without manufacturing an alert origin.

## Validation and query design

The service uses one original full scan, one grouped latest-observation query and an optional advanced selected analysis; there is no per-patient, finding or cell query loop. PDF formatting consumes computed aggregates and validates revisions. Tests cover exact scope, denominators, real zero, unavailable charts, determinism, privacy, version races, source isolation and restoration.
