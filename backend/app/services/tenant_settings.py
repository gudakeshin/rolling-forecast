"""Per-company overrides of otherwise-global settings.

`app.models.fx.SystemSetting` holds a single global value per key
(reporting_currency, fiscal_calendar) shared by every company today.
`app.models.tenant_setting.BusinessUnitSetting` adds a per-company override
of the same keys, additive on top -- the global row is untouched and stays
the fallback default whenever no company override exists (including for
every caller that doesn't pass a business_unit_id at all, so existing
behavior for anyone not yet company-aware is unchanged).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.fx import SystemSetting
from app.models.tenant_setting import BusinessUnitSetting


def get_tenant_setting(
    db: Session, key: str, *, business_unit_id: str | None = None, default: str | None = None
) -> str | None:
    """Company override, falling back to the global value, falling back to `default`."""
    if business_unit_id:
        row = (
            db.query(BusinessUnitSetting)
            .filter(
                BusinessUnitSetting.business_unit_id == business_unit_id,
                BusinessUnitSetting.key == key,
            )
            .first()
        )
        if row is not None and row.value:
            return row.value
    global_row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if global_row is not None and global_row.value:
        return global_row.value
    return default


def set_tenant_setting(
    db: Session, key: str, value: str, *, business_unit_id: str | None = None
) -> None:
    """Write a company override, or the global default when business_unit_id is None."""
    if business_unit_id:
        row = (
            db.query(BusinessUnitSetting)
            .filter(
                BusinessUnitSetting.business_unit_id == business_unit_id,
                BusinessUnitSetting.key == key,
            )
            .first()
        )
        if row:
            row.value = value
            row.updated_at = datetime.now(timezone.utc)
        else:
            db.add(BusinessUnitSetting(business_unit_id=business_unit_id, key=key, value=value))
        db.flush()
        return

    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))
    db.flush()
