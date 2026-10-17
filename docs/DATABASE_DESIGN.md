# SQLite storage and import design

AEI Sentinel uses the schema in [`db/schema.sql`](../db/schema.sql), user version 1. No populated database is required in a clone. The importer and application initialize directories and schema when needed.

## Files and data roles

| Location | Role | Versioned? |
|---|---|---|
| `data/synthetic/*.csv` | Six original synthetic inputs | Yes, exact supplied bytes |
| `db/schema.sql` | Tables, constraints and indexes | Yes |
| `data/runtime/aei.db` | Local imported laboratory source | No |
| `data/runtime/workspaces.db` | Token-scoped preferences and named snapshots | No |
| `data/runtime/replay/` | Owned isolated Replay generations and leases | No |
| `data/backups/`, `data/raw/` | Optional local backups/private import files | No |

Environment overrides are `AEI_DATASET_PATH`, `AEI_DATABASE_PATH` and `AEI_WORKSPACE_DATABASE_PATH`. Relative override paths resolve from the process working directory. Defaults derive from the module location. The bundled dataset takes priority; legacy project/sibling `data/raw` discovery remains supported when the bundle is absent. Workspace storage must be separate from the laboratory database.

## Schema and normalized source

`patients` and `wards` reference `cultures`; cultures reference `isolates`; isolates and `antibiotics` reference `susceptibility`. Natural keys are patient_id, ward_code, culture_id, isolate_id, antibiotic_code and the isolate/antibiotic susceptibility pair. Exact source columns/counts are listed in the [dataset reference](../data/synthetic/README.md).

The importer validates redundant ward, culture, organism and antibiotic metadata against canonical records before storing normalized fields. CSV `antibiotics.class` maps to `antibiotic_class`; `cultures.n_isolates` maps to `n_isolates_declared`. Missing admission dates become SQL NULL. Date, ISO-week, age, admission chronology, specimen translation, reporting scope and declared isolate counts are validated. Original CSV bytes remain untouched.

`data_version`, `import_batches` and `import_files` hold revision and attempt/file-hash history. `user_sessions` and `saved_workspaces` serve private workspace persistence in a separate database using the same schema. `analysis_cache` is reserved by the schema but is **not used**: analytics are recalculated from read snapshots. PDF bytes are only session-local export artifacts.

## Initialization and atomic imports

Run `python -m src.data_loader --expect-original` from the repository root. The first import inserts 253,766 source rows and creates revision 1. A repeated identical bundle is a no-op for source records and revision, while recording the attempt.

Each connection enables foreign keys, bounded busy timeout and synchronous FULL. Initialization selects WAL. Imports use BEGIN IMMEDIATE and insert in dependency order: wards/antibiotics/patients, cultures, isolates, susceptibility. Parsing and validation must succeed before source inserts. Conflicting existing natural keys reject the entire packet; committed observations are not overwritten. Changing source rows, revision and successful-import metadata commit together. Failure rolls back source changes and records the failed attempt in a separate transaction when storage is accessible.

All six filenames are required; a table without incoming rows may contain its header only. Cultures must arrive with their declared isolates. Later AST records may refer to existing isolates. See [continuous updates](CONTINUOUS_UPDATES.md).

## Analysis and persistence boundaries

Cohort/date filters precede deterministic first-isolate selection. Antibiotic test lookup follows selection, so an untested first isolate is not replaced by a later tested one. Separate reporting periods and groups deduplicate independently. The full ward-specific surveillance scan includes ward in its partition; a selected pooled cohort has its documented distinct scope. See [resistance analysis](RESISTANCE_ANALYSIS.md).

Analytical services use short-lived read snapshots and source-version checks. Workspace writes target only the separate workspace database. Raw restore tokens are not stored: owner/session keys are their SHA-256 digests. Preference JSON has an allowlist and optimistic revision; it does not contain laboratory rows, patient identifiers or calculated reports. Replay recovery validates owned relative paths, source/history/version, database integrity and active leases, then restores paused. See [workspaces](WORKSPACES.md).

## Operational limitations

SQLite is on the application host and is shared by visitors selecting the laboratory source. It is not a separate browser-side database. Imports are unauthenticated in this local prototype. A controlled correction workflow, production identity/authorization, real hospital connectors, automated backup policy and guaranteed durable cloud storage are not implemented.

Autosave is not a backup. Use SQLite's backup API for a consistent live snapshot; do not blindly copy an active WAL file or delete its WAL/SHM sidecars. Retain private runtime storage outside Git. Inspect [public-demo blockers](DEPLOYMENT.md) before any hosting.
