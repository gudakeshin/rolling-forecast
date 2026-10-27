"""Separate platform admins from company admins; stable OIDC identity; scope
organization memory to a company.

`users.is_platform_admin` replaces the old rule that any Role with
`can_admin`/`can_view_all_bus` saw every company -- roles are shared across
companies, so a role granted inside one company's membership reached all of
them (app/services/permissions.py:can_view_all_bus). Backfilled True for
users who *currently* hold such a role, so no existing account loses access
on upgrade; admins should review that list, since new company admins no
longer get cross-company reach.

`users.oidc_issuer`/`oidc_subject` let SSO link on the IdP's stable (iss,
sub) pair instead of email-or-username (app/api/auth.py:resolve_oidc_user).

`memory_blocks` with scope='organization' were stored with owner_id NULL and
injected into every company's chat prompt. They are re-keyed to the company
of the user who last wrote them; rows whose author has no company stay NULL,
which the new read rule treats as unreachable (fail closed).

Revision ID: 030_platform_admin_and_oidc_identity
Revises: 029_heuristic_and_memory_scoping
Create Date: 2026-10-27
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

_alembic_dir = str(_Path(__file__).resolve().parents[1])
if _alembic_dir not in sys.path:
    sys.path.insert(0, _alembic_dir)
from migration_helpers import (  # noqa: E402
    add_column_if_missing,
    create_index_if_missing,
    table_exists,
)

revision: str = "030_platform_admin_and_oidc_identity"
down_revision: Union[str, None] = "029_heuristic_and_memory_scoping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_user_columns() -> None:
    add_column_if_missing(
        "users",
        sa.Column(
            "is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    add_column_if_missing("users", sa.Column("oidc_issuer", sa.String(length=512), nullable=True))
    add_column_if_missing("users", sa.Column("oidc_subject", sa.String(length=255), nullable=True))
    create_index_if_missing(
        "uq_users_oidc_identity", "users", ["oidc_issuer", "oidc_subject"], unique=True
    )


def _backfill_platform_admins() -> None:
    if not table_exists("roles"):
        return
    op.get_bind().execute(
        sa.text(
            """
            UPDATE users SET is_platform_admin = :t
            WHERE role_id IN (
                SELECT id FROM roles
                WHERE can_admin = :t OR can_view_all_bus = :t OR name = 'admin'
            )
            """
        ),
        {"t": True},
    )


def _rekey_organization_memory() -> None:
    if not table_exists("memory_blocks") or not table_exists("business_units"):
        return
    op.get_bind().execute(
        sa.text(
            """
            UPDATE memory_blocks
            SET owner_id = (
                SELECT business_units.company_id
                FROM users JOIN business_units ON business_units.id = users.business_unit_id
                WHERE users.id = memory_blocks.updated_by
            )
            WHERE scope = 'organization' AND owner_id IS NULL
            """
        )
    )


def upgrade() -> None:
    _add_user_columns()
    _backfill_platform_admins()
    _rekey_organization_memory()


def downgrade() -> None:
    # Expand-phase migration; downgrade is a no-op (same convention as 029).
    pass
