"""Numeric grounding helpers for LLM commentary."""

from __future__ import annotations

import re
from typing import Any


_FACT_RE = re.compile(r"\{fact:([a-zA-Z0-9_]+)\}")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?%?")


def render_fact_placeholders(text: str, facts: dict[str, Any]) -> str:
    """Replace {fact:key} placeholders with values from the facts dict."""

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in facts:
            return m.group(0)
        val = facts[key]
        if isinstance(val, float):
            return f"{val:,.1f}"
        if isinstance(val, int):
            return f"{val:,}"
        return str(val)

    return _FACT_RE.sub(_sub, text)


def validate_numeric_claims(
    text: str, allowed_numbers: set[str]
) -> tuple[str, int, int]:
    """Strip or flag numeric claims not present in allowed_numbers.

    Returns (cleaned_text, verified_count, total_numeric_claims).
    """
    verified = 0
    total = 0

    def _check(m: re.Match) -> str:
        nonlocal verified, total
        total += 1
        raw = m.group(0)
        normalized = raw.replace(",", "").rstrip("%")
        if raw in allowed_numbers or normalized in allowed_numbers:
            verified += 1
            return raw
        # Soft strip: keep text but mark unverified numbers as [n]
        return f"[{raw}]"

    cleaned = _NUMBER_RE.sub(_check, text)
    return cleaned, verified, total


def facts_from_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Build a flat fact map from commentary context for placeholder rendering."""
    facts: dict[str, Any] = {}
    if "revenue_total" in ctx:
        facts["revenue_total"] = ctx["revenue_total"]
    if "ebitda_total" in ctx:
        facts["ebitda_total"] = ctx["ebitda_total"]
    summary = ctx.get("summary") or {}
    for k, v in summary.items():
        if isinstance(v, (int, float, str)):
            facts[str(k)] = v
    return facts
