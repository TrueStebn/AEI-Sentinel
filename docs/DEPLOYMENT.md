# Local demo baseline and optional restricted hosting

**This guide describes the Phase 15 local-only launcher.** Local Demo Ready: YES after the checks below. This launcher alone does not provide account isolation, connection admission or a kernel memory cap and must not be connected to a public tunnel under the developer account. The owner-approved anonymous ngrok deployment, its completed external checks and different transport/session limits are documented in [Public hosting](PUBLIC_HOSTING.md). Restricted hosting with identity verification remains unconfigured. These local instructions create no external endpoint.

## Operating modes

Developer mode (`AEI_PUBLIC_DEMO=0`, default) retains imports, writable databases, workspaces and historical Replay. Never expose this process. Guided/Expert changes presentation, not permissions.

Public Demo mode (`AEI_PUBLIC_DEMO=1`) rejects every write connection, CSV import and persistent workspace operation below the UI. Reads are restricted to five owner-prepared synthetic snapshots using SQLite `mode=ro&immutable=1` and `query_only`. Initialization validates their schema without creating files. The normal development database is not opened. Revision-watcher Replay polling is disabled.

URL preferences and workspace restore tokens are ignored and cleared. No visitor restore token is consumed or generated. Drafts, results, filters and scenario position are temporary Streamlit session memory. They are not authentication. Browser refresh/restart starts fresh; short reconnects can retain the same session in this local launcher. The bounded public launcher discards supplied reconnect session IDs and uses a zero disconnected-session TTL. Independent visitors do not share query state. Disconnected sessions become eligible for disposal after 20 seconds; that is not a guaranteed deletion deadline.

Public Replay is the **Simulated Surveillance Scenario**, four immutable states (0-3), with visitor-local Next Batch and Reset selections. Step 2 has current 10% at N=40; step 3 has current 40% at N=100 and one original supported alert. It is explicitly artificial. No visitor Replay DB is created. Historical Replay, persistent workspace sharing/restoration/autosave and cleanup are disabled only in public mode.

## Windows preparation, startup and shutdown

Use Python 3.12 and the installed `requirements-lock.txt` environment. From the repository root, replace the Python path:

```powershell
$python = 'C:\path\to\venv\Scripts\python.exe'
$env:AEI_PUBLIC_DEMO = '0'
& $python -m tools.prepare_public_demo
& $python -m tools.check_local_model
.\Start-PublicDemo.ps1 -Python $python -Port 8515
```

Preparation verifies the six bundled CSV checksums and builds a dedicated laboratory snapshot and four scenario snapshots, with hash/size manifest. It never reads/modifies the development DB. It removes only its own exact intermediate filenames and refuses an existing destination. Reuse valid assets. Inspect a failed preparation locally and select a new dedicated directory rather than deleting developer files. `AEI_DEMO_ASSETS` selects the same directory for preparation/runtime (default `data/runtime/public-demo`); five final DBs occupy about 27.8 MB, bounded at 256 MiB. Never modify them while serving.

The PowerShell script refuses a non-loopback Ollama listener or occupied application port. The foreground Python launcher enforces public mode, validates assets/control storage, holds a server OS lock, checks model metadata, and starts only `127.0.0.1:8515`. The supported Streamlit `App` middleware interface rejects upload/static-app routes and unsafe HTTP methods before reading any body. Missing/mismatched AI produces an English message while Manual Search remains usable. No downloads, permanent service, credentials, tunnel or external network configuration are invoked.

