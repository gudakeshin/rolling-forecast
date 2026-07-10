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
