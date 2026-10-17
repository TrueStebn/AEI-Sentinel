# Local startup and public-demo readiness

## Reproducible local setup

Use Python 3.12 and a new virtual environment. Install `requirements-lock.txt` for the validated dependency set, then run `python -m src.data_loader --expect-original` using that environment. Run `python -m streamlit run app.py --server.address 127.0.0.1` from the repository root. The [README](../README.md) has Windows and macOS/Linux commands.

The bundled six CSVs are sufficient to construct the source; no developer database or sibling checkout is required. Initialization creates runtime parents/schema. The first import produces revision 1; a repeated identical import is idempotent. Use `AEI_DATABASE_PATH` and `AEI_WORKSPACE_DATABASE_PATH` for explicit private writable locations. Keep schema and CSV inputs readable, runtime storage writable by the service account, and backups private. A missing/invalid source or inaccessible storage produces a recoverable startup/import message.

`requirements.txt` contains runtime ranges; `requirements-dev.txt` adds pytest and pypdf; the lock file records the exact tested set. Streamlit 1.66 and Plotly 6.9 are conservative tested minimums for the current layout/chart APIs. No external PDF converter, custom font installation, AI key or hospital connector is required to run the application.

## Assessment: unrestricted Community Cloud demo is blocked

Source-code publication and public application hosting are separate decisions. The repository can be reviewed and run locally without exposing writable services. This version must not be made publicly writable as-is.

| Area | Current behavior | Required before an unrestricted public demo |
|---|---|---|
| Shared CSV writes | Expert local controls can import six files without authentication and modify the primary source seen by other visitors | Server-enforced read-only demo or authenticated/authorized import path; hiding controls is insufficient |
| Primary SQLite | Shared host-side state; serialized writes and revision checks | Define immutable seeded source, isolated visitor targets and concurrent-write policy |
| Replay | Owned per-workspace databases, confined paths, leases and paused recovery | Bound disk/CPU/memory, visitor concurrency and retention; test cleanup without removing active/reference data |
| Restore links | Bearer tokens, hashed at rest, no production account identity | Define expiration/revocation and access model; redact URLs from logs, recordings and public links |
| Restart persistence | Recovery requires the same workspace and Replay files and valid source revision | Persistent external storage or explicit disposable-demo semantics, startup reseeding and honest recovery messaging |
| Files/downloads | Aggregated in-memory exports; runtime files are outside the public candidate | Verify static-serving configuration, file endpoints, logs and errors cannot expose runtime storage or credentials |
| Multiple visitors | Session preferences and Replay are isolated; laboratory imports remain shared | Test quotas, writer contention, conflicting tabs, cross-workspace access and restart behavior under load |
| Network/configuration | Repository defaults bind to 127.0.0.1 | Review platform binding only after write/storage blockers are solved; do not disable XSRF protections |

The provider's HTTPS and hosting controls do not replace application authorization. This assessment is based on the source architecture; no cloud deployment or production penetration test was performed. No major hosting redesign is included in this cleanup.

## Platform facts and references

Community Cloud runs the application from the repository root and requires repository-local dependency/configuration declarations and needed input files. Local SQLite persistence is not guaranteed there. Platform security is a shared responsibility. These facts support the blockers above; the proposed application safeguards are engineering recommendations, not provider guarantees.

- [Streamlit file organization](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization)
- [Streamlit connections and local SQLite persistence](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)
- [Streamlit trust and security](https://docs.streamlit.io/deploy/streamlit-community-cloud/get-started/trust-and-security)
- [Streamlit secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

No secrets file belongs in Git. A future deployment should use platform-managed secrets and a reviewed data/access design. Do not publish clinical or private visitor data as replacement sample inputs.
