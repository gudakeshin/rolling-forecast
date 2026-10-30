"""Custom KPIs: resolve formula references against a company's chart of
accounts, manage definitions, and evaluate them against a forecast version.

Values come from the version's ForecastLineResult rows -- the published
number per line and period (``override_value`` when overridden, else
``p50``). A category's value is the sum of its non-calculated lines, so a
subtotal line in the same category isn't counted twice. Each KPI is evaluated
per period and once on horizon totals (so a ratio KPI's total is the ratio of
totals, not a sum of ratios).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.kpi_formula import (
    FormulaError,
    Node,
    Ref,
    evaluate,
    parse_formula,
    references,
)
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.kpi import KPI_DISPLAY_FORMATS, KpiDefinition
from app.models.line_item import LineItem
from app.services.audit import record_audit

# A resolved reference: ("item", line_item_id) or ("category", category name).
Resolved = tuple[str, Any]


def _company_items(db: Session, business_unit_id: str) -> list[LineItem]:
    return db.query(LineItem).filter(LineItem.business_unit_id == business_unit_id).all()


def resolve_references(
    refs: list[Ref], items: list[LineItem]
) -> dict[Ref, Resolved]:
    """Map each reference to one line item or category of the company.

    Matching is case-insensitive. A bare ``[name]`` that matches both a line
    item and a category is ambiguous and must be prefixed. Raises FormulaError
    naming every reference that fails."""
    by_name: dict[str, list[LineItem]] = defaultdict(list)
    by_code: dict[str, list[LineItem]] = defaultdict(list)
    categories: dict[str, str] = {}
    for li in items:
        by_name[li.name.strip().lower()].append(li)
        by_code[li.account_code.strip().lower()].append(li)
        categories.setdefault(li.category.strip().lower(), li.category)

    resolved: dict[Ref, Resolved] = {}
    problems: list[str] = []
    for ref in refs:
        key = ref.name.strip().lower()
        item_matches = by_name.get(key) or by_code.get(key) or []
        category = categories.get(key)
        if ref.kind == "item" or (ref.kind == "any" and item_matches and not category):
            if len(item_matches) == 1:
                resolved[ref] = ("item", item_matches[0].id)
            elif len(item_matches) > 1:
                problems.append(f"{ref} matches {len(item_matches)} line items; use its account code")
            else:
                problems.append(f"{ref} is not a line item")
        elif ref.kind == "category" or (ref.kind == "any" and category and not item_matches):
            if category:
                resolved[ref] = ("category", category)
            else:
                problems.append(f"{ref} is not a category")
        elif ref.kind == "any" and item_matches and category:
            problems.append(
                f"{ref} is both a line item and a category; write "
                f"[item:{ref.name}] or [category:{ref.name}]"
            )
        else:
            problems.append(f"{ref} is not a line item or category")
    if problems:
        raise FormulaError("; ".join(problems))
    return resolved


def compile_kpi_formula(
    db: Session, *, business_unit_id: str, formula: str
) -> tuple[Node, dict[Ref, Resolved]]:
    """Parse and resolve a formula for one company. Raises FormulaError."""
    node = parse_formula(formula)
    return node, resolve_references(references(node), _company_items(db, business_unit_id))


def _check_fields(name: str, display_format: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("KPI name must not be blank")
    if len(name) > 120:
        raise ValueError("KPI name exceeds 120 characters")
    if display_format not in KPI_DISPLAY_FORMATS:
        raise ValueError(f"display_format must be one of {list(KPI_DISPLAY_FORMATS)}")
    return name


def _ensure_unique(db: Session, business_unit_id: str, name: str, exclude_id: str | None = None) -> None:
    q = db.query(KpiDefinition).filter(
        KpiDefinition.business_unit_id == business_unit_id,
        func.lower(KpiDefinition.name) == name.lower(),
    )
    if exclude_id:
        q = q.filter(KpiDefinition.id != exclude_id)
    if q.first() is not None:
        raise ValueError(f"A KPI named '{name}' already exists")


def list_kpis(db: Session, *, business_unit_id: str) -> list[KpiDefinition]:
    return (
        db.query(KpiDefinition)
        .filter(KpiDefinition.business_unit_id == business_unit_id)
        .order_by(KpiDefinition.created_at)
        .all()
    )


def create_kpi(
    db: Session,
    *,
    business_unit_id: str,
    name: str,
    formula: str,
    display_format: str = "number",
    description: str | None = None,
    actor: Any,
) -> KpiDefinition:
    """Raises ValueError (incl. FormulaError) for invalid input."""
    name = _check_fields(name, display_format)
    _ensure_unique(db, business_unit_id, name)
    compile_kpi_formula(db, business_unit_id=business_unit_id, formula=formula)
    kpi = KpiDefinition(
        business_unit_id=business_unit_id,
        name=name,
        formula=formula.strip(),
        display_format=display_format,
        description=(description or "").strip() or None,
        created_by=getattr(actor, "id", None),
    )
    db.add(kpi)
    db.flush()
    _audit(db, "kpi.create", kpi, actor)
    return kpi


def update_kpi(db: Session, kpi: KpiDefinition, *, changes: dict[str, Any], actor: Any) -> KpiDefinition:
    """Apply name/formula/display_format/description changes. Raises ValueError."""
    name = _check_fields(changes.get("name", kpi.name), changes.get("display_format", kpi.display_format))
    if name.lower() != kpi.name.lower():
        _ensure_unique(db, kpi.business_unit_id, name, exclude_id=kpi.id)
    if "formula" in changes:
        compile_kpi_formula(db, business_unit_id=kpi.business_unit_id, formula=changes["formula"])
        kpi.formula = changes["formula"].strip()
    kpi.name = name
    kpi.display_format = changes.get("display_format", kpi.display_format)
    if "description" in changes:
        kpi.description = (changes["description"] or "").strip() or None
    db.flush()
    _audit(db, "kpi.update", kpi, actor)
    return kpi


def delete_kpi(db: Session, kpi: KpiDefinition, *, actor: Any) -> None:
    _audit(db, "kpi.delete", kpi, actor)
    db.delete(kpi)
    db.flush()


def _audit(db: Session, action: str, kpi: KpiDefinition, actor: Any) -> None:
    record_audit(
        db,
        action=action,
        entity_type="kpi_definition",
        entity_id=kpi.id,
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={"business_unit_id": kpi.business_unit_id, "name": kpi.name, "formula": kpi.formula},
    )


def evaluate_kpis(db: Session, version: ForecastVersion) -> list[dict[str, Any]]:
    """Every KPI of the version's company, evaluated against that version."""
    if not version.business_unit_id:
        return []
    kpis = list_kpis(db, business_unit_id=version.business_unit_id)
    if not kpis:
        return []

    items = _company_items(db, version.business_unit_id)
    item_by_id = {li.id: li for li in items}
    item_values: dict[int, dict[str, float]] = defaultdict(dict)
    category_values: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    periods: set[str] = set()
    rows = (
        db.query(ForecastLineResult)
        .filter(ForecastLineResult.version_id == version.id)
        .all()
    )
    for row in rows:
        li = item_by_id.get(row.line_item_id)
        if li is None:
            continue
        value = float(row.override_value if row.is_overridden and row.override_value is not None else row.p50)
        item_values[li.id][row.period] = value
        if not li.is_calculated:
            category_values[li.category][row.period] += value
        periods.add(row.period)
    ordered_periods = sorted(periods)

    def ref_value(resolved: Resolved, period: str | None) -> float | None:
        kind, key = resolved
        series = item_values.get(key) if kind == "item" else category_values.get(key)
        if not series:
            return None
        if period is None:
            return sum(series.values())
        return series.get(period)

    out: list[dict[str, Any]] = []
    for kpi in kpis:
        entry: dict[str, Any] = {
            "id": kpi.id,
            "name": kpi.name,
            "formula": kpi.formula,
            "display_format": kpi.display_format,
            "description": kpi.description,
            "total": None,
            "by_period": [],
            "missing": [],
            "error": None,
        }
        try:
            node, resolved = compile_kpi_formula(
                db, business_unit_id=version.business_unit_id, formula=kpi.formula
            )
        except FormulaError as e:
            # e.g. a referenced line item was renamed since the KPI was saved
            entry["error"] = str(e)
            out.append(entry)
            continue
        entry["missing"] = [str(ref) for ref, res in resolved.items() if ref_value(res, None) is None]
        entry["total"] = evaluate(node, {ref: ref_value(res, None) for ref, res in resolved.items()})
        entry["by_period"] = [
            {
                "period": p,
                "value": evaluate(node, {ref: ref_value(res, p) for ref, res in resolved.items()}),
            }
            for p in ordered_periods
        ]
        out.append(entry)
    return out