If necessary, explicitly install Ollama from its [official Windows distribution](https://ollama.com/download/windows). Use an existing loopback tray service rather than starting another. For a foreground instance owned by you:

```powershell
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
ollama serve
```

Only `qwen3:8b`, Q4_K_M, digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` is accepted. An explicit owner installation can use `ollama pull qwen3:8b`; verify identity afterward. Upstream tag changes fail closed. There is no automatic 4B fallback or weight download. No 14B+ model is used or benchmarked. Metadata checks do not warm/load weights. Submit one short supported question locally to warm the pinned model; a cold-load timeout does not retry automatically. Check residency with `ollama ps` and retain Manual Search as fallback.

In another terminal:

```powershell
Invoke-WebRequest -Uri 'http://127.0.0.1:8515/_stcore/health'
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(8515,11434) }
```

Expect HTTP 200 and loopback only. Then open the app, try AI/manual, open two independent tabs, advance one scenario, verify the other is unchanged, generate PDF, reset, switch Guided/Expert and refresh. Health alone does not verify model/UI behavior.

**Stop with Ctrl+C in the application terminal.** Stop an Ollama instance only if you started it (Ctrl+C in that terminal); otherwise leave the owner's tray service running. Optional `ollama stop qwen3:8b` unloads this model. Never kill unrelated Python processes. Restart reuses snapshots but loses visitor drafts; shared request history remains.

## Resource and storage limits

| Resource | Enforced bound |
|---|---|
| Question | 1-1,200 characters; submit-only; typing/reruns do not infer |
| Prompt | Terminology <=6,144 bytes; whole body <=20,000 bytes; no patient rows |
| Model | 4,096 context tokens; 500 output tokens; response <=64 KiB; existing structured validation |
| Deadline | Default 45 seconds including identity/chat; direct client setting capped at 90; no automatic retry |
| AI | One nonwaiting OS slot shared across workers plus process semaphore; no queue |
| AI budget | Shared rolling 6/minute and 30/hour, retained across new sessions/restarts |
| Session cooldown | Two seconds; usability guard, not identity/abuse protection |
| Analytical reruns | Two nonwaiting OS slots; shared 90/minute and 900/hour including manual/PDF |
| Control storage | Fixed allowlisted files, each <=64 KiB; bounded timestamps; no client IDs, questions or tokens |
| Visitor disk files | No workspace/Replay DB, uploads, disk PDFs or exports |
| Session data | Current query/result and PDF signatures in memory; disconnected TTL 20 seconds |

All workers must share the same `AEI_DEMO_CONTROL` directory (default `data/runtime/demo-control`). Missing/corrupt controls, clock rollback, unexpected files and missing/changed snapshots fail closed. Startup validates, never resets valid budget history. Only inspect/repair controls with all demo workers stopped. OS locks release on process death. A disconnected inference may continue to its deadline; no replacement thread/queue or automatic retry is created. Direct Ollama use by other local applications is outside these limits.

No background application cleanup runs. Streamlit disposes obsolete media/session references in memory. This local launcher has no kernel RAM, session or HTTP admission cap; its AI/rerun budgets do not supply them. It is insufficient for external exposure. The separate [bounded Windows deployment](PUBLIC_HOSTING.md) adds verified account isolation, a worker Job Object and HTTP/WebSocket controls, including established-connection frame limits. Its quotas and availability limits still apply.

For external hosting use a dedicated unprivileged Windows account: read-only source/assets and write access only to fixed control storage; keep developer files/backups/credentials inaccessible. These ACL/account changes are not performed by this local startup script. The separately approved Phase 16 installer and isolated launcher provide and verify them for the public copy. Local OS administrators remain trusted.

## Local launcher Streamlit security review

The launcher verifies actual applied configuration against the locked installed version: CORS and XSRF on, explicit WebSocket Host allowlist, origin allowlist, static app file serving off, uploads zero MB, WebSocket messages 16 MiB, incoming widget state 1 MiB, disconnected TTL 20 seconds, no compression/file watcher, hidden error detail, minimal toolbar, telemetry off, and no trusted user headers. Developer defaults are unchanged. See [Streamlit configuration](https://docs.streamlit.io/develop/api-reference/configuration/config.toml).

Streamlit also permits built-in local/same-origin requests and nonbrowser WebSockets without Origin. CORS/XSRF are not authentication. Host validation applies to WebSockets; HTTP health/index success is not authorization. Framework JS assets remain available; application static serving is disabled. The public HTTP middleware explicitly returns 403 for upload routes, app-static routes and POST/PUT/PATCH/DELETE, without reading bodies; WebSockets and GET/HEAD downloads/health remain available. A real test found that upload size zero alone still accepted empty chunked files (204); the middleware closes that path (403), including valid-session/valid-XSRF requests. No public upload widget is provided; the importer independently denies writes.

Downloads contain only included synthetic aggregates, not private questions/workspaces. Media URLs are not account authorization. Never replace these inputs with real records. The locked server disables Uvicorn access logs; the launcher uses error-level logging without a persistent sink. Console errors remain private. Future proxy logs must redact query strings, cookies and Access tokens and have bounded retention; do not cache personalized downloads. No custom external component or header-based identity is introduced. `pip-audit` checks known dependency advisories; this and unit tests are not penetration testing.

## Optional future Cloudflare setup: owner approval required

Proposed architecture: HTTPS reviewer -> Cloudflare Access -> Tunnel with token validation -> localhost Streamlit. No router forwarding, quick tunnel, SQLite/Ollama route or unrestricted developer server. The checklist below is preparation only; none of these external actions was performed in Phase 15.

1. Approve hostname, named tunnel, reviewer identities, duration and connection/resource limits. Confirm account/plan capability for WebSocket admission and edge protection.
2. Create an Access self-hosted application covering the entire hostname/all paths. Allow only approved emails/identities; keep default deny, no Everyone/Bypass policy. Select IdP or one-time PIN and short sessions. Adding a reviewer means updating this exact allowlist.
3. After Access exists, map a named tunnel hostname solely to `http://127.0.0.1:8515`. Enable **Protect with Access** using the correct team/application audience so the connector validates tokens. Keep credentials outside Git/logs. Do not trust bare `Cf-Access-*` headers in the app. Provider instructions: [Access and origin token validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/), [email PIN login](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/).
4. Start the local script with `-Origin 'https://the-approved-hostname'`. This changes validation settings only; it creates no DNS/tunnel. Preserve Host/Origin and WebSocket upgrades. Keep CORS/XSRF on.
5. Configure and test active connection/session admission, HTTP/download limits, log retention and memory monitoring. If necessary, separately approve a local loopback proxy with admission controls. Do not substitute HTTP-only rate rules for WebSocket limits.
6. After separate approval to connect, use external private browsers to verify unsigned users cannot reach the app, an unapproved identity is denied, and an approved reviewer can use WebSockets, AI/manual, Replay and PDFs. Test forged/expired/wrong-audience tokens, alternate paths/hosts and origin bypass. A PIN-sent message alone is not proof of authorization. Record outcomes without credentials.
7. Verify two approved visitors, reconnect/expiry, host limits and origin outage through real HTTPS. Only then reassess restricted readiness. Real Cloudflare unauthorized denial is **not verified** in this local phase.

To revoke access: stop the connector first, remove the reviewer Allow entry/revoke Access sessions, and stop the public server. Existing sockets may survive a policy edit; stopping the connector/server closes them. Rotate exposed credentials. If unavailable, keep Access closed while checking local health/model, offer manual/offline synthetic reports, and never expose developer mode or disable protections as a workaround.

This optional authenticated architecture has not been configured or externally verified. An obscure URL is not authentication. The current anonymous synthetic demo uses the separately verified [Phase 16 deployment](PUBLIC_HOSTING.md); it does not claim reviewer identity enforcement or production availability. The local launcher and this optional checklist install no service, modify no firewall/router/DNS settings, add no license, and perform no Git commit or push.
