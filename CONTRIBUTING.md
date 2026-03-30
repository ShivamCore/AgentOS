# Contributing to AgentOS

Thank you for your interest in contributing! AgentOS is a local-first autonomous AI coding agent — contributions that improve stability, documentation, and developer experience are especially welcome.

---

## Ground Rules

1. **Zero regression** — every PR must pass the full test suite before review.
2. **No surprise refactors** — core execution logic (`agent/`, `backend/workers/`) changes require a design discussion in an Issue first.
3. **Tests required** — new behaviour needs new tests. Bug fixes need a regression test.
4. **One thing per PR** — small, focused PRs merge faster.

---

## Ways to Contribute

| Type | How |
|---|---|
| 🐛 Bug report | Open a GitHub Issue with steps to reproduce |
| 💡 Feature idea | Open a GitHub Discussion or Issue first |
| 📝 Documentation | PRs directly welcome — no Issue needed |
| 🧪 New test | PRs directly welcome |
| 🔧 Bug fix | PR with a linked Issue |
| 🆕 New feature | Issue → discussion → PR |

---

## Development Setup

```bash
# 1. Fork and clone
git clone https://github.com/<your-fork>/agentos
cd agentos/local-coder-agent

# 2. Create a virtual environment
python3.11 -m venv venv
source venv/bin/activate

# 3. Install all dependencies (including dev tools)
make install
# equivalent to: pip install -e ".[dev]" && pre-commit install --install-hooks

# 4. Verify baseline passes
make test-unit
```

---

## Development Workflow

```bash
# Create a branch (never commit directly to main)
git checkout -b fix/describe-your-fix

# Write your code + tests
# ...

# Run linters and type checks before pushing
make lint
make typecheck

# Run the test suite
make test-unit          # fast (< 30s) — run often
make test               # full suite with coverage gate — run before push
make test-regression    # always run if touching agent/ or backend/workers/

# Commit (conventional commit format required)
git commit -m "fix(agent): correct node_callback signature mismatch"
```

### Commit message format

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>

[optional body]

[optional footer: Fixes #issue]
```

**Types:** `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `ci`

**Scope examples:** `agent`, `backend`, `planner`, `memory`, `sandbox`, `frontend`, `ci`, `deps`

---

## Pre-commit Hooks

Pre-commit hooks run automatically on `git commit`. They check:

- **ruff** — lint + import sort
- **black** — code formatting
- **mypy** — type checking
- **bandit** — security scan
- **detect-secrets** — no accidental credential commits
- **check-yaml/toml/json** — file syntax

If a hook fails, fix the issue and re-commit. Hooks auto-fix what they can.

Run all hooks manually:

```bash
pre-commit run --all-files
```

---

## Testing Guidelines

| What changed | Tests to run |
|---|---|
| `agent/` or `backend/workers/` | `make test-unit && make test-regression` |
| `backend/api/` | `make test-unit && make test-integration` |
| New file/function | Add a unit test in `tests/unit/` |
| Bug fix | Add a regression test in `tests/regression/` |
| API contract change | Update `tests/contract/test_api_contract.py` |

Coverage must remain ≥ 90%. The CI will fail if it drops.

---

## Code Style

- **Python 3.11+** — use modern syntax (`match`, `X | Y` unions, etc.)
- **Line length**: 88 characters (black default)
- **Type annotations**: required on all public functions
- **Docstrings**: for complex logic only — clear names over documentation
- Follow the patterns already in the file you're editing

---

## Adding a New Agent

Agents are plain Markdown files in `agents/`. To add one:

1. Copy `agents/coder.md` as a template
2. Fill in `Role`, `Tools`, `System Prompt`, `Constraints`, `Memory`
3. Add a unit test in `tests/unit/` that loads your agent and validates the manifest
4. Run `make test-unit` to confirm it loads correctly

See [docs/agent-format.md](docs/agent-format.md) for the full format specification.

---

## Pull Request Checklist

Before submitting a PR, confirm:

- [ ] `make lint` passes with no warnings
- [ ] `make typecheck` passes (mypy strict)
- [ ] `make test` passes (all tests, ≥90% coverage)
- [ ] New behaviour has new tests
- [ ] Bug fixes have a regression test
- [ ] `ARCHITECTURE.md` updated if your change affects the system design
- [ ] Commit messages follow conventional commit format
- [ ] PR description explains *why*, not just *what*

---

## Review Process

1. Automated CI runs all checks (usually < 5 minutes for unit tests)
2. A maintainer reviews within a few days
3. Address review comments as new commits (don't force-push during review)
4. Once approved and CI is green, a maintainer will merge

---

## Questions?

- **Bug or feature**: open a [GitHub Issue](../../issues)
- **Design question**: open a [GitHub Discussion](../../discussions)
- **Security issue**: see [SECURITY.md](SECURITY.md) — do not open a public issue

---

## Code of Conduct

Be kind, constructive, and patient. We're all here because we find this problem interesting. Harassment of any kind will not be tolerated.
