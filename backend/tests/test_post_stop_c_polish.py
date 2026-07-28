"""Post–Stop C polish: driver list freshness batching + explainability query bounds."""

from __future__ import annotations

import asyncio

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.models.forecast import ForecastLineResult, ForecastVersion
from app.services.driver_series import create_driver, upsert_driver_values


def _count_queries(engine: Engine):
    state = {"n": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        state["n"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)

    class Counter:
        @property
        def count(self) -> int:
            return state["n"]

        def reset(self) -> None:
            state["n"] = 0

        def remove(self) -> None:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)

    return Counter()


def test_list_drivers_freshness_is_batched(db_engine, db_session, seed_users):
    """include_freshness must not issue 2 queries per driver."""
    from app.api.drivers import list_drivers

    admin = seed_users["admin"]
    drivers = []
    for i in range(6):
        d = create_driver(
            db_session,
            key=f"batch_fresh_{i}",
            name=f"Batch Fresh {i}",
            driver_type="macro",
            actor=admin,
        )
        drivers.append(d)
    db_session.flush()
    for d in drivers:
        upsert_driver_values(
            db_session,
            driver_id=d.id,
            rows=[{"period": "2024-01", "value": 1.0}, {"period": "2024-02", "value": 2.0}],
        )
    db_session.commit()

    counter = _count_queries(db_engine)
    try:
        counter.reset()
        result = asyncio.run(
            list_drivers(
                driver_type=None,
                include_freshness=True,
                current_user=admin,
                db=db_session,
            )
        )
        queries = counter.count
    finally:
        counter.remove()

    matched = [r for r in result if str(r["key"]).startswith("batch_fresh_")]
    assert len(matched) >= 6
    assert all("freshness" in r for r in matched)
    # scoped list + 2 aggregate freshness queries (not 2×N)
    assert queries < 6 + len(matched), f"expected batched freshness, got {queries} queries"


def test_budget_bridge_attribute_query_bound(db_session, seed_users, seed_line_items):
    """Attributed budget bridge stays O(1) w.r.t. line count for override preload."""
    from app.api.executive import budget_bridge

    items = [li for li in seed_line_items.values() if not li.is_calculated][:8]
    if len(items) < 3:
        return
    version = ForecastVersion(
        id="polish-bridge-q",
        name="Polish Bridge",
        status="draft",
        version_type="baseline",
        horizon_months=2,
        created_by=seed_users["admin"].id,
    )
    db_session.add(version)
    db_session.flush()
    for li in items:
        db_session.add(
            ForecastLineResult(
                version_id=version.id,
                line_item_id=li.id,
                period="2024-01",
                p10=90.0,
                p50=100.0,
                p90=110.0,
                model_type="linear",
                confidence_score=70.0,
                confidence_level="medium",
            )
        )
    db_session.commit()

    engine = db_session.get_bind()
    counter = _count_queries(engine)
    try:
        counter.reset()
        result = asyncio.run(
            budget_bridge(
                version_id=version.id,
                budget_version_id=None,
                materiality_pct=0.0,
                page=1,
                page_size=20,
                attribute=True,
                convention="volume_first",
                current_user=seed_users["admin"],
                db=db_session,
            )
        )
        queries = counter.count
    finally:
        counter.remove()

    assert result["attribute"] is True
    # Batched aggregates + BridgeAttributionContext — not per-line override scans
    assert queries < 40 + len(items), f"bridge queries grew too fast: {queries}"
