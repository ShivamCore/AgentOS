"""
Minimal additive schema migration.

``Base.metadata.create_all`` creates missing tables but never adds columns to
existing ones, so an older ``saas_backend.db`` would crash on the new columns.
``ensure_schema`` creates tables and then adds any missing *nullable or
defaulted* columns. It never drops or rewrites data.
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from sqlalchemy import inspect, text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# table -> [(column, DDL type)]
_ADDITIVE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "tasks": [
        ("idempotency_key", "VARCHAR"),
        ("task_input_json", "TEXT"),
        ("constraints_json", "TEXT"),
        ("started_at", "DATETIME"),
        ("completed_at", "DATETIME"),
        ("cancel_requested", "BOOLEAN NOT NULL DEFAULT 0"),
        ("error", "TEXT"),
        ("result_json", "TEXT"),
    ],
    "task_nodes": [
        ("files_modified", "INTEGER NOT NULL DEFAULT 0"),
        ("attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("error", "TEXT"),
        ("dependencies_json", "TEXT"),
    ],
    "logs": [("seq_id", "INTEGER"), ("node_id", "VARCHAR")],
    "file_edits": [("node_id", "VARCHAR")],
}


def ensure_schema(engine: Engine) -> list[str]:
    import backend.models.sql_models  # noqa: F401  (registers tables)

    from backend.db.database import Base

    Base.metadata.create_all(bind=engine)
    added: list[str] = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _ADDITIVE_COLUMNS.items():
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    added.append(f"{table}.{name}")
    if added:
        logger.warning("schema migration added columns: %s", ", ".join(added))
    return added
