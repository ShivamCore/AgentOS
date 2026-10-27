#!/usr/bin/env python3
"""
AgentOS — additive database migration.

Creates missing tables and adds missing columns without dropping data.
Safe to run repeatedly. The API and worker also run this on startup.

    PYTHONPATH=. python db_migrate.py
"""

from backend.db.database import engine
from backend.db.migrations import ensure_schema

if __name__ == "__main__":
    added = ensure_schema(engine)
    print("[migrate] added: " + (", ".join(added) if added else "nothing (schema up to date)"))
