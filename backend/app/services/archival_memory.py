"""Archival memory — durable, semantically searchable long-term notes.

Archival memory complements core memory: core memory is a small always-in-prompt
block, archival memory is unbounded and retrieved on demand. Entries are scoped
to a business unit so retrieval never leaks across BUs.

Two backends sit behind :class:`ArchivalStore`:

* ``chroma`` — the real vector store (shares the ChromaDB client with documents).
* ``memory`` — a deterministic in-process store using hashed bag-of-words
  embeddings. Used when ``APP_ENV=test`` so CI needs neither ChromaDB
  persistence nor sentence-transformers model downloads.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.services.audit import record_audit
from app.services.permissions import can_view_all_bus

logger = logging.getLogger(__name__)

VALID_PROVENANCE = frozenset({"agent", "document", "reflection"})
SHARED_BU = ""  # metadata value for entries visible to every business unit

MAX_TEXT_CHARS = 8000
EMBED_DIM = 256

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def hash_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic hashed bag-of-words embedding (no model download needed)."""
    counts = Counter(_TOKEN_RE.findall((text or "").lower()))
    vec = [0.0] * dim
    for token, n in counts.items():
        # Python's str hash is salted per process; use a stable digest instead.
        bucket = int.from_bytes(token.encode("utf-8"), "little") % dim
        vec[bucket] += float(n)
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class ArchivalStore(Protocol):
    """Minimal vector-store surface used by archival memory."""

    def add(self, *, entry_id: str, text: str, metadata: dict[str, Any]) -> None: ...

    def query(
        self,
        *,
        text: str,
        top_k: int,
        business_unit: str | None,
        unrestricted: bool,
    ) -> list[dict[str, Any]]: ...

    def count(self) -> int: ...


@dataclass
class _InMemoryArchivalStore:
    """Deterministic dict-backed store for tests and chroma-less environments."""

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, *, entry_id: str, text: str, metadata: dict[str, Any]) -> None:
        self.entries[entry_id] = {
            "text": text,
            "metadata": dict(metadata),
            "embedding": hash_embedding(text),
        }

    def query(
        self,
        *,
        text: str,
        top_k: int,
        business_unit: str | None,
        unrestricted: bool,
    ) -> list[dict[str, Any]]:
        query_vec = hash_embedding(text)
        allowed = _allowed_bu_values(business_unit)
        hits: list[dict[str, Any]] = []
        for entry_id, row in self.entries.items():
            row_bu = str(row["metadata"].get("business_unit") or SHARED_BU)
            if not unrestricted and row_bu not in allowed:
                continue
            score = _cosine(query_vec, row["embedding"])
            if score <= 0:
                continue
            hits.append(
                {
                    "id": entry_id,
                    "text": row["text"],
                    "score": score,
                    "metadata": dict(row["metadata"]),
                }
            )
        hits.sort(key=lambda h: (-h["score"], h["id"]))
        return hits[:top_k]

    def count(self) -> int:
        return len(self.entries)


class _ChromaArchivalStore:
    """Chroma-backed store using the dedicated archival collection."""

    def _collection(self):
        from app.services.vector_store import ARCHIVAL_COLLECTION_NAME, get_collection

        return get_collection(ARCHIVAL_COLLECTION_NAME)

    def add(self, *, entry_id: str, text: str, metadata: dict[str, Any]) -> None:
        self._collection().add(ids=[entry_id], documents=[text], metadatas=[metadata])

    def query(
        self,
        *,
        text: str,
        top_k: int,
        business_unit: str | None,
        unrestricted: bool,
    ) -> list[dict[str, Any]]:
        collection = self._collection()
        total = collection.count()
        if total == 0:
            return []
        where: dict[str, Any] | None = None
        if not unrestricted:
            where = {"business_unit": {"$in": sorted(_allowed_bu_values(business_unit))}}
        results = collection.query(
            query_texts=[text],
            n_results=min(top_k, total),
            where=where,
        )
        ids = (results.get("ids") or [[]])[0]
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
        out = []
        for i, entry_id in enumerate(ids):
            out.append(
                {
                    "id": entry_id,
                    "text": docs[i] if i < len(docs) else "",
                    "score": 1 - (dists[i] if i < len(dists) else 0.0),
                    "metadata": dict(metas[i]) if i < len(metas) and metas[i] else {},
                }
            )
        return out

    def count(self) -> int:
        return self._collection().count()


