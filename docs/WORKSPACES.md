# Workspace persistence, autosave and recovery

AEI Sentinel is a local hackathon prototype using synthetic data. A restore token is a bearer credential for a demonstration workspace. It is not hospital authentication, identity verification, role-based clinical access or encryption.

## Storage and private restore links

The application reuses the existing schema-v1 `user_sessions` and `saved_workspaces` tables in a **separate** `data/runtime/workspaces.db`. The working laboratory database, its revision, import history and source CSVs are not changed by workspace operations. The unused laboratory tables in the workspace file remain empty. No schema migration or new dependency is required.

With a custom `AEI_DATABASE_PATH`, workspace storage defaults to the same parent directory, named `workspaces.db`. Set `AEI_WORKSPACE_DATABASE_PATH` to override it. A workspace path equal to the laboratory database is rejected. Use the same laboratory and workspace paths after restarting the server.

An initial session creates a cryptographically random 32-byte token (256 bits, 43 URL-safe characters). Streamlit retains it in the `workspace` query parameter. SQLite stores its SHA-256 digest as the session/owner identifier; the raw token is not stored in the database. Unknown or malformed tokens display a recovery error and never silently select or create another user's identity.

Open Data & Settings. Keep the complete private workspace URL, or copy **Workspace → Your restore token (copy and keep private)**. Open that URL after closing the browser or restarting Streamlit. Alternatively, open a new session, expand **Workspace**, paste the token in **Restore token**, and press **Restore Workspace**. There is also a recovery form when the URL token is invalid.

Anyone possessing the URL/token can access the associated demo preferences, snapshots and replay state. Do not publish it, include it in screenshots of the address bar, or share it as an ordinary public link. URLs may remain in browser history. Losing both URL and token prevents recovery through this prototype; there is no password reset or account lookup. Local filesystem permissions remain important. **Start New Workspace** creates a new independent identity without deleting the previous one.

## Saved state and autosave

Only an allowlist of workspace fields is stored: ward, organism, antibiotic, specimen, analysis date, follow-latest preference, detailed period, navigation page, data source, signal threshold, per-source filters, last observed laboratory revision and minimal replay descriptors. Dates are ISO strings. UI state is validated on save and load. Laboratory records, patient identifiers, result tables, statistical calculations and arbitrary session-state values are excluded.

Filter/date/period callbacks save immediately; source switching, playback controls and committed replay steps save before rerunning or starting expensive analysis. A final save captures validated defaults and refreshed metadata. Data & Settings shows **Saving…**, **All changes saved.**, or **Unable to save — retry.**; **Workspace** shows **Last Saved** and a retry control when needed. A canonical state comparison skips unchanged saves; ordinary unchanged reruns do not update the workspace timestamp or perform SQL writes.

Each update uses the existing short-lived connection factory and `BEGIN IMMEDIATE`, WAL, `synchronous=FULL`, foreign keys and a bounded busy timeout. State JSON and its revision are updated atomically. A failed save retains the previous committed workspace, leaves current unsaved choices in memory, reports the error, and pauses replay. Retry after storage is available. Laboratory imports and revision refresh remain independent of workspace-save failures.

The saved JSON has an optimistic revision. If two tabs holding the same token change different state from an old revision, the stale save is rejected rather than silently overwriting the newer one. Use **Restore Workspace** with the same token to reload the latest state, then make the intended change. This is conflict protection, not real-user authorization. Different tokens remain separate identities.

## Manual snapshots

Expand **Workspace**:

1. Enter an explicit **Workspace title**, then press **Save Workspace**.
2. **List Saved Workspaces** shows only this identity's snapshots, with creation/update timestamps.
3. Select a **Saved Workspace** and press **Restore Saved Workspace** to apply its preferences.
4. Use **New workspace title → Rename Workspace** to rename it.
5. Check **Confirm deletion of selected saved workspace**, then press **Delete Workspace** to permanently remove that snapshot. This does not delete laboratory records, the active identity or replay data.

All snapshot lookup, rename and delete operations include the authenticated-by-possession owner digest in the SQL predicate. Knowing another snapshot's UUID alone is insufficient. Snapshot state uses the same allowlist and validation as autosave.

Snapshots are preference copies with replay-file references, not database backups or rewind points. Replay databases are append-only. If a referenced generation advanced after a snapshot was taken, restoration verifies its history and uses the latest durable committed batch, while explaining that committed progress was recovered. It never silently rewinds or discards laboratory observations. Snapshot references protect replay files from cleanup.

## Replay recovery

Each replay file receives an owner marker in its existing `user_sessions` table. A descriptor stores mode, relative database location, source revision, chronological packet periods, batch cursor, speed, expected dataset version, last committed batch, completion and whether it had been running. No patient-level observations are copied into the workspace JSON.

