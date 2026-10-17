# Security and publication boundaries

## Supported use

This repository is a local demonstration using synthetic data. It is not a clinical deployment, production identity system or medical treatment tool. The application currently has no authentication or authorization for shared laboratory imports. Guided/Expert is a presentation preference, not a security boundary.

## Keep private state out of Git

Do not commit environment files, Streamlit secrets, API credentials, runtime SQLite files or WAL/SHM/journals, workspace state, Replay generations/leases, backups, logs, screenshots containing private URLs, or generated reports. The supplied `data/synthetic` CSVs are the audited artificial inputs; private imports belong elsewhere. `.gitignore` and `.gitattributes` enforce the repository's file boundaries, but ignored files can still be deliberately force-added: review the actual candidate list before committing.

Restore URLs contain a random bearer credential. Anyone who possesses one can access that demonstration workspace. The database stores only the token hash, but URLs may remain in browser history, proxy logs or recordings. Do not publish a restore token or URL in an issue, screenshot, demo video or README. Token recovery is not account authentication, expiration or revocation.

## Existing safeguards and their limits

- Imports validate the complete six-file packet and commit atomically; they remain unauthenticated shared writes.
- Analytical readers and PDF generation validate source revisions; this does not provide tenant isolation for the primary laboratory source.
- Replay uses owned confined paths, separate settings, checked history and active-file leases. It does not impose per-visitor storage/CPU quotas or an automatic retention service.
- Workspace fields are allowlisted and use optimistic revision checks. Identity is still token possession, not a verified user.
- Exports are aggregate-only and XML-escaped. Keep the host filesystem, logs, backups and download artifacts private as well.

## Before public hosting

Read [Deployment](docs/DEPLOYMENT.md). Block unauthenticated shared imports on the server side; define data isolation, resource limits, durable storage and token lifecycle; review error/log/file exposure; verify concurrent visitors and restart/recovery behavior. Keep this version bound to localhost until those blockers are addressed. Do not weaken XSRF or network protections to make a demo accessible.

## Reporting a concern

Contact the repository maintainer privately through an agreed channel. Do not attach credentials, restore URLs, private databases or real patient data to a public issue. No active credential was found in the publication candidate during preparation; a future discovery should be removed from the candidate and rotated/revoked before publication. Deleting a committed secret from the latest file alone would not remove it from history.
