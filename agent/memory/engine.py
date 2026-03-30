"""
AgentOS — Semantic Memory Engine
==================================
Persistent vector storage backed by ChromaDB with a graceful no-op fallback.
Thread-safe per workspace_dir via a module-level LRU cache.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False
    logger.warning(
        "chromadb not installed — MemoryEngine running in no-op mode. "
        "Install with: pip install chromadb"
    )


@dataclass(slots=True)
class MemoryResult:
    """A single semantic memory retrieval result."""

    id: str
    document: str
    metadata: dict[str, Any]
    distance: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document": self.document,
            "metadata": self.metadata,
            "distance": round(self.distance, 4),
        }


class LocalMemoryEngine:
    """
    Persistent vector database backed by ChromaDB (cosine similarity).
    Degrades gracefully to a no-op when ChromaDB is unavailable.
    """

    def __init__(self, workspace_dir: str) -> None:
        self.workspace_dir = workspace_dir
        self._collection = None

        if not _CHROMA_AVAILABLE:
            return

        db_path = str(__import__("pathlib").Path(workspace_dir) / ".memory")
        try:
            client = chromadb.PersistentClient(path=db_path)
            self._collection = client.get_or_create_collection(
                name="agent_memory",
                metadata={"hnsw:space": "cosine"},
            )
        except Exception:
            logger.exception(
                "ChromaDB client failed to initialise at %s — running in no-op mode.",
                db_path,
            )

    # ── Write ───────────────────────────────────────────────────────────────────

    def store_memory(
        self,
        agent_id: str,
        mem_type: str,
        content: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Embed and persist a memory entry. Returns True on success."""
        if self._collection is None:
            return False

        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        meta: dict[str, Any] = {
            "agent_id": agent_id,
            "type": mem_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if extra_metadata:
            for k, v in extra_metadata.items():
                # ChromaDB metadata values must be scalar types
                meta[k] = v if isinstance(v, (str, int, float, bool)) else json.dumps(v)

        try:
            self._collection.add(
                ids=[mem_id],
                documents=[content],
                metadatas=[meta],
            )
            return True
        except Exception:
            logger.exception("Failed to store memory entry '%s'", mem_id)
            return False

    # ── Read ────────────────────────────────────────────────────────────────────

    def search_memory(
        self,
        query: str,
        agent_id: str | None = None,
        mem_type: str | None = None,
        limit: int = 5,
    ) -> list[MemoryResult]:
        """Retrieve top-k semantically similar memories. Returns [] on any failure."""
        if self._collection is None:
            return []

        where: dict[str, Any] = {}
        if agent_id:
            where["agent_id"] = agent_id
        if mem_type:
            where["type"] = mem_type

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=limit,
                where=where or None,
            )

            docs = results.get("documents", [[]])[0]
            ids = results.get("ids", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]

            return [
                MemoryResult(
                    id=ids[i],
                    document=docs[i],
                    metadata=metas[i],
                    distance=dists[i] if dists else 0.0,
                )
                for i in range(len(docs))
            ]
        except Exception:
            logger.exception("Memory search failed for query: %.80s", query)
            return []


# ── Module-level factory ────────────────────────────────────────────────────────
# LRU-cached so each unique workspace_dir gets exactly one engine instance.
# Safe for multi-threaded use: lru_cache is thread-safe in CPython.

@lru_cache(maxsize=8)
def get_memory_engine(workspace_dir: str) -> LocalMemoryEngine:
    """Return (or create) the memory engine for *workspace_dir*."""
    return LocalMemoryEngine(workspace_dir)
