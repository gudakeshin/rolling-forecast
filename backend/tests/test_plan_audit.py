"""The enhancement plan and its verification audit must not drift apart.

Re-extracts every requirement bullet from ENHANCEMENT_PLAN.md (item number +
bullet letter, in plan order) and checks ENHANCEMENT_PLAN_VERIFICATION.md's
audit appendix has a row with evidence for each, and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def plan_bullet_ids(text: str) -> list[str]:
    ids: list[str] = []
    item: str | None = None
    count = 0
    for line in text.splitlines():
        heading = re.match(r"\*\*(\d\.\d) ", line)
        if heading:
            item, count = heading.group(1), 0
            continue
        if line.startswith("## ") and not line.startswith("## Phase"):
            item = None
            continue
        if item is None:
            continue
        is_bullet = line.startswith("- ")
        is_lead_paragraph = (
            count == 0 and line and line[0] not in " -*#|" and not line.startswith("**")
        )
        if is_bullet or is_lead_paragraph:
            count += 1
            ids.append(f"{item}.{chr(96 + count)}")
    return ids


def audit_rows(text: str) -> dict[str, str]:
    block = text.split("<!-- plan-audit:start -->", 1)[1].split("<!-- plan-audit:end -->", 1)[0]
    rows = {}
    for line in block.splitlines():
        m = re.match(r"\|\s*(\d\.\d\.[a-z])\s*\|\s*(.*?)\s*\|\s*$", line)
        if m:
            rows[m.group(1)] = m.group(2)
    return rows


def test_every_plan_bullet_has_audited_evidence():
    ids = plan_bullet_ids((ROOT / "ENHANCEMENT_PLAN.md").read_text())
    rows = audit_rows((ROOT / "ENHANCEMENT_PLAN_VERIFICATION.md").read_text())
    assert len(ids) >= 60, "plan bullet extraction looks broken"
    missing = [i for i in ids if not rows.get(i)]
    extra = sorted(set(rows) - set(ids))
    assert not missing, f"plan bullets without audited evidence: {missing}"
    assert not extra, f"audit rows for bullets the plan does not have: {extra}"


def test_cited_backend_tests_exist():
    text = (ROOT / "ENHANCEMENT_PLAN_VERIFICATION.md").read_text()
    cited = set(re.findall(r"`(?:\w+::)?(test_[a-z0-9_]+)`", text))
    defined = set()
    for path in (ROOT / "backend" / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"def (test_[a-z0-9_]+)", path.read_text()))
        defined.add(path.stem)
    assert not sorted(cited - defined), f"cited tests that do not exist: {sorted(cited - defined)}"


def test_cited_frontend_tests_exist():
    text = (ROOT / "ENHANCEMENT_PLAN_VERIFICATION.md").read_text()
    cited = set(re.findall(r"`([\w]+\.test\.tsx?)`", text))
    present = {p.name for p in (ROOT / "frontend" / "src").rglob("*.test.ts*")}
    assert not sorted(cited - present), f"cited frontend tests missing: {sorted(cited - present)}"