On reconnect or server restart, recovery:

1. Validates the descriptor and confines its relative `<uuid>/<uuid>.db` location to the configured `data/runtime/replay/` directory.
2. Rejects traversal, absolute paths, symbolic links/junctions, and hard links to the working database. A missing file is not recreated by a read operation.
3. Uses a read-only connection to verify schema version, SQLite integrity, foreign keys, owner/mode marker and dataset revision.
4. Checks the successful import history for consecutive, recognized replay packet commits. Duplicates may be logged, but cannot add another revision. Unknown imports or inconsistent progress block recovery.
5. Reconciles a database commit that succeeded just before workspace autosave was interrupted. A valid newer committed cursor is recovered and explained; a database older than the saved expected revision is rejected.
6. Restores the controller **paused**, regardless of its previous running flag. The timer never automatically resumes playback after recovery. Press **Start Replay** or **Next Batch** explicitly.

Filters and date controls are restored before widgets are instantiated. Stored values are checked against current catalog options/date bounds. An unavailable custom reporting period is replaced by a valid one with a notice. The latest primary laboratory revision is checked on each full run. Analytics are recalculated from the selected current database; no saved analytical output is shown as current. Existing mixed-revision guards remain in place.

If the replay file is missing, corrupt, foreign-owned or inconsistent, the UI explains the problem and offers **Reset Replay**. It does not show main-database metrics under the replay label or silently restart the demonstration. Reset creates a new owned empty generation; the original laboratory database remains untouched. A historical replay keeps its planned source revision; if the laboratory source changed, reset the replay before continuing with new historical packets.

## Safe manual cleanup

Open **Workspace → Preview abandoned Replay cleanup**. The preview reports eligible file count and bytes. Check **Confirm deletion of abandoned Replay data**, then press **Clean Up Abandoned Replay Data**. The result reports reclaimed space.

Cleanup is intentionally conservative. It considers only this identity's owned replay generations with safe UUID paths. It excludes every file referenced by any current workspace or any saved snapshot, regardless of owner. A short-lived SQLite writer lock protects reference inspection/deletion against concurrent autosave or snapshot creation. Each live replay controller holds an OS lock on its own `.active` sidecar, so active files are protected across local processes, including unsaved generations. OS locks are released when a process exits; unlocked stale sidecars may be removed with an eligible file.

Reset releases the old generation's active lease after a new file is initialized. The old file becomes eligible only after all workspace/snapshot references are gone. Cleanup checks ownership again, avoids links and directory traversal, and only unlinks verified descendant database files and their WAL/SHM/lease companions. It never recursively deletes directories or deletes the working/workspace databases.

**Legacy files without an owner marker and corrupt files whose ownership cannot be proved are retained.** Their active/reference status cannot safely be inferred. They require separate operator inspection; the UI does not relabel or delete them opportunistically. Files referenced by abandoned identities also remain protected. There is no retention policy or identity-expiration service.

## Crash safety, recovery and limits

- A power loss can lose changes not yet committed. SQLite durability reduces risk; it is not a backup system and cannot protect against disk loss or every filesystem/storage failure.
- A process termination during an uncommitted workspace write rolls it back; a separate committed laboratory import remains independent. Retry or reopen the private URL after restarting.
- After an interrupted replay import, retrying a committed packet is idempotent. Recovery checks history before advancing. Missing/corrupt replay files require Reset Replay; no automatic database reconstruction or backup restoration is claimed.
- Keep workspace, replay and laboratory databases on local storage with appropriate permissions. Do not remove SQLite WAL/SHM files from a running application. Snapshot deletion/cleanup are filesystem/database operations, not reversible backups.
- Connected-browser Streamlit timers provide real-time-like refresh, without a background polling loop or browser-close confirmation dialog. Browser closure is not relied on to flush a last-minute save.
- Distinct tokens provide prototype isolation. Sharing a token intentionally shares access; there is no identity verification, expiry/revocation UI, hospital authentication, cloud service, AI feature or PostgreSQL migration.
- Multiple tabs with the same credential can conflict. Stale writes are rejected, and replay is paused on save errors. Restore the latest workspace before continuing. Different identities have independent filters, snapshots and owned replay databases.

## Verification

Tests use disposable SQLite files and cover creation, 256-bit token shape/hash storage, invalid tokens, allowlist validation, autosave/no-op behavior, restart/session recreation, independent identities, concurrent writers/conflicts, snapshot CRUD/confirmation/owner checks, interrupted SQL updates and process exit, replay pause/recovery/commit reconciliation, missing/corrupt/foreign files, version changes, safe cleanup and preservation of laboratory/CSV hashes. Browser checks additionally close and reopen the workspace URL, compare Urology / E. coli / TMP statistics at a pinned date, exercise independent sessions, and restart Streamlit while replay is paused.