_store: ArchivalStore | None = None


def _use_in_memory_store() -> bool:
    return settings.app_env.lower() == "test"


def get_store() -> ArchivalStore:
    """Return the process-wide archival store, choosing a backend by environment."""
    global _store
    if _store is None:
        if _use_in_memory_store():
            _store = _InMemoryArchivalStore()
        else:
            _store = _ChromaArchivalStore()
    return _store


def set_store(store: ArchivalStore | None) -> None:
    """Override the backend (tests inject a fake / reset between cases)."""
    global _store
    _store = store


def reset_store() -> None:
    set_store(None)


def _allowed_bu_values(business_unit: str | None) -> set[str]:
    """BU metadata values a caller in ``business_unit`` may read."""
    allowed = {SHARED_BU}
    if business_unit:
        allowed.add(business_unit)
    return allowed


def _resolve_business_unit(actor: User, business_unit: str | None) -> str:
    """Scope a write to the actor's BU unless they may write across BUs."""
    requested = (business_unit or "").strip()
    actor_bu = (getattr(actor, "business_unit", None) or "").strip()
    if not requested:
        return actor_bu
    if requested == actor_bu or can_view_all_bus(actor):
        return requested
    raise ValueError(
        f"Cannot write archival memory for business unit '{requested}' from '{actor_bu or 'none'}'"
    )


def insert(
    db: Session,
    actor: User,
    text: str,
    *,
    provenance: str,
    business_unit: str | None = None,
) -> dict[str, Any]:
    """Store one archival memory entry and return its descriptor."""
    body = (text or "").strip()
    if not body:
        raise ValueError("Archival memory text is required")
    if len(body) > MAX_TEXT_CHARS:
        raise ValueError(f"Archival memory text exceeds {MAX_TEXT_CHARS} chars")
    if provenance not in VALID_PROVENANCE:
        raise ValueError(
            f"Invalid provenance '{provenance}'; expected one of {sorted(VALID_PROVENANCE)}"
        )

    bu = _resolve_business_unit(actor, business_unit)
    entry_id = str(uuid.uuid4())
    metadata = {
        "provenance": provenance,
        "business_unit": bu,
        "actor_id": actor.id or "",
        "actor_username": actor.username or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    get_store().add(entry_id=entry_id, text=body, metadata=metadata)

    record_audit(
        db,
        action="memory.archival.insert",
        entity_type="archival_memory",
        entity_id=entry_id,
        actor_id=actor.id,
        actor_username=actor.username,
        details={
            "provenance": provenance,
            "business_unit": bu or None,
            "chars": len(body),
        },
        commit=True,
    )
    return {
        "id": entry_id,
        "provenance": provenance,
        "business_unit": bu or None,
        "chars": len(body),
    }


def search(
    actor: User,
    query: str,
    top_k: int = 5,
    *,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over archival memory, restricted to the actor's BU.

    Entries stored without a business unit are shared and always visible.
    ``db`` is optional; when supplied the search is audited.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("Archival memory query is required")
    k = max(1, min(25, int(top_k or 5)))
    actor_bu = (getattr(actor, "business_unit", None) or "").strip() or None
    unrestricted = can_view_all_bus(actor)

    try:
        hits = get_store().query(
            text=q, top_k=k, business_unit=actor_bu, unrestricted=unrestricted
        )
    except Exception as exc:  # pragma: no cover - store availability guard
        logger.warning(f"Archival memory search failed: {exc}")
        hits = []

    if db is not None:
        record_audit(
            db,
            action="memory.archival.search",
            entity_type="archival_memory",
            entity_id=None,
            actor_id=actor.id,
            actor_username=actor.username,
            details={
                "query": q,
                "top_k": k,
                "result_count": len(hits),
                "business_unit": actor_bu,
                "unrestricted": unrestricted,
            },
            commit=True,
        )
    return hits
