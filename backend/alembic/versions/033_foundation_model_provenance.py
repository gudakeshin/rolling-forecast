"""Foundation-model provenance on forecast versions (Phase C, Chronos-2).

Stores the model id, pinned weight revision and fm-service version that a
version's foundation-model forecasts came from, next to ``input_hash``, so a
published number can be traced to the exact weights that produced it.

Revision ID: 033_foundation_model_provenance
Revises: 032_kpi_definitions
Create Date: 2026-11-25
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path
from typing import Sequence, Union

import sqlalchemy as sa

_alembic_dir = str(_Path(__file__).resolve().parents[1])
if _alembic_dir not in sys.path:
    sys.path.insert(0, _alembic_dir)
from migration_helpers import add_column_if_missing, drop_column_if_exists  # noqa: E402

revision: str = "033_foundation_model_provenance"
down_revision: Union[str, None] = "032_kpi_definitions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "forecast_versions",
        sa.Column("foundation_model_provenance", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    drop_column_if_exists("forecast_versions", "foundation_model_provenance")
