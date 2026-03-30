# Deployment Runbook

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | Runtime for API and Celery workers |
| Docker | ≥ 24.0 | Multi-arch builds require buildx |
| Redis | ≥ 7.0 | Celery broker + real-time pub/sub |
| Ollama | ≥ 0.1.29 | Local LLM inference (on host or sidecar) |
| Node.js | ≥ 18 | Frontend build only |

---

## GitHub Secrets Required

Configure these in **Settings → Secrets and variables → Actions**:

| Secret | Where used | How to get it |
|---|---|---|
| `GITHUB_TOKEN` | docker-build, deploy-production | Auto-provided by GitHub Actions |
| `CODECOV_TOKEN` | coverage-gate | Sign up at [codecov.io](https://codecov.io), add repo, copy token |
| `SLACK_WEBHOOK_URL` | deploy-staging, deploy-production | Slack → Apps → Incoming Webhooks → Create |
| `STAGING_SSH_KEY` | deploy-staging (if SSH-based) | `ssh-keygen -t ed25519 -C "ci-staging"`, add public key to server |
| `PRODUCTION_SSH_KEY` | deploy-production (if SSH-based) | Same as above, separate key for production |

---

## GitHub Environments Required

Configure these in **Settings → Environments**:

### `staging`
- No protection rules needed
- Triggered automatically on push to `main`

### `production`
- **Required reviewers**: Add at least 1 team member who must approve
- **Deployment branches**: Only allow tags matching `v*.*.*`
- This creates a manual approval gate before production deployment

---

## Local Development

```bash
# 1. Clone and install
git clone https://github.com/youruser/agentos && cd agentos/local-coder-agent
make install

# 2. Configure environment
cp .env.example .env
# Edit .env with your values

# 3. Validate environment
python scripts/check_secrets.py

# 4. Start all services
make dev
# → API: http://localhost:8000
# → Flower: http://localhost:5555
# → Frontend: http://localhost:3000 (run separately: cd frontend && npm run dev)

# 5. Run tests
make test-unit        # Fast unit tests
make test             # Full suite with coverage gate
```

---

## Staging Deployment

Staging deploys automatically when code is merged to `main`:

1. CI runs all 12 jobs (lint → typecheck → security → unit → integration → contract → regression → coverage → docker-build)
2. Docker image is pushed to `ghcr.io/youruser/agentos:sha-{commit}`
3. `deploy-staging` job runs (requires `staging` environment)
4. Smoke tests verify `/health` and `/ready` endpoints
5. Slack notification sent

### Manual staging deploy

```bash
# Build and push image manually
make docker-build
docker tag agentos:local ghcr.io/youruser/agentos:manual
docker push ghcr.io/youruser/agentos:manual

# Deploy to staging server
ssh staging 'docker compose pull && docker compose up -d'
```

---

## Production Deployment

Production deploys on git tags only, with manual approval:

```bash
# 1. Create a release tag
git tag v1.0.0
git push origin v1.0.0

# 2. CI runs full pipeline including docker-build
# 3. deploy-staging runs first (same image)
# 4. deploy-production requires manual approval in GitHub UI
# 5. After approval: image deployed, GitHub Release created, Slack notified
```

### Rollback

```bash
# Roll back to previous image tag
ssh production 'docker compose pull ghcr.io/youruser/agentos:v0.9.0 && docker compose up -d'

# Or via container orchestrator
kubectl rollout undo deployment/agentos-api
```

---

## Health Checks

| Endpoint | Expected | What it checks |
|---|---|---|
| `GET /health` | `200 OK` | App is running |
| `GET /ready` | `200 OK` | DB + Redis connections are live |

---

## Monitoring

| Tool | URL | Purpose |
|---|---|---|
| Flower | `http://localhost:5555` | Celery task queue monitoring |
| Codecov | `https://codecov.io/gh/youruser/agentos` | Test coverage trends |
| GitHub Security | Repo → Security tab | SARIF scan results (bandit + trivy) |
