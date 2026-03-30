#!/usr/bin/env python3
"""
AgentOS — Startup Environment Validator
========================================
Validates that all required environment variables are set before
the application starts. Called during FastAPI lifespan startup.

Usage:
    python scripts/check_secrets.py          # standalone check
    from scripts.check_secrets import validate_env  # import in lifespan

Exit codes:
    0 — all required vars present
    1 — one or more required vars missing
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# ── Color codes for terminal output ─────────────────────────────────────────
RED = "\033[1;31m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[1;34m"
RESET = "\033[0m"

# ── Variable definitions ────────────────────────────────────────────────────
# Format: (name, required: bool, description)
EXPECTED_VARS: list[tuple[str, bool, str]] = [
    # Database & Infrastructure
    ("REDIS_URL", True, "Redis connection URL for Celery broker"),
    ("DATABASE_URL", False, "SQLAlchemy database connection string"),
    # Application
    ("WORKSPACE_DIR", False, "Directory for sandboxed workspaces"),
    ("ALLOWED_ORIGINS", False, "Comma-separated CORS origins"),
    ("MAX_WORKERS", False, "Concurrent agent threads per worker"),
    ("MAX_CONCURRENT_TASKS", False, "Backpressure task limit"),
    ("TASK_TIMEOUT_SECONDS", False, "Hard kill timeout per task"),
    ("RATE_LIMIT_RPM", False, "API rate limit (requests/minute)"),
    # LLM
    ("OLLAMA_URL", False, "Ollama generate API endpoint"),
    ("OLLAMA_MODEL", False, "Default LLM model name"),
    ("OLLAMA_NUM_CTX", False, "Context window size in tokens"),
    # Observability
    ("LOG_LEVEL", False, "Application log level"),
]


def _parse_env_example(env_example_path: Path) -> list[str]:
    """
    Parse .env.example to discover all defined variable names.
    Returns a list of variable names (ignores comments and blank lines).
    """
    if not env_example_path.exists():
        return []

    var_names: list[str] = []
    var_pattern = re.compile(r"^([A-Z][A-Z0-9_]+)\s*=")

    for line in env_example_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = var_pattern.match(line)
        if match:
            var_names.append(match.group(1))

    return var_names


def validate_env(
    strict: bool = False,
    env_example_path: Path | None = None,
) -> tuple[bool, list[str], list[str]]:
    """
    Validate the current environment against expected variables.

    Args:
        strict: If True, treat all vars from .env.example as required.
        env_example_path: Path to .env.example (auto-discovered if None).

    Returns:
        (all_ok, missing_required, missing_optional)
    """
    missing_required: list[str] = []
    missing_optional: list[str] = []

    # Check hardcoded required vars
    for name, required, desc in EXPECTED_VARS:
        value = os.environ.get(name)
        if value is None or value.strip() == "":
            if required:
                missing_required.append(name)
            else:
                missing_optional.append(name)

    # Cross-check against .env.example if available
    if env_example_path is None:
        project_root = Path(__file__).parent.parent
        env_example_path = project_root / ".env.example"

    example_vars = _parse_env_example(env_example_path)
    known_names = {name for name, _, _ in EXPECTED_VARS}

    for var in example_vars:
        if var not in known_names:
            value = os.environ.get(var)
            if value is None or value.strip() == "":
                if strict:
                    missing_required.append(var)
                else:
                    missing_optional.append(var)

    all_ok = len(missing_required) == 0
    return all_ok, missing_required, missing_optional


def main() -> int:
    """Run the environment check and print results."""
    print(f"{BLUE}═══ AgentOS Environment Check ═══{RESET}\n")

    all_ok, missing_required, missing_optional = validate_env()

    # Report required
    if missing_required:
        print(f"{RED}✗ MISSING REQUIRED variables:{RESET}")
        for var in missing_required:
            desc = next((d for n, _, d in EXPECTED_VARS if n == var), "See .env.example")
            print(f"  {RED}• {var}{RESET} — {desc}")
        print()
    else:
        print(f"{GREEN}✓ All required variables are set{RESET}")

    # Report optional
    if missing_optional:
        print(f"\n{YELLOW}⚠ Optional variables not set (defaults will be used):{RESET}")
        for var in missing_optional:
            desc = next((d for n, _, d in EXPECTED_VARS if n == var), "See .env.example")
            print(f"  {YELLOW}• {var}{RESET} — {desc}")

    # Security warnings
    print(f"\n{BLUE}── Security Checks ──{RESET}")

    origins = os.environ.get("ALLOWED_ORIGINS", "")
    if "*" in origins:
        print(f"  {RED}✗ ALLOWED_ORIGINS contains '*' — wildcard CORS is a security risk{RESET}")
    else:
        print(f"  {GREEN}✓ CORS origins are restricted{RESET}")

    db_url = os.environ.get("DATABASE_URL", "")
    if "password" in db_url.lower() and "localhost" not in db_url:
        print(f"  {YELLOW}⚠ DATABASE_URL appears to contain credentials for a remote host{RESET}")

    flower_pw = os.environ.get("FLOWER_PASSWORD", "changeme")
    if flower_pw == "changeme":
        print(f"  {YELLOW}⚠ FLOWER_PASSWORD is still the default — change before exposing{RESET}")
    else:
        print(f"  {GREEN}✓ FLOWER_PASSWORD has been changed{RESET}")

    # Final result
    print()
    if all_ok:
        print(f"{GREEN}═══ Environment OK — ready to start ═══{RESET}")
        return 0
    else:
        print(f"{RED}═══ FAILED — fix missing required variables before starting ═══{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
