# Security

The threat model, sandbox guarantees, verified and unverified properties, and known limitations are documented in [docs/security.md](docs/security.md).

Summary: generated commands run only in a Docker container with no network, capped CPU/memory/PIDs/output, non-root, all capabilities dropped and a read-only root filesystem. With no Docker, command execution **fails closed**. The API is unauthenticated unless `API_KEY` is set, so bind it to localhost.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting ("Security" tab → "Report a vulnerability") on this repository. Do not open a public issue.
