"""Whole-version forecast export (CSV / XLSX).

The forecast table pages its rows, so its client-side export only ever held
one page. This builds every line x period row of a version on the server,
company-scoped like the table itself.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy.orm import Session

from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.line_item import LineItem
from app.models.user import User
from app.services.permissions import line_item_scope_filter

EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("account_code", "Account code"),
    ("line_item", "Line item"),
    ("category", "Category"),
    ("period", "Period"),
    ("p10", "P10"),
    ("p50", "P50"),
    ("p90", "P90"),
    ("model_p50", "Model P50"),
    ("is_overridden", "Overridden"),
    ("override_value", "Override value"),
    ("published_value", "Published value"),
    ("model_type", "Model"),
    ("confidence_level", "Confidence"),
    ("confidence_score", "Confidence score"),
    ("bounds_method", "Bounds method"),
    ("review_status", "Review status"),
]


def export_rows(
    db: Session,
    version: ForecastVersion,
    user: User,
    *,
    category: str | None = None,
    confidence_level: str | None = None,
) -> list[dict[str, Any]]:
    query = (
        db.query(ForecastLineResult, LineItem)
        .join(LineItem, LineItem.id == ForecastLineResult.line_item_id)
        .filter(ForecastLineResult.version_id == version.id)
    )
    query = line_item_scope_filter(query, user, LineItem)
    if category:
        query = query.filter(LineItem.category == category)
    if confidence_level:
        query = query.filter(ForecastLineResult.confidence_level == confidence_level)
    out = []
    for r, li in query.order_by(
        LineItem.display_order, LineItem.account_code, ForecastLineResult.period
    ).all():
        published = r.override_value if r.is_overridden and r.override_value is not None else r.p50
        out.append({
            "account_code": li.account_code,
            "line_item": li.name,
            "category": li.category,
            "period": r.period,
            "p10": r.p10,
            "p50": r.p50,
            "p90": r.p90,
            "model_p50": r.model_p50,
            "is_overridden": bool(r.is_overridden),
            "override_value": r.override_value,
            "published_value": published,
            "model_type": r.model_type,
            "confidence_level": r.confidence_level,
            "confidence_score": r.confidence_score,
            "bounds_method": r.bounds_method,
            "review_status": r.review_status,
        })
    return out


def _safe_cell(value: Any) -> Any:
    """Neutralize spreadsheet formula injection in free-text cells (a line item
    named "=HYPERLINK(...)" must not execute when the file is opened)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in EXPORT_COLUMNS])
    for row in rows:
        writer.writerow(["" if row[k] is None else _safe_cell(row[k]) for k, _ in EXPORT_COLUMNS])
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel reads UTF-8


def to_xlsx(rows: list[dict[str, Any]], *, sheet_title: str) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_title or "Forecast")[:31]
    ws.append([label for _, label in EXPORT_COLUMNS])
    for row in rows:
        ws.append([_safe_cell(row[k]) for k, _ in EXPORT_COLUMNS])
    ws.freeze_panes = "C2"  # keep account code + line item visible
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
