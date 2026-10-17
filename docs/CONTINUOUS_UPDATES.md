# Continuous updates and isolated replay

This guide describes Developer Mode. Public Demo Mode disables imports, writable/historical Replay and revision-watcher polling, and substitutes four owner-prepared immutable scenario snapshots with an independent visitor cursor. See [Deployment](DEPLOYMENT.md).

Replay recovery uses durable token-scoped workspaces, validated paused restoration and conservative owned-file cleanup. See [Workspaces and recovery](WORKSPACES.md).

All supplied laboratory observations and the additional demonstration scenario are synthetic. The application provides exploratory surveillance, not treatment advice or confirmation of a clinical outbreak.

## Incoming records and atomic imports

Use **Data & Settings → Laboratory Dataset → Expert Mode** to upload a UTF-8 six-file bundle: `wards.csv`, `antibiotics.csv`, `patients.csv`, `cultures.csv`, `isolates.csv`, `susceptibility.csv`. Keep the exact headers documented in `src/data_loader.py`. Include new records and any required references. Tables without new rows may contain only their header. References already committed may be omitted or repeated identically. A culture must arrive with all its declared isolates; susceptibility results can arrive later for existing isolates.

`import_csv_bundle` is the existing, shared ingestion path. It checks schemas, values, metadata, chronology, natural-key conflicts, culture isolate counts, reporting restrictions and foreign keys. Exact duplicates are skipped. Conflicting existing values reject the entire packet: committed results cannot be overwritten. Missing, unexpected or repeated filenames are rejected.

Validation and append operations run under `BEGIN IMMEDIATE`, foreign keys, WAL, a five-second busy timeout and `synchronous=FULL`. A changing successful transaction increments `data_version` exactly once and commits source rows, file SHA-256 metadata and success history together. A successful duplicate-only packet records the attempt without changing the revision. Failure rolls back every laboratory change and revision update; a separate transaction records the failure. If the database remains inaccessible, the error explains that history could not be recorded.

Python example, using an explicit target rather than global configuration changes:

```python
from pathlib import Path
from src.config import get_settings
from src.data_loader import HEADERS, import_csv_bundle

incoming = Path("incoming")
files = {f"{table}.csv": (incoming / f"{table}.csv").read_bytes() for table in HEADERS}
result = import_csv_bundle(files, get_settings(), origin="incremental_csv")
print(result.data_version, result.rows_inserted, result.rows_unchanged)
```

## Refresh and consistent analytics

`refresh_watcher` uses `st.fragment(run_every=1.0)`. Each invocation checks the current revision and last successful import timestamp with short-lived read-only connections. When either changes, it requests a full app rerun. It never contains a blocking polling loop. Browser connectivity and Streamlit scheduling determine actual latency; suspended tabs may update later.

A rerun reloads the catalog and recalculates the 28-day rate, overlapping rolling 365-day rate, trends, selected comparison, all-cohort statistical scan and preliminary signals. `build_report` checks every analytical result against the catalog revision; a mixed report is hidden and the user is prompted to refresh. No analytical result cache or schema change is needed at this scale.

The page displays the latest sample, revision, source revision timestamp and **Last Successful Import**. A green update notice confirms changed revisions and recalculation. By default Analysis Date follows the latest sample; manually choosing a date intentionally pins it until **Use latest sample** is pressed. Historical observations use sample dates, never the host's current clock. A changing import containing only older samples still increments the revision even if the maximum sample date remains unchanged.

## Historical replay

Choose **Historical Replay** in **Data Source**. `historical_plan` inventories distinct dates in a read-only snapshot of the configured laboratory database. Nonempty Monday–Sunday calendar weeks become chronological packets. Each step queries only that week's cultures, all their declared isolates and susceptibility records, and the required patients, wards and antibiotics. It serializes those rows into the normal six-file bundle and invokes the same atomic importer against the session's demo database. It does not re-import the complete original dataset on each step. The existing validator does read committed target records when checking consistency.

Controls:

- **Next Batch**: commit one packet immediately, then refresh the whole page.
- **Start Replay**: schedule the next packet; continue while the connected page is active.
- **Pause Replay**: stop scheduling packets without deleting committed records.
- **Playback Speed**: one packet per 1, 2, 5 or 10 seconds. This is a minimum interval, not a throughput guarantee. Calculation time may extend the observed interval.
- **Reset Replay**: pause and create a new empty owned database, returning to batch zero. Historical reset refreshes the source date inventory and revision.

Each timer invocation imports at most one packet: missed ticks never cause a catch-up loop. The cursor advances only after a successful commit. A failed packet pauses playback and leaves the cursor unchanged. Correct the input/source problem and retry; resubmitting an already committed packet is harmless. Switching sources also pauses running replay controllers. A completed plan stops automatically.

The source revision is checked inside each packet's read snapshot. If it has changed since the plan was created, replay pauses with **Historical source changed**. Reset to build a consistent new plan. The prototype does not maintain a separate immutable archive of old source revisions.

## Session and database isolation

Each Streamlit session creates a random directory beside the configured working database:

```text
data/runtime/aei.db                      # Working laboratory database
data/runtime/replay/<session-mode-uuid>/<generation-uuid>.db
```

