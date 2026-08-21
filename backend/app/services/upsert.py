"""Dialect-aware bulk upsert helpers (SQLite + Postgres)."""

from __future__ import annotations

from typing import Any, Callable, Sequence, Type

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database import Base

DEFAULT_CHUNK_SIZE = 1000


def bulk_upsert(
    db: Session,
    model: Type[Base],
    rows: Sequence[dict[str, Any]],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> int:
    """Insert or update ``rows`` on ``conflict_cols``, updating ``update_cols``.

    Uses dialect-specific ``INSERT ... ON CONFLICT DO UPDATE``. Returns the
    number of rows submitted (not necessarily rows changed).
    """
    if not rows:
        return 0

    bind = db.get_bind()
    dialect = bind.dialect.name
    insert_fn: Callable[..., Any]
    if dialect == "postgresql":
        insert_fn = pg_insert
    elif dialect == "sqlite":
        insert_fn = sqlite_insert
    else:
        raise NotImplementedError(
            f"bulk_upsert is not implemented for dialect {dialect!r}"
        )

    table: Any = model.__table__
    total = 0
    for start in range(0, len(rows), chunk_size):
        chunk = list(rows[start : start + chunk_size])
        stmt = insert_fn(table).values(chunk)
        set_map = {col: stmt.excluded[col] for col in update_cols}
        stmt = stmt.on_conflict_do_update(
            index_elements=list(conflict_cols),
            set_=set_map,
        )
        db.execute(stmt)
        total += len(chunk)

    return total
