"""Standard P&L chart-of-accounts dependency wiring after ingest."""

from __future__ import annotations

import logging
import re
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.line_item import LineItem, LineItemDependency
from app.services.dependency_graph import DependencyGraphManager

logger = logging.getLogger(__name__)

# Canonical category → calculated rollups
# Maps calculated line category patterns to (sources, relationship)
STANDARD_ROLLUPS: list[dict] = [
    {
        "name_patterns": [r"gross\s*margin", r"^gm$", r"gross\s*profit"],
        "category": "Gross Margin",
        "formula": "Revenue - COGS",
        "sources": [
            {"categories": ["Revenue"], "relationship": "sum"},
            {"categories": ["COGS", "Cost of Goods Sold", "Cost of Sales"], "relationship": "subtract"},
        ],
    },
    {
        "name_patterns": [r"operating\s*income", r"ebit(?!da)", r"operating\s*profit"],
        "category": "Operating Income",
        "formula": "Gross Margin - OpEx",
        "sources": [
            {"categories": ["Gross Margin", "Gross Profit"], "relationship": "sum"},
            {"categories": ["OpEx", "Operating Expense", "Operating Expenses", "SG&A"], "relationship": "subtract"},
        ],
    },
    {
        "name_patterns": [r"ebitda"],
        "category": "EBITDA",
        "formula": "Operating Income + D&A",
        "sources": [
            {"categories": ["Operating Income", "EBIT", "Operating Profit"], "relationship": "sum"},
            {"categories": ["D&A", "Depreciation", "Amortization", "Depreciation & Amortization"], "relationship": "sum"},
        ],
    },
    {
        "name_patterns": [r"net\s*income", r"^ni$", r"net\s*profit"],
        "category": "Net Income",
        "formula": "EBITDA - Interest - Tax",
        "sources": [
            {"categories": ["EBITDA", "Operating Income", "EBIT"], "relationship": "sum"},
            {"categories": ["Interest", "Interest Expense"], "relationship": "subtract"},
            {"categories": ["Tax", "Taxes", "Income Tax"], "relationship": "subtract"},
        ],
    },
]


def _matches(item: LineItem, patterns: Iterable[str], categories: Iterable[str] | None = None) -> bool:
    name = (item.name or "").lower()
    cat = (item.category or "").lower()
    for pat in patterns:
        if re.search(pat, name, re.IGNORECASE):
            return True
    if categories:
        for c in categories:
            if cat == c.lower() or c.lower() in cat:
                return True
    return False


def _find_items(items: list[LineItem], categories: list[str]) -> list[LineItem]:
    found = []
    for item in items:
        cat = (item.category or "").lower()
        name = (item.name or "").lower()
        for c in categories:
            cl = c.lower()
            if cat == cl or cl in cat or cl in name:
                found.append(item)
                break
    return found


def ensure_standard_dependencies(db: Session, line_items: list[LineItem] | None = None) -> int:
    """
    Wire standard P&L rollup dependencies for calculated lines.
    Returns number of new dependency edges created.
    """
    items = line_items or db.query(LineItem).all()
    if not items:
        return 0

    existing = {
        (d.dependent_item_id, d.source_item_id)
        for d in db.query(LineItemDependency).all()
    }
    dag = DependencyGraphManager(db)
    created = 0

    for rollup in STANDARD_ROLLUPS:
        dependents = [
            li for li in items
            if _matches(li, rollup["name_patterns"], [rollup["category"]])
        ]
        if not dependents:
            # Create a calculated placeholder if we have the source categories
            continue

        for dependent in dependents:
            dependent.is_calculated = True
            if not dependent.formula:
                dependent.formula = rollup["formula"]

            for source_spec in rollup["sources"]:
                sources = _find_items(items, source_spec["categories"])
                # Prefer leaf (non-calculated) sources when available
                leaf = [s for s in sources if not s.is_calculated and s.id != dependent.id]
                use = leaf or [s for s in sources if s.id != dependent.id]
                for source in use:
                    key = (dependent.id, source.id)
                    if key in existing:
                        continue
                    ok, msg = dag.add_dependency(
                        dependent.id,
                        source.id,
                        relationship_type=source_spec["relationship"],
                    )
                    if ok:
                        existing.add(key)
                        created += 1
                    else:
                        logger.warning("Skip dependency %s -> %s: %s", source.id, dependent.id, msg)

    # Heuristic: if we have Revenue + COGS but no Gross Margin calculated line,
    # mark any "Gross Margin" category item as calculated even without name match
    revenue = _find_items(items, ["Revenue"])
    cogs = _find_items(items, ["COGS", "Cost of Goods Sold", "Cost of Sales"])
    gm_items = [li for li in items if (li.category or "").lower() in ("gross margin", "gross profit")]
    for gm in gm_items:
        gm.is_calculated = True
        gm.formula = gm.formula or "Revenue - COGS"
        for src in revenue:
            key = (gm.id, src.id)
            if key not in existing:
                ok, _ = dag.add_dependency(gm.id, src.id, "sum")
                if ok:
                    existing.add(key)
                    created += 1
        for src in cogs:
            key = (gm.id, src.id)
            if key not in existing:
                ok, _ = dag.add_dependency(gm.id, src.id, "subtract")
                if ok:
                    existing.add(key)
                    created += 1

    db.flush()
    logger.info("CoA dependencies: created %s new edges", created)
    return created
