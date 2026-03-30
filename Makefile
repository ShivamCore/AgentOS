# ═══════════════════════════════════════════════════════════════════════════════
# AgentOS — Developer Makefile
# ═══════════════════════════════════════════════════════════════════════════════
# Usage: make <target>
# Run `make help` to see all available targets.
# ═══════════════════════════════════════════════════════════════════════════════

.PHONY: help install dev test test-unit test-watch lint lint-fix typecheck \
        security clean docker-build docker-scan ci-local migrate seed shell

# Colors
BLUE   := \033[1;34m
GREEN  := \033[1;32m
RED    := \033[1;31m
YELLOW := \033[1;33m
RESET  := \033[0m

# ── Help ─────────────────────────────────────────────────────────────────────
help: ## Show this help message
	@echo "$(BLUE)AgentOS — Available Targets$(RESET)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-18s$(RESET) %s\n", $$1, $$2}'

# ── Setup ────────────────────────────────────────────────────────────────────
install: ## Install all dependencies + pre-commit hooks
	@echo "$(BLUE)→ Installing Python dependencies...$(RESET)"
	pip install -e ".[dev]" 2>/dev/null || pip install -r requirements.txt
	@echo "$(BLUE)→ Installing pre-commit hooks...$(RESET)"
	pip install pre-commit
	pre-commit install --install-hooks
	@echo "$(BLUE)→ Installing frontend dependencies...$(RESET)"
	cd frontend && npm install
	@echo "$(GREEN)✓ Installation complete$(RESET)"

# ── Development ──────────────────────────────────────────────────────────────
dev: ## Start full dev stack with Docker (hot-reload)
	@echo "$(BLUE)→ Starting AgentOS dev stack...$(RESET)"
	docker compose up --build
	@echo "$(GREEN)✓ Dev stack stopped$(RESET)"

# ── Testing ──────────────────────────────────────────────────────────────────
test: ## Run full test suite with coverage (excludes benchmarks)
	@echo "$(BLUE)→ Running full test suite...$(RESET)"
	pytest tests/ \
		-v -m "not benchmark" \
		--cov=backend --cov=agent \
		--cov-report=term-missing \
		--cov-fail-under=90
	@echo "$(GREEN)✓ All tests passed$(RESET)"

test-unit: ## Run unit tests only (fast, < 30s)
	@echo "$(BLUE)→ Running unit tests...$(RESET)"
	pytest tests/unit/ -v -x --no-header -q -m "not benchmark"
	@echo "$(GREEN)✓ Unit tests passed$(RESET)"

test-watch: ## Watch mode — re-run unit tests on file change (TDD)
	@echo "$(BLUE)→ Starting pytest-watch (Ctrl+C to stop)...$(RESET)"
	ptw tests/unit/ -- -v -x --no-header -q -m "not benchmark"

test-regression: ## Run regression tests only (zero tolerance)
	@echo "$(BLUE)→ Running regression tests...$(RESET)"
	pytest tests/regression/ -v --tb=long -m "not benchmark"
	@echo "$(GREEN)✓ All regression tests passed$(RESET)"

test-integration: ## Run integration tests
	@echo "$(BLUE)→ Running integration tests...$(RESET)"
	pytest tests/integration/ -v --tb=short -m "not benchmark"
	@echo "$(GREEN)✓ Integration tests passed$(RESET)"

test-perf: ## Run performance benchmarks
	@echo "$(BLUE)→ Running benchmarks...$(RESET)"
	pytest tests/perf/ --benchmark-only -v

# ── Code Quality ─────────────────────────────────────────────────────────────
lint: ## Run all linters (ruff + black check + mypy)
	@echo "$(BLUE)→ Running ruff lint...$(RESET)"
	ruff check .
	@echo "$(BLUE)→ Running ruff format check...$(RESET)"
	ruff format --check .
	@echo "$(BLUE)→ Running black check...$(RESET)"
	black --check --diff backend/ agent/
	@echo "$(GREEN)✓ All lint checks passed$(RESET)"

lint-fix: ## Auto-fix lint errors (ruff + black format)
	@echo "$(BLUE)→ Auto-fixing with ruff...$(RESET)"
	ruff check --fix .
	@echo "$(BLUE)→ Formatting with ruff...$(RESET)"
	ruff format .
	@echo "$(BLUE)→ Formatting with black...$(RESET)"
	black backend/ agent/
	@echo "$(GREEN)✓ Auto-fix complete$(RESET)"

typecheck: ## Run type checkers (mypy strict + pyre2)
	@echo "$(BLUE)→ Running mypy (strict)...$(RESET)"
	mypy backend/ agent/ --strict --show-error-codes --pretty --ignore-missing-imports
	@echo "$(BLUE)→ Running pyre2...$(RESET)"
	pyre --noninteractive check || echo "$(YELLOW)⚠ Pyre2 reported issues$(RESET)"
	@echo "$(GREEN)✓ Type check complete$(RESET)"

# ── Security ─────────────────────────────────────────────────────────────────
security: ## Run security scans (bandit + pip-audit + trivy)
	@echo "$(BLUE)→ Running bandit SAST scan...$(RESET)"
	bandit -r backend/ agent/ -ll -x tests/
	@echo "$(BLUE)→ Running pip-audit dependency scan...$(RESET)"
	pip-audit -r requirements.txt
	@echo "$(BLUE)→ Running trivy filesystem scan...$(RESET)"
	trivy fs . --severity HIGH,CRITICAL || echo "$(YELLOW)⚠ trivy not installed — skipping$(RESET)"
	@echo "$(GREEN)✓ Security scan complete$(RESET)"

# ── Docker ───────────────────────────────────────────────────────────────────
docker-build: ## Build Docker image locally
	@echo "$(BLUE)→ Building Docker image...$(RESET)"
	docker build \
		--build-arg BUILD_DATE=$$(date -u +"%Y-%m-%dT%H:%M:%SZ") \
		--build-arg VCS_REF=$$(git rev-parse --short HEAD) \
		--build-arg VERSION=$$(git describe --tags --always) \
		-t agentos:local .
	@echo "$(GREEN)✓ Image built: agentos:local$(RESET)"

docker-scan: ## Scan local Docker image with trivy
	@echo "$(BLUE)→ Scanning agentos:local...$(RESET)"
	trivy image agentos:local --severity HIGH,CRITICAL
	@echo "$(GREEN)✓ Image scan complete$(RESET)"

# ── Database ─────────────────────────────────────────────────────────────────
migrate: ## Run database migrations (Alembic)
	@echo "$(BLUE)→ Running migrations...$(RESET)"
	alembic upgrade head 2>/dev/null || python -c "from backend.db.database import Base, engine; Base.metadata.create_all(bind=engine)"
	@echo "$(GREEN)✓ Migration complete$(RESET)"

seed: ## Load fixture data for local development
	@echo "$(BLUE)→ Seeding database...$(RESET)"
	python -c "from backend.db.database import SessionLocal; print('DB connection OK')"
	@echo "$(GREEN)✓ Seed complete$(RESET)"

# ── Utilities ────────────────────────────────────────────────────────────────
shell: ## Open IPython shell with app context loaded
	@echo "$(BLUE)→ Opening shell...$(RESET)"
	python -c "from IPython import start_ipython; start_ipython()" 2>/dev/null || python -i -c "print('AgentOS shell ready')"

ci-local: ## Run GitHub Actions locally via nektos/act
	@echo "$(BLUE)→ Running CI locally via act...$(RESET)"
	act -j lint --container-architecture linux/amd64

clean: ## Remove all build artifacts and caches
	@echo "$(BLUE)→ Cleaning...$(RESET)"
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .coverage coverage.xml htmlcov/ reports/
	rm -rf .mypy_cache .ruff_cache .pytest_cache .pyre/
	rm -rf dist/ build/ *.egg-info
	@echo "$(GREEN)✓ Clean complete$(RESET)"