The controller passes a frozen copied `Settings` object explicitly to ingestion and analysis. No environment variable or global database setting is switched. Historical and artificial modes have distinct controllers, and two browsers have separate demo databases, cursors, speeds and filters. Filters are saved separately for each source mode within a session. Demo target paths are checked against the working and historical source database paths. Reset rotates a database rather than deleting files or source records. Old generations remain locally for inspection; there is no automatic cleanup service or backup system.

Replay pages display their mode prominently. **Simulated Surveillance Scenario** labels cover all charts, metrics, signals and alerts below that banner. Data & Settings in replay mode disables arbitrary imports and directs the user to Laboratory Dataset for shared laboratory uploads. The primary dataset and original six CSV files remain unchanged. Concurrent real imports are serialized by SQLite transactions. The application has no authentication and is intended for local demonstration.

## Preliminary signals versus statistical alerts

Both use the unchanged first-isolate rules and fixed, non-overlapping surveillance windows: current 28 days, baseline the preceding 365 days ending before the current window. These differ from the Resistance Trends's overlapping annual rate.

**Preliminary Signal** is descriptive: both windows must meet the effective minimum (at least 30 tested first isolates) and `abs(current resistance − baseline resistance) > threshold_pp`. The default is 10 percentage points, configured with `AEI_SIGNAL_MINIMUM_CHANGE_PP` or the Expert Resistance Trends threshold control. The comparison is strict: exactly 10 pp does not pass a 10 pp threshold. This setting does not alter statistical thresholds or BY correction.

Signals display actual baseline/current S/R/N, change, cohort, shared tested patients and independence limitations. They can exist for `analysis_limited` comparisons. They must never be interpreted as statistically significant merely because the effect is large. Repeated-patient records are retained; no patients are silently dropped to manufacture independence.

**Statistically Supported Alert** is an exploratory scan result that passes the existing sample-size and patient-independence guards, two-sided Fisher test, minimum effect size and Benjamini–Yekutieli correction over all eligible scan comparisons. Existing significance/effect defaults remain unchanged. Selected-filter tests remain explicitly unadjusted and exploratory; the new alert count comes from the corrected all-cohort scan. Wilson intervals remain proportion intervals, not intervals for the difference. Unmeasured confounding and other dependencies remain possible even with distinct patient identifiers. Neither layer confirms a real outbreak. A cohort may appear in both layers; their counts must not be added as distinct events.

## Reproducible artificial demonstration

Select **Simulated Surveillance Scenario**. It is deliberately constructed and does not reflect an event in the original dataset. Every observation uses the dedicated `SIM` ward and `SIM`-prefixed patient/culture/isolate identifiers, *E. coli*, ciprofloxacin and urine, with valid reference metadata and EUCAST labeling. There are 200 distinct patients across both windows.

| Batch | Sample date | New S | New R | Purpose |
| --- | --- | ---: | ---: | --- |
| 1 | 2026-08-01 | 90 | 10 | Baseline |
| 2 | 2026-09-01 | 36 | 4 | Stable recent rate before the change |
| 3 | 2026-09-02 | 24 | 36 | Deliberately increased resistance |

After two steps: revision 2, recent 4/40 = **10%**, annual 14/140 = **10%**, baseline 10/100 = **10%**, no preliminary signal and no corrected statistical alert.

After the third step: revision 3, recent 40/100 = **40%**, annual 50/200 = **25%**, baseline 10/100 = **10%**, change **+30 pp**, one preliminary signal and one corrected exploratory alert. There is one eligible comparison, so its BY-adjusted p-value equals its Fisher p-value. Reset and repeat to reproduce the same values. Larger configured minima or different thresholds may suppress the demonstration alerts intentionally.

## Recovery and limits

- Failed imports leave the previously committed laboratory revision available. Review Import history, correct the packet and retry. A terminated process mid-transaction is recovered by SQLite; reopen and check integrity. Do not remove WAL/SHM files from a running database.
- Replay failures pause the controller and preserve its last commit. Retrying a committed packet is idempotent. Reset creates an independent clean run; historical source changes require reset.
- Controllers are reconstructed from validated workspace descriptors after reconnect/server restart and always recover paused. Recovery requires the same durable files and source revision. Use the owned-file cleanup preview; do not delete unknown, referenced or active Replay generations.
- Timer refresh is connected-browser, real-time-like behavior, not a background ingestion scheduler or guaranteed real-time service. SQLite lock timeouts can require retry. Full analysis and validation costs increase with data volume; there is no distributed analytical cache or database migration.
- CSV packets remain a six-file interface. Culture/isolates arrive consistently; corrections of existing laboratory results need a future audited workflow. Real hospital connectors, production authentication, an AI assistant, database migration and an automatic backup policy are not implemented. Persistent workspaces are supported on durable local storage.

## Validation

The full pytest suite covers initial state, batch dates and dependencies, no-op retries, rollback, revision transitions, analytics, signal thresholds, independence guards, artificial Fisher/BY results, pause/restart/speed, reset, source changes, independent controllers and UI sessions, native timer reruns, protected working DB/CSV hashes and dashboard updates. Tests use disposable databases; the optional integration test reads and verifies the original files without changing their content.
