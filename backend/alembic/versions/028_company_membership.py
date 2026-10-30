"""Add company_memberships -- the grant list behind real multi-company users.

Today a User has exactly one business_unit_id and one role_id, ever. This
adds CompanyMembership(user_id, company_id, role_id) as the set of
companies a user may switch into and what role they hold in each --
business_unit_id/role_id on User remain "the currently active company +
role," now backed by a membership check when they change (see
app/services/company_membership.py). can_view_all_bus is untouched and
stays a separate all-company bypass for admins.

Backfill: one membership row per existing user, matching their current
business_unit's company (via 027_company's business_units.company_id) and
current role_id -- their existing state becomes their first membership.

Revision ID: 028_company_membership
Revises: 027_company
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
from migration_helpers import create_table_if_missing, table_exists  # noqa: E402

revision: str = "028_company_membership"
down_revision: Union[str, None] = "027_company"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill() -> None:
    if not table_exists("users") or not table_exists("business_units"):
        return
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    rows = bind.execute(
        sa.text(
            "SELECT u.id, bu.company_id, u.role_id "
            "FROM users u "
            "JOIN business_units bu ON bu.id = u.business_unit_id "
            "WHERE u.business_unit_id IS NOT NULL AND bu.company_id IS NOT NULL"
        )
    ).fetchall()
    for user_id, company_id, role_id in rows:
        existing = bind.execute(
            sa.text(
                "SELECT id FROM company_memberships WHERE user_id = :uid AND company_id = :cid"
            ),
            {"uid": user_id, "cid": company_id},
        ).first()
        if existing:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO company_memberships (id, user_id, company_id, role_id, created_at) "
                "VALUES (:id, :user_id, :company_id, :role_id, :created_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "company_id": company_id,
                "role_id": role_id,
                "created_at": now,
            },
        )


def upgrade() -> None:
    create_table_if_missing(
        "company_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "company_id", sa.String(length=36), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "company_id", name="uq_company_memberships_user_company"),
    )
    _backfill()


def downgrade() -> None:
    # Expand-phase migration; downgrade is a no-op (same convention as every
    # migration on this branch).
    pass
