# Surveillance Map

Open **Explore → Surveillance Map** for a Ward × Antibiotic analytical matrix. This is not a geographic map. All observations are synthetic. Select organism/specimen in the sidebar and date in the header popover. Expert's patient/map filters provide sex, age groups (0–17, 18–44, 45–64, 65+) or inclusive bounds, ward type, institution and antibiotic class. Hidden restrictions remain active in Guided. Categorical axes and options come from the active source. Sort wards by name or eligible-isolate volume (current-period volume for Change).

## Definitions and periods

**Resistance Level** is `100 × R / (S + R)` for the last 28 days or rolling 365 days. High resistance does not establish a recent increase. **Resistance Change** is `current resistance (%) − historical baseline resistance (%)` in percentage points. Both periods must meet the minimum denominator. The Level-period control does not change Change's fixed windows.

| Window | Inclusive dates for analysis date D | Example D=2026-09-27 |
|---|---|---|
| Current 28 days | D−27 through D | 2026-08-31–2026-09-27 |
| Historical baseline | D−392 through D−28 | 2025-08-31–2026-08-30 |
| Rolling 365 days | D−364 through D | 2025-09-28–2026-09-27 |

The baseline excludes current observations. The rolling annual window overlaps current and is descriptive. Change is not a newly asserted significance result.

## Cohorts and first isolates

The engine reuses `ranked_isolates_sql`, shared cohort predicates and the existing read-snapshot mechanism. Cohort/date filters precede first-isolate selection. Partition by patient, organism, specimen, **ward** and reporting period; order by sample date, culture ID, isolate number and isolate ID. This preserves ward-specific deduplication. Age is age at sampling. A patient may contribute to multiple wards or pooled species/specimens; independently ranked denominators must not be summed into a hospital-wide rate.

Only after ranking does the query join susceptibility and aggregate S/R for all antibiotics. Multiple antibiotics on one isolate do not inflate eligible-first counts. An untested first isolate stays untested; a later tested observation cannot replace it. Class filtering restricts columns after first-isolate selection. Missing tests equal eligible first isolates minus tested S/R first isolates, never susceptible observations.

The map uses `max(minimum_sample_size, anomaly_minimum_sample_size)`, currently 30, consistently with Explorer. Existing ResistanceEngine behavior is unchanged. Suppressed percentages are null, not zero. All-organism/specimen selections are pooled descriptive populations; the map does not infer independent-sample significance from their denominators.

## Status and visual semantics

| Display | Meaning |
|---|---|
| Number | Adequate N; Level is %, Change is pp |
| `0.0` | Actual zero with adequate tested observations |
| `IN` | Insufficient Data, in either period for Change |
| `ND` | No selected-antibiotic S/R results; eligible first isolates can still exist |
| `NA` | Unavailable under explicit urine-only metadata with Blood or Wound selected |

NA uses only `antibiotics.reported_for`; other empty organism/antibiotic combinations are ND without invented biological unsupported classifications. With All specimens, urine-only antibiotics retain the established pooled first-isolate/missing-test behavior.

Level defaults to a 0–100% sequential blue palette. The UI also offers a labeled 0–50% focused scale: values above 50% share the darkest shade but keep their true numeric labels. Change retains a blue–neutral–orange scale centered at zero, with bounds −100/+100 pp. Color bars, numbers, status codes and a textual legend remain visible. Foreground contrast is a readability choice, not a clinical threshold. Selecting a cell does not fade other cell labels. An optional filter hides columns without reportable values, listing their codes; exports and investigation selectors retain every column. See [Surveillance Briefing](SURVEILLANCE_BRIEFING.md).

Hover shows full ward/antibiotic names, dates, percentage or suppressed status, S/R/tested, eligible first isolates, missing tests, minimum N and cell status. Change hover includes both periods and the difference. Selected-cell details show the same counts and period rates. Higher resistance does not establish clinical danger; lower resistance does not establish suitability for a specific patient. Interpret values with organism, specimen and sampling context. No safe/unsafe treatment cutoffs are defined.

## Native selection and investigation

