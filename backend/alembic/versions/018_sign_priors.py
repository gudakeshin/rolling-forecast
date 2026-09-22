"""Add admin-editable sign_priors table, seeded from the discovery defaults.

Revision ID: 018_sign_priors
Revises: 017_memory_archival_heuristics
Create Date: 2026-07-28
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path as _Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

_alembic_dir = str(_Path(__file__).resolve().parents[1])
if _alembic_dir not in sys.path:
    sys.path.insert(0, _alembic_dir)
from migration_helpers import (  # noqa: E402
    create_index_if_missing,
    create_table_if_missing,
    drop_table_if_exists,
    table_exists,
)

revision: str = "018_sign_priors"
down_revision: Union[str, None] = "017_memory_archival_heuristics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Snapshot of app.services.driver_discovery.DEFAULT_SIGN_PRIORS as of this
# revision. Duplicated on purpose: a migration must describe the schema at a
# point in time, not track a moving constant.
SEED_ROWS: list[tuple[str, str, int]] = [
    ("headcount", "expense", 1),
    ("headcount", "revenue", 1),
    ("volume", "revenue", 1),
    ("volume", "expense", 1),
    ("price", "revenue", 1),
    ("rate", "expense", 1),
]


def upgrade() -> None:
    create_table_if_missing(
        "sign_priors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("driver_type", sa.String(length=50), nullable=False),
        sa.Column("line_family", sa.String(length=20), nullable=False),
        sa.Column("expected_sign", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "driver_type", "line_family", name="uq_sign_priors_type_family"
        ),
    )
    create_index_if_missing(
        "ix_sign_priors_driver_type", "sign_priors", ["driver_type"]
    )

    if not table_exists("sign_priors"):  # pragma: no cover — defensive
        return

    bind = op.get_bind()
    existing = {
        (str(t), str(f))
        for t, f in bind.execute(
            sa.text("SELECT driver_type, line_family FROM sign_priors")
        ).fetchall()
    }
    now = datetime.now(timezone.utc)
    pending = [
        {
            "driver_type": driver_type,
            "line_family": family,
            "expected_sign": sign,
            "notes": "Seeded default",
            "updated_by": None,
            "updated_at": now,
        }
        for driver_type, family, sign in SEED_ROWS
        if (driver_type, family) not in existing
    ]
    if pending:
        bind.execute(
            sa.text(
                "INSERT INTO sign_priors "
                "(driver_type, line_family, expected_sign, notes, updated_by, updated_at) "
                "VALUES (:driver_type, :line_family, :expected_sign, :notes, "
                ":updated_by, :updated_at)"
            ),
            pending,
        )


def downgrade() -> None:
    drop_table_if_exists("sign_priors")
