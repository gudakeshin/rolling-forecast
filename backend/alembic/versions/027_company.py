"""Introduce Company as the real tenant root; BusinessUnit becomes its child.

BusinessUnit has been doing double duty as both "the tenant/company
boundary" (every scoping check keys off business_unit_id) and, per the UI's
own suggestion chips ("Collect driver inputs from BUs"), the domain's actual
business-unit concept *within* a company. This migration adds a `companies`
table and a nullable `business_units.company_id` FK, backfilling **one
Company per existing BusinessUnit** (1:1) -- today's rows ("Carl Zeiss
Test", "Carl Zeiss India", "North America", ...) are each already a
distinct real tenant, not business units of a shared company, so 1:1 is the
only backfill that doesn't silently merge unrelated companies' scoping.

Schema-only: no enforcement logic changes. business_unit_id remains the
sole enforced tenant boundary everywhere (see app/services/permissions.py) --
this just gives the domain model the vocabulary a later pass needs to build
real multi-BU-per-company support without a bigger migration then.

Revision ID: 027_company
Revises: 026_scoping_holes_2b
Create Date: 2026-09-28
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path as _Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

_alembic_dir = str(_Path(__file__).resolve().parents[1])
if _alembic_dir not in sys.path:
    sys.path.insert(0, _alembic_dir)
from migration_helpers import (  # noqa: E402
    add_column_if_missing,
    create_table_if_missing,
    table_exists,
)

revision: str = "027_company"
down_revision: Union[str, None] = "026_scoping_holes_2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill() -> None:
    """One Company per existing BusinessUnit, matching name."""
    if not table_exists("business_units"):
        return
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    rows = bind.execute(
        sa.text("SELECT id, name FROM business_units WHERE company_id IS NULL")
    ).fetchall()
    for bu_id, name in rows:
        existing = bind.execute(
            sa.text("SELECT id FROM companies WHERE name = :name"), {"name": name}
        ).first()
        if existing:
            company_id = existing[0]
        else:
            company_id = str(uuid.uuid4())
            bind.execute(
                sa.text(
                    "INSERT INTO companies (id, name, created_at) VALUES (:id, :name, :created_at)"
                ),
                {"id": company_id, "name": name, "created_at": now},
            )
        bind.execute(
            sa.text("UPDATE business_units SET company_id = :company_id WHERE id = :id"),
            {"company_id": company_id, "id": bu_id},
        )


def upgrade() -> None:
    create_table_if_missing(
        "companies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_companies_name"),
    )
    add_column_if_missing(
        "business_units",
        sa.Column(
            "company_id",
            sa.String(length=36),
            sa.ForeignKey("companies.id", name="fk_business_units_company_id"),
            nullable=True,
        ),
    )
    _backfill()


def downgrade() -> None:
    # Expand-phase migration; downgrade is a no-op (same convention as every
    # migration on this branch -- nothing downstream reads company_id unless
    # this migration has run, so there's nothing to unwind).
    pass
