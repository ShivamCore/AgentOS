#!/usr/bin/env python3
"""
AgentOS — Safe Database Migration Script
========================================
Adds any missing columns to the existing SQLite database without dropping
or recreating tables (i.e., no data loss).

Run from repo root:
    PYTHONPATH=. python db_migrate.py

Safe to run multiple times — it checks which columns already exist first.
"""

import os
import sys
import sqlite3

DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./saas_backend.db").replace("sqlite:///", "")
if DB_PATH.startswith("."):
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH.lstrip("./"))

print(f"[migrate] Database path: {DB_PATH}")

if not os.path.exists(DB_PATH):
    print("[migrate] DB does not exist yet — creating fresh schema via SQLAlchemy...")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backend.db.database import engine, Base
    from backend.models import sql_models  # noqa: F401 — registers all models
    Base.metadata.create_all(bind=engine)
    print("[migrate] ✅ Fresh schema created. Nothing to migrate.")
    sys.exit(0)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

def columns(table: str) -> set:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}

def add_col(table: str, col: str, col_type: str) -> None:
    existing = columns(table)
    if col in existing:
        print(f"[migrate]   {table}.{col} already exists — skipping.")
        return
    print(f"[migrate]   Adding {table}.{col} ({col_type}) ...")
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    conn.commit()
    print(f"[migrate]   ✅ Added {table}.{col}")

# ── tasks table ───────────────────────────────────────────────────────────────
print("[migrate] Checking 'tasks' table...")
add_col("tasks", "idempotency_key", "TEXT")
add_col("tasks", "task_input_json", "TEXT")
add_col("tasks", "constraints_json", "TEXT")
add_col("tasks", "started_at",      "DATETIME")
add_col("tasks", "completed_at",    "DATETIME")

# ── logs table ────────────────────────────────────────────────────────────────
print("[migrate] Checking 'logs' table...")
add_col("logs", "seq_id",   "INTEGER")
add_col("logs", "node_id",  "TEXT")

# ── task_nodes table ─────────────────────────────────────────────────────────
print("[migrate] Checking 'task_nodes' table...")
add_col("task_nodes", "files_modified", "INTEGER DEFAULT 0")

# ── file_edits table ─────────────────────────────────────────────────────────
print("[migrate] Checking 'file_edits' table...")
add_col("file_edits", "node_id", "TEXT")

conn.close()
print("\n[migrate] ✅ Migration complete. All columns are up to date.")
