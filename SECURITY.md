# Security and publication boundaries

## Supported use

This repository is a local demonstration using synthetic data, not a clinical deployment or medical treatment tool. Developer mode has unrestricted local imports and bearer workspaces. Public Demo Mode disables those paths and uses only verified immutable synthetic snapshots. Guided/Expert is presentation, not authorization. The owner-approved anonymous ngrok demo runs under a dedicated standard Windows account with bounded resources and admission controls; see [Public hosting](docs/PUBLIC_HOSTING.md). It intentionally requires no visitor identity. An authenticated restricted deployment would need separate configuration and verification.

## Keep private state out of Git

Do not commit environment files, Streamlit secrets, API credentials, runtime SQLite files or WAL/SHM/journals, workspace state, Replay generations/leases, backups, logs, screenshots containing private URLs, or generated reports. The supplied `data/synthetic` CSVs are the audited artificial inputs; private imports belong elsewhere. `.gitignore` and `.gitattributes` enforce the repository's file boundaries, but ignored files can still be deliberately force-added: review the actual candidate list before committing.

Restore URLs contain a random bearer credential. Anyone who possesses one can access that demonstration workspace. The database stores only the token hash, but URLs may remain in browser history, proxy logs or recordings. Do not publish a restore token or URL in an issue, screenshot, demo video or README. Token recovery is not account authentication, expiration or revocation.

## Existing safeguards and their limits

- Imports validate complete packets atomically in developer mode. Public mode rejects every import before reading files, rejects all writable SQLite connections, and permits reads only from five prepared hash-verified synthetic snapshots with `mode=ro&immutable=1` and `query_only`.
- Analytical readers and PDF generation validate source revisions; this does not provide tenant isolation for the primary laboratory source.
- Developer Replay retains owned paths/history/leases. Public Replay uses four predefined read-only states with a visitor-local cursor (0-3), never creating a visitor database. Historical Replay and persistent cleanup are disabled there.
- Developer workspaces use allowlisted state and optimistic revisions; token possession is not verified identity. Public mode rejects workspace construction/restoration, ignores URL preferences/tokens and uses disposable session memory.
- Exports are aggregate-only and XML-escaped. Keep the host filesystem, logs, backups and download artifacts private as well.
- Smart Search sends terminology only to localhost Ollama, verifies pinned Qwen3 8B digest/Q4_K_M and validates untrusted structured output. It executes no model SQL/tools. Public AI has a nonwaiting cross-process lock and shared 6/minute, 30/hour budget; browsing has two slots and 90/minute, 900/hour. All workers must share control storage. Per-session cooldown is not abuse protection. The Phase 15 local launcher alone does not bound incoming sessions or total RAM. The Phase 16 public copy adds a four-WebSocket admission limit, frame/request budgets and Windows Job Object caps. Ollama, GPU memory and supervisor overhead remain outside the application worker cap.
- The public launcher preserves CORS/XSRF, validates WebSocket Hosts/Origins, disables uploads/static app files, hides error details and disables access logs. Public visitor reports/exports remain in memory; no background disk cleanup is introduced. Host administrators and local console logs remain trusted/private.