Click a square, or use **Ward for investigation** and **Antibiotic for investigation**. The square-cell heatmap uses native `go.Scatter` point selections through Streamlit `on_select="rerun"`, `selection_mode="points"` and Plotly `clickmode="event+select"`. This avoids Heatmap trace selection limitations without custom JavaScript. References: [Streamlit selection API](https://docs.streamlit.io/develop/api-reference/charts/st.plotly_chart), [Plotly scatter reference](https://plotly.com/python/reference/scatter/), [upstream Heatmap selection issue](https://github.com/streamlit/streamlit/issues/15042).

Tiles display one decimal; details show two, and exports retain full precision. Selection events are validated against current axis codes. Chart identity includes source identity/revision, date, cohort, view, period, class and sort. A consumed event cannot overwrite a later manual selector change.

**Investigate this cell** transfers ward, organism, antibiotic, specimen, date and applicable subgroup/healthcare filters. The active source remains unchanged. Investigation recalculates current, baseline and rolling results. Ordinary map selection explicitly clears any previous alert origin; clicking does not create an alert. To open original signal evidence, use Investigate on an Overview finding.

## Matching surveillance context

P means a matching existing descriptive Preliminary Signal. A means a matching existing exploratory BY-adjusted alert, taking precedence over P. No new anomaly algorithm or significance test is introduced.

Indicators come from the unchanged **full global scan**, preserving its comparison family and configured preliminary threshold. They require an exact organism/specimen context, no additional patient restrictions, matching source identity/revision/date and matching current/baseline counts. Ward-type/institution filters restrict displayed whole wards, so they do not alter the patients within each remaining row. Class filtering never redefines the BY family.

Indicators appear only in Current Level or Change, whose periods match the scan. They are withheld for annual Level, pooled organisms/specimens, sex/age/home/admission subgroups, or mismatched counts/source. This holds even when a subgroup happens to contain all observed patients. A mismatched scan date/version rejects mixed evidence with a refresh message. Missing indicators do not prove absence of change or risk.

## Workspace, Replay and quality

Workspace format remains 1. Navigation and map preference keys are explicitly allowlisted and validated. View, Level period, sort, sex/age/bounds, type/institution, class and selected ward/antibiotic persist. Organism/specimen/date retain their shared canonical preferences. Missing map fields in older format-1 records or named snapshots receive defaults; restoration precedes widgets. Each data source has separate filters and recovered Replay remains paused.

No analytical outputs, point-event payloads or patient identifiers enter saved workspace state. There is no analytical cache or global user-state cache. The supplied active database is used explicitly, never a fallback to primary while browsing Replay. A matrix uses one read transaction; catalog and optional scan versions are checked. Committed imports trigger the existing refresh watcher.

Empty and partial Replay sources show No Data safely. All choices initialized on an empty source remain All after data arrives; choose an exact organism/specimen to request matching indicators. A source without ward/antibiotic references waits for a batch. The original SQLite and CSVs remain read-only during analysis.

The expandable quality section provides aggregate counts/statuses and CSV/JSON downloads with dates, cohort parameters, view, class, source/version and matching indicators. No patient, culture or isolate identifiers are displayed or exported. Transient patient sets used by the existing scanner remain internal.

## Actual original-source examples

All examples use original synthetic laboratory data, version 1, date **2026-09-27**, separately from the artificial Replay scenario:

- Urology / E. coli / urine / TMP: current S=26/R=7/N=33 (21.21%); historical 257/81/338 (23.96%); rolling 256/81/337 (24.04%); descriptive change −2.75 pp. Original baseline results are preserved.
- Urology / E. coli / urine / CIP: current 28/5/33 (15.15%); historical 310/28/338 (8.28%); rolling 306/31/337 (9.20%); change +6.87 pp, without an automatically assigned alert.
- PHC-S / E. coli / urine / AMP: current 40/34/74 (45.95%); historical 430/190/620 (30.65%); change +15.30 pp. It matches P; 33 shared patients prevent independent Fisher testing. P is withheld in Annual and patient subgroups.
- ICU / E. coli / urine / TMP: rolling 19/9/28 is IN; current N=0 is ND; historical 22/9/31 is 29.03%. Change is unavailable.
- ED / E. coli / urine / MEM: rolling R=0/N=616 and current R=0/N=45 are valid 0.00% observations, distinct from ND and unrelated to patient treatment suitability.
- Male 65+, Urology / E. coli / urine / CIP: current 2/3/5 is suppressed; historical 37/13/50 (26.00%); rolling 31/16/47 (34.04%). Scan classification is withheld for the subgroup.
- E. coli / blood / TMP is NA under explicit urine-only metadata, not 0%.

The E. coli/urine annual matrix has 117 cells: 9 wards × 13 antibiotics; 72 valid, 9 insufficient, 36 no-data. Current has 36 valid, 36 insufficient and 45 no-data. Blood annual has 27 explicit NA cells: three urine-only antibiotics across nine wards.

## Validation and query design

Deterministic fixtures and original-source checks compare all matrix periods with ResistanceEngine, including missing/insufficient values, native selection, source isolation and restoration. A set-based analytical query serves all cells and periods; matching current/change indicators reuse the original full scan. No per-cell resistance query loop or analytical cache is introduced.
