"""Harden business_unit into a real, enforced company boundary.

Adds a `business_units` registry table and nullable `business_unit_id` FK
columns alongside the existing free-text `business_unit` string on
users/line_items/drivers/driver_inputs/driver_form_configs (expand phase of
an expand->dual-write->contract migration -- the string columns are dropped
in a later, separate migration once nothing reads them), plus new
`business_unit_id` columns on actuals_datasets/forecast_versions/
integration_connections. Backfills a BusinessUnit row per distinct existing
free-text value (any NULL/empty value -- "shared, visible to everyone" under
the old scoping rules -- goes to one "Default" bucket instead, since there is
no shared scope anymore; see app/services/permissions.py).

Also fixes a live cross-company data collision: line_items.account_code was
globally unique, so two different companies uploading the same account code
(e.g. "REV-001") collided onto the same row. Uniqueness becomes
(account_code, business_unit_id).

Also adds actuals_datasets.storage_path (persists the uploaded file's path
so a hard delete can remove it from disk -- see
app/domain/skills/manage_actuals_dataset.py).

Revision ID: 025_business_unit
Revises: 024_actuals_dataset_pin
Create Date: 2026-09-15
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
    unique_constraint_exists,
)

revision: str = "025_business_unit"
down_revision: Union[str, None] = "024_actuals_dataset_pin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_BU_NAME = "Default"

# (table, legacy free-text column) pairs that get a business_unit_id sibling
_BU_TABLES = [
    ("users", "business_unit"),
    ("line_items", "business_unit"),
    ("drivers", "business_unit"),
    ("driver_inputs", "business_unit"),
    ("driver_form_configs", "business_unit"),
]


def _add_business_unit_id_columns() -> None:
    for table, _ in _BU_TABLES:
        add_column_if_missing(
            table,
            sa.Column(
                "business_unit_id",
                sa.String(length=36),
                sa.ForeignKey("business_units.id", name=f"fk_{table}_business_unit_id"),
                nullable=True,
            ),
        )
    add_column_if_missing(
        "actuals_datasets",
        sa.Column(
            "business_unit_id",
            sa.String(length=36),
            sa.ForeignKey("business_units.id", name="fk_actuals_datasets_business_unit_id"),
            nullable=True,
        ),
    )
    add_column_if_missing("actuals_datasets", sa.Column("storage_path", sa.Text(), nullable=True))
    add_column_if_missing(
        "forecast_versions",
        sa.Column(
            "business_unit_id",
            sa.String(length=36),
            sa.ForeignKey("business_units.id", name="fk_forecast_versions_business_unit_id"),
            nullable=True,
        ),
    )
    add_column_if_missing(
        "integration_connections",
        sa.Column(
            "business_unit_id",
            sa.String(length=36),
            sa.ForeignKey("business_units.id", name="fk_integration_connections_business_unit_id"),
            nullable=True,
        ),
    )


def _backfill() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    def _get_or_create_bu(name: str) -> str:
        row = bind.execute(
            sa.text("SELECT id FROM business_units WHERE name = :name"), {"name": name}
        ).first()
        if row:
            return row[0]
        bu_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO business_units (id, name, created_at) VALUES (:id, :name, :created_at)"
            ),
            {"id": bu_id, "name": name, "created_at": now},
        )
        return bu_id

    default_id = _get_or_create_bu(DEFAULT_BU_NAME)

    for table, legacy_col in _BU_TABLES:
        if not table_exists(table):
            continue
        distinct_values = [
            r[0]
            for r in bind.execute(
                sa.text(
                    f"SELECT DISTINCT {legacy_col} FROM {table} "
                    f"WHERE {legacy_col} IS NOT NULL AND {legacy_col} != ''"
                )
            ).fetchall()
        ]
        for value in distinct_values:
            bu_id = _get_or_create_bu(value)
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET business_unit_id = :bu_id "
                    f"WHERE {legacy_col} = :value AND business_unit_id IS NULL"
                ),
                {"bu_id": bu_id, "value": value},
            )
        # Anything left unset (legacy NULL/empty business_unit, i.e. the old
        # "shared" bucket) becomes Default rather than staying NULL.
        bind.execute(
            sa.text(
                f"UPDATE {table} SET business_unit_id = :default_id "
                f"WHERE business_unit_id IS NULL"
            ),
            {"default_id": default_id},
        )

    # actuals_datasets/forecast_versions never had a legacy business_unit
    # string to backfill from (they're new columns) -- without this, every
    # pre-existing dataset/version would end up business_unit_id=NULL and
    # silently vanish from a now company-scoped app. Best-effort: infer each
    # dataset's company from the majority of the line items its actuals
    # records reference (already backfilled above); fall back to Default.
    if table_exists("actuals_datasets") and table_exists("actuals_records"):
        dataset_ids = [
            r[0]
            for r in bind.execute(
                sa.text("SELECT id FROM actuals_datasets WHERE business_unit_id IS NULL")
            ).fetchall()
        ]
        for dataset_id in dataset_ids:
            row = bind.execute(
                sa.text(
                    "SELECT li.business_unit_id, COUNT(*) AS n "
                    "FROM actuals_records ar "
                    "JOIN line_items li ON li.id = ar.line_item_id "
                    "WHERE ar.dataset_id = :dataset_id AND li.business_unit_id IS NOT NULL "
                    "GROUP BY li.business_unit_id ORDER BY n DESC LIMIT 1"
                ),
                {"dataset_id": dataset_id},
            ).first()
            bu_id = row[0] if row else default_id
            bind.execute(
                sa.text("UPDATE actuals_datasets SET business_unit_id = :bu_id WHERE id = :id"),
                {"bu_id": bu_id, "id": dataset_id},
            )

    if table_exists("forecast_versions"):
        version_rows = bind.execute(
            sa.text(
                "SELECT id, actuals_dataset_id FROM forecast_versions "
                "WHERE business_unit_id IS NULL"
            )
        ).fetchall()
        for version_id, dataset_id in version_rows:
            bu_id = None
            if dataset_id and table_exists("actuals_datasets"):
                row = bind.execute(
                    sa.text("SELECT business_unit_id FROM actuals_datasets WHERE id = :id"),
                    {"id": dataset_id},
                ).first()
                bu_id = row[0] if row else None
            bind.execute(
                sa.text("UPDATE forecast_versions SET business_unit_id = :bu_id WHERE id = :id"),
                {"bu_id": bu_id or default_id, "id": version_id},
            )


def _fix_account_code_uniqueness() -> None:
    """account_code was globally unique; make it unique per business unit."""
    if not table_exists("line_items"):
        return
    if unique_constraint_exists("line_items", "uq_line_items_account_bu"):
        return  # a fresh DB created straight from the current ORM model already has it

    bind = op.get_bind()
    old_name = None
    for uc in sa.inspect(bind).get_unique_constraints("line_items"):
        if uc["column_names"] == ["account_code"]:
            old_name = uc["name"] or "uq_line_items_account_code"
            break
    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table(
        "line_items", recreate="always", naming_convention=naming_convention
    ) as batch_op:
        if old_name:
            batch_op.drop_constraint(old_name, type_="unique")
        batch_op.create_unique_constraint(
            "uq_line_items_account_bu", ["account_code", "business_unit_id"]
        )


def upgrade() -> None:
    create_table_if_missing(
        "business_units",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_business_units_name"),
    )
    _add_business_unit_id_columns()
    _backfill()
    _fix_account_code_uniqueness()


def downgrade() -> None:
    # Expand-phase migration; downgrade is a no-op (the legacy string columns
    # are still authoritative until the later contract migration).
    pass
