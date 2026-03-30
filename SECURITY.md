# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.x     | ✅ Active support  |
| < 1.0   | ❌ End of life     |

Security patches are released as patch versions (e.g. 1.0.1) and backported
to the current minor release only. We do not backport to older minor versions.

---

## Reporting a Vulnerability

**Do not open a public GitHub Issue for security vulnerabilities.**

### Option 1 — GitHub Private Vulnerability Reporting (preferred)

Use GitHub's built-in private reporting:

1. Go to the **Security** tab of this repository
2. Click **"Report a vulnerability"**
3. Fill in the form with as much detail as possible
4. Submit — only maintainers can see this report

GitHub private reporting gives you a CVE assignment path and
a tracked remediation timeline without public disclosure.

### Option 2 — Email

Send an encrypted report to:

**security@your-domain.com**

PGP key fingerprint:
```
XXXX XXXX XXXX XXXX XXXX
XXXX XXXX XXXX XXXX XXXX
```

Download our PGP key: [security-pgp-key.asc](https://your-domain.com/security-pgp-key.asc)

---

## What to Include in Your Report

The more detail you provide, the faster we can triage and fix.
Please include:

- **Description** — what is the vulnerability and what does it affect?
- **Affected component** — which file, endpoint, or module?
- **Steps to reproduce** — a minimal, reproducible example
- **Proof of concept** — code, curl commands, or screenshots if available
- **Impact** — what can an attacker do if they exploit this?
- **Suggested fix** — if you have one (optional but appreciated)
- **Your environment** — OS, Python version, deployment type

---

## Our Commitment to You

| What we commit to              | Timeline            |
|-------------------------------|---------------------|
| Acknowledge your report       | Within 48 hours     |
| Confirm validity / triage     | Within 5 business days |
| Provide a remediation plan    | Within 14 days      |
| Release a patch               | Within 30 days for critical, 90 days for others |
| Credit you in the changelog   | On release (unless you request anonymity) |
| CVE assignment (if applicable)| Coordinated with GitHub Advisory Database |

We will never take legal action against researchers who report
vulnerabilities in good faith and follow this policy.

---

## Responsible Disclosure Policy

We follow a **coordinated disclosure** model:

1. You report privately using one of the methods above
2. We acknowledge and begin investigation within 48 hours
3. We develop and test a fix
4. We agree on a disclosure date (default: 90 days from report)
5. We release the patch and publish a security advisory simultaneously
6. You may publish your own writeup after the patch is live

If we cannot ship a fix within 90 days, we will notify you and
negotiate a short extension. We will not ask for indefinite silence.

### What qualifies for responsible disclosure

- Remote code execution (RCE)
- SQL injection or NoSQL injection
- Authentication bypass
- Privilege escalation
- Sensitive data exposure (PII, credentials, tokens)
- Server-side request forgery (SSRF)
- Insecure deserialization
- Agent prompt injection leading to system compromise
- Arbitrary file read/write via agent Markdown profiles

### What does not qualify

- Vulnerabilities in dependencies we do not control
  (report these upstream; we will update our deps)
- Theoretical attacks with no practical exploit path
- Social engineering of maintainers
- Physical access attacks
- Issues already publicly known

---

## Security Architecture

### Agent Markdown system

The Markdown-driven agent system (`agents/*.md`) is a potential attack
surface. We mitigate this by:

- **Content security policy** in `agent/loader.py`: all `.md` files are
  scanned for URLs, shell directives, and prompt injection phrases
  before being loaded
- **Atomic file writes**: agent files are written via `tempfile` +
  `os.replace()` to prevent partial reads
- **Read-write locks**: Celery workers cannot read a file mid-write
- **Rollback**: if a new `.md` file fails validation, the previous
  known-good manifest is restored automatically

### LLM prompt injection

We treat all user-supplied input as untrusted. Before injection into
LLM system prompts:

- Input is sanitized and length-bounded per `AgentManifest.max_input_tokens`
- No user input is concatenated directly into agent `.md` content
- The `selector.py` confidence threshold prevents low-confidence
  agent selections from executing

### Secrets management

- All secrets are injected via environment variables at runtime
- No secrets appear in source code, logs, or error responses
- The `scripts/check_secrets.py` validator runs at startup and
  fails fast if required vars are missing
- `.env.example` contains only placeholder values

---

## Automated Security Scanning

This repository runs the following automated security checks:

| Tool | What it checks | Schedule |
|------|---------------|----------|
| CodeQL | Static analysis — Python + JS/TS | Every PR + weekly |
| Semgrep | SAST — OWASP Top 10, FastAPI, Celery rules | Every PR |
| Gitleaks | Secret scanning in git history | Every PR |
| pip-audit | Python dependency CVEs | Every PR + weekly |
| Trivy | Filesystem + Docker image CVEs | Every PR + weekly |
| OSV-Scanner | OSV database cross-reference | Every PR |
| npm audit | Frontend dependency CVEs | Every PR |
| pip-licenses | License compliance (no GPL/AGPL) | Weekly |
| Dependabot | Automated dependency updates | Weekly PRs |

All findings are visible in the **Security** tab of this repository.
Critical findings block merges.

---

## Security Contacts

| Role | Contact |
|------|---------|
| Primary security contact | security@your-domain.com |
| GitHub private report | [Report here](../../security/advisories/new) |
| Maintainer (GitHub) | @your-github-username |

For non-security bugs, use [GitHub Issues](../../issues).
For general questions, use [GitHub Discussions](../../discussions).

---

## Hall of Fame

We thank the following researchers for responsible disclosures:

_No reports yet. You could be first._

Researchers who responsibly disclose valid vulnerabilities will be
credited here (with permission) and in the release changelog.

---

## Legal

This responsible disclosure policy is not a bug bounty program.
We do not offer monetary rewards at this time.

We commit not to pursue legal action against researchers who:
- Report vulnerabilities privately before public disclosure
- Do not access, modify, or delete data beyond what is needed to
  demonstrate the vulnerability
- Do not perform denial of service attacks
- Do not social-engineer our team or users
- Act in good faith and with good intent

_Last updated: 2026-03-28_