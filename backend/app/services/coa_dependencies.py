"""Standard P&L chart-of-accounts dependency wiring after ingest.

Formula references support:
  - Bracketed names: ``[Deferred Revenue] - [COGS]``
  - Whole-token / longest-match unbracketed names (avoids ``Revenue`` matching
    inside ``Deferred Revenue``)
  - BU-scoped wiring: sources must share the dependent's business_unit
    (or be shared NULL-BU lines)
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.line_item import LineItem, LineItemDependency
from app.services.dependency_graph import DependencyGraphManager

logger = logging.getLogger(__name__)

# Canonical category → calculated rollups
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

_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_TOKEN_SPLIT_RE = re.compile(r"\s*([+\-*/()])\s*")


class FormulaAmbiguityError(ValueError):
    """Raised when a formula token matches multiple line items in scope."""


def _bu_compatible(dependent: LineItem, source: LineItem) -> bool:
    """Same BU, or source is shared (NULL BU)."""
    dep_bu = dependent.business_unit
    src_bu = source.business_unit
    if src_bu is None:
        return True
    if dep_bu is None:
        return True
    return src_bu == dep_bu


def _scoped_items(items: list[LineItem], dependent: LineItem) -> list[LineItem]:
    return [li for li in items if li.id != dependent.id and _bu_compatible(dependent, li)]


def resolve_name_token(
    token: str,
    candidates: list[LineItem],
    *,
    allow_substring: bool = False,
) -> LineItem | None:
    """Resolve a formula token to a single line item via longest exact name match.

    Prefer exact (case-insensitive) name or account_code. If ``allow_substring``
    and multiple names contain the token, pick the longest name that equals the
    token as a whole word — never a shorter name that is a substring of a longer
    candidate's name (``Revenue`` must not steal ``Deferred Revenue``).
    """
    t = token.strip()
    if not t:
        return None
    tl = t.lower()

    exact = [
        li for li in candidates
        if (li.name or "").lower() == tl or (li.account_code or "").lower() == tl
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise FormulaAmbiguityError(
            f"Ambiguous formula reference '{token}': "
            + ", ".join(f"{li.name} (id={li.id})" for li in exact)
        )

    # Longest-match: names that equal the token as a whole word boundary match
    word_hits: list[LineItem] = []
    for li in candidates:
        name = li.name or ""
        if re.search(rf"(?i)(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", name):
            # Only accept if the name IS the token (whole) or token is full name
            if name.lower() == tl:
                word_hits.append(li)
            elif allow_substring and name.lower().startswith(tl):
                # Prefer not to use prefix-only; skip
                pass

    # Category exact match as last resort for standard rollups
    cat_hits = [li for li in candidates if (li.category or "").lower() == tl]
    if len(cat_hits) == 1:
        return cat_hits[0]
    if len(cat_hits) > 1:
        # Prefer non-calculated leaves when category is shared
        leaves = [li for li in cat_hits if not li.is_calculated]
        pool = leaves or cat_hits
        if len(pool) > 1:
            raise FormulaAmbiguityError(
                f"Ambiguous category reference '{token}': "
                + ", ".join(li.name or "?" for li in pool)
            )
        return pool[0]

    return None


def parse_formula_refs(formula: str, candidates: list[LineItem]) -> list[tuple[LineItem, str]]:
    """Parse formula into (line_item, relationship) pairs.

    Relationship is ``sum`` for ``+`` / leading terms, ``subtract`` for ``-``.
    Supports ``[Name]`` brackets and bare tokens separated by +/−.
    """
    if not formula or not formula.strip():
        return []

    # Normalize: expand brackets first so names with spaces stay intact
    work = formula.strip()
    bracket_map: dict[str, str] = {}
    for i, m in enumerate(_BRACKET_RE.finditer(work)):
        key = f"__REF{i}__"
        bracket_map[key] = m.group(1).strip()
        work = work.replace(m.group(0), f" {key} ", 1)

    # Split on + / - keeping separators
    parts = re.split(r"([+\-])", work)
    refs: list[tuple[LineItem, str]] = []
    sign = "+"
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part in ("+", "-"):
            sign = part
            continue
        # Strip residual operators/parens
        token = part.strip("() \t")
        if token in bracket_map:
            token = bracket_map[token]
        elif token.startswith("__REF") and token in bracket_map:
            token = bracket_map[token]
        li = resolve_name_token(token, candidates)
        if li is None:
            logger.warning("Formula token '%s' unresolved in: %s", token, formula)
            continue
        rel = "subtract" if sign == "-" else "sum"
        refs.append((li, rel))
        sign = "+"
    return refs


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


def _find_items_scoped(
    items: list[LineItem],
    categories: list[str],
    dependent: LineItem,
) -> list[LineItem]:
    """Category match within BU scope — exact category preferred over substring name."""
    found: list[LineItem] = []
    scoped = _scoped_items(items, dependent)
    for item in scoped:
        cat = (item.category or "").lower()
        for c in categories:
            cl = c.lower()
            if cat == cl:
                found.append(item)
                break
            # Whole-word name match only (not substring of a longer name)
            try:
                if resolve_name_token(c, [item]) is not None:
                    found.append(item)
                    break
            except FormulaAmbiguityError:
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
            continue

        for dependent in dependents:
            dependent.is_calculated = True
            if not dependent.formula:
                dependent.formula = rollup["formula"]

            # Prefer explicit formula tokenization when present
            formula_refs: list[tuple[LineItem, str]] = []
            if dependent.formula:
                try:
                    formula_refs = parse_formula_refs(
                        dependent.formula, _scoped_items(items, dependent)
                    )
                except FormulaAmbiguityError as e:
                    logger.error("Formula ambiguity for %s: %s", dependent.name, e)
                    raise

            if formula_refs:
                for source, rel in formula_refs:
                    key = (dependent.id, source.id)
                    if key in existing:
                        continue
                    ok, msg = dag.add_dependency(dependent.id, source.id, relationship_type=rel)
                    if ok:
                        existing.add(key)
                        created += 1
                    else:
                        logger.warning("Skip dependency %s -> %s: %s", source.id, dependent.id, msg)
                continue

            for source_spec in rollup["sources"]:
                sources = _find_items_scoped(items, source_spec["categories"], dependent)
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

    # Heuristic Gross Margin wiring (BU-scoped)
    for gm in items:
        if (gm.category or "").lower() not in ("gross margin", "gross profit"):
            continue
        gm.is_calculated = True
        gm.formula = gm.formula or "Revenue - COGS"
        revenue = _find_items_scoped(items, ["Revenue"], gm)
        cogs = _find_items_scoped(items, ["COGS", "Cost of Goods Sold", "Cost of Sales"], gm)
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
