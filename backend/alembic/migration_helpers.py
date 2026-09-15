"""Shared helpers for Alembic migrations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


def table_exists(name: str) -> bool:
    """Return True if the named table already exists (tolerates create_all drift)."""
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def create_table_if_missing(name: str, *args, **kwargs) -> None:
    """Create a table only when it does not already exist."""
    if not table_exists(name):
        op.create_table(name, *args, **kwargs)


def drop_table_if_exists(name: str) -> None:
    """Drop a table only when it exists."""
    if table_exists(name):
        op.drop_table(name)


def column_exists(table: str, column: str) -> bool:
    """Return True if ``table.column`` exists on the live schema."""
    if not table_exists(table):
        return False
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    return column in cols


def add_column_if_missing(table: str, column: sa.Column) -> None:
    """Add a column when the table exists and the column is absent."""
    if not table_exists(table):
        return
    if column_exists(table, column.name):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(column)


def index_exists(name: str, table: str | None = None) -> bool:
    """Return True if an index with ``name`` exists (optionally scoped to ``table``)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table is not None:
        return any(ix["name"] == name for ix in inspector.get_indexes(table))
    for tname in inspector.get_table_names():
        if any(ix["name"] == name for ix in inspector.get_indexes(tname)):
            return True
    return False


def unique_constraint_exists(table: str, name: str) -> bool:
    """Return True if a unique constraint named ``name`` exists on ``table``."""
    if not table_exists(table):
        return False
    bind = op.get_bind()
    return any(
        uc["name"] == name for uc in sa.inspect(bind).get_unique_constraints(table)
    )


def create_index_if_missing(
    name: str,
    table: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    """Create an index when the table exists and the index name is absent."""
    if not table_exists(table) or index_exists(name, table):
        return
    op.create_index(name, table, columns, unique=unique)


def drop_column_if_exists(table: str, column: str) -> None:
    """Drop a column when present (SQLite-safe via batch)."""
    if not column_exists(table, column):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_column(column)
