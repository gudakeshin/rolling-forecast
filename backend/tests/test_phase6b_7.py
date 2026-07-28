"""Phase 6b + 7 — identity decomposition and what-if scenarios."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.line_item import LineItem
from app.services.driver_series import assert_link, create_driver, upsert_driver_values
from app.services.scenario_what_if import DriverShock, create_what_if_scenario
from app.services.variance_attribution import attribute_variance


def test_identity_qp_volume_first(db_session, seed_users):
    li = LineItem(account_code="REV-QP", name="Revenue QP", category="Revenue", display_order=1)
    db_session.add(li)
    ds = ActualsDataset(
        source_type="test",
        source_name="idqp",
        file_hash="idqp-hash",
        row_count=2,
        period_start="2024-01",
        period_end="2024-02",
        periods_count=2,
    )
    db_session.add(ds)
    db_session.flush()
    # L0=100*10=1000, L1=120*11=1320 → delta 320
    db_session.add_all(
        [
            ActualsRecord(dataset_id=ds.id, line_item_id=li.id, period="2024-01", value=1000.0, currency="USD"),
            ActualsRecord(dataset_id=ds.id, line_item_id=li.id, period="2024-02", value=1320.0, currency="USD"),
        ]
    )
    q = create_driver(db_session, key="qty_rev_qp", name="Quantity", driver_type="volume", actor=seed_users["admin"])
    p = create_driver(db_session, key="price_rev_qp", name="Price", driver_type="price", actor=seed_users["admin"])
    db_session.flush()
    upsert_driver_values(
        db_session, driver_id=q.id, rows=[{"period": "2024-01", "value": 100.0}, {"period": "2024-02", "value": 120.0}]
    )
    upsert_driver_values(
        db_session, driver_id=p.id, rows=[{"period": "2024-01", "value": 10.0}, {"period": "2024-02", "value": 11.0}]
    )
    assert_link(
        db_session,
        driver_id=q.id,
        line_item_id=li.id,
        relation="quantity",
        composition_group="rev_qp",
        status="active",
        actor=seed_users["admin"],
    )
    assert_link(
        db_session,
        driver_id=p.id,
        line_item_id=li.id,
        relation="unit_price",
        composition_group="rev_qp",
        status="active",
        actor=seed_users["admin"],
    )
    db_session.commit()

    out = attribute_variance(
        db_session,
        line_item_id=li.id,
        period_from="2024-01",
        period_to="2024-02",
        basis="identity_qp",
        convention="volume_first",
    )
    assert out["method"] == "identity_qp"
    assert out["buckets"]["volume"] == pytest.approx(200.0)  # (120-100)*10
    assert out["buckets"]["price"] == pytest.approx(120.0)  # (11-10)*120
    assert out["buckets"]["unattributed"] == pytest.approx(0.0)


def test_identity_qp_price_first_changes_split(db_session, seed_users):
    li = LineItem(account_code="REV-QP2", name="Revenue QP2", category="Revenue", display_order=2)
    db_session.add(li)
    ds = ActualsDataset(
        source_type="test",
        source_name="idqp2",
        file_hash="idqp2-hash",
        row_count=2,
        period_start="2024-01",
        period_end="2024-02",
        periods_count=2,
    )
    db_session.add(ds)
    db_session.flush()
    db_session.add_all(
        [
            ActualsRecord(dataset_id=ds.id, line_item_id=li.id, period="2024-01", value=1000.0, currency="USD"),
            ActualsRecord(dataset_id=ds.id, line_item_id=li.id, period="2024-02", value=1320.0, currency="USD"),
        ]
    )
    q = create_driver(db_session, key="qty_rev_qp2", name="Quantity2", driver_type="volume", actor=seed_users["admin"])
    p = create_driver(db_session, key="price_rev_qp2", name="Price2", driver_type="price", actor=seed_users["admin"])
    db_session.flush()
    upsert_driver_values(
        db_session, driver_id=q.id, rows=[{"period": "2024-01", "value": 100.0}, {"period": "2024-02", "value": 120.0}]
    )
    upsert_driver_values(
        db_session, driver_id=p.id, rows=[{"period": "2024-01", "value": 10.0}, {"period": "2024-02", "value": 11.0}]
    )
    assert_link(db_session, driver_id=q.id, line_item_id=li.id, relation="quantity", status="active", actor=seed_users["admin"])
    assert_link(db_session, driver_id=p.id, line_item_id=li.id, relation="unit_price", status="active", actor=seed_users["admin"])
    db_session.commit()

    out = attribute_variance(
        db_session,
        line_item_id=li.id,
        period_from="2024-01",
        period_to="2024-02",
        basis="identity_qp",
        convention="price_first",
    )
    assert out["method"] == "identity_qp"
    assert out["buckets"]["price"] == pytest.approx(100.0)   # (11-10)*100
    assert out["buckets"]["volume"] == pytest.approx(220.0)  # (120-100)*11


def test_identity_qp_mix_parent_sums_to_delta(db_session, seed_users):
    """Parent with two children gets mix bucket; bridge sums to ΔL."""
    from app.models.line_item import LineItem, LineItemDependency

    parent = LineItem(
        account_code="REV-MIX-P",
        name="Revenue Mix Parent",
        category="Revenue",
        display_order=10,
        is_subtotal=True,
    )
    child_a = LineItem(
        account_code="REV-MIX-A",
        name="Product A",
        category="Revenue",
        display_order=11,
    )
    child_b = LineItem(
        account_code="REV-MIX-B",
        name="Product B",
        category="Revenue",
        display_order=12,
    )
    db_session.add_all([parent, child_a, child_b])
    ds = ActualsDataset(
        source_type="test",
        source_name="mix-parent",
        file_hash="mix-parent-hash",
        row_count=4,
        period_start="2024-01",
        period_end="2024-02",
        periods_count=2,
    )
    db_session.add(ds)
    db_session.flush()
    db_session.add_all(
        [
            ActualsRecord(dataset_id=ds.id, line_item_id=child_a.id, period="2024-01", value=1000.0, currency="USD"),
            ActualsRecord(dataset_id=ds.id, line_item_id=child_a.id, period="2024-02", value=1200.0, currency="USD"),
            ActualsRecord(dataset_id=ds.id, line_item_id=child_b.id, period="2024-01", value=1000.0, currency="USD"),
            ActualsRecord(dataset_id=ds.id, line_item_id=child_b.id, period="2024-02", value=1100.0, currency="USD"),
            ActualsRecord(dataset_id=ds.id, line_item_id=parent.id, period="2024-01", value=2000.0, currency="USD"),
            ActualsRecord(dataset_id=ds.id, line_item_id=parent.id, period="2024-02", value=2300.0, currency="USD"),
        ]
    )
    db_session.add_all(
        [
            LineItemDependency(
                dependent_item_id=parent.id,
                source_item_id=child_a.id,
                relationship_type="sum",
                weight=1.0,
            ),
            LineItemDependency(
                dependent_item_id=parent.id,
                source_item_id=child_b.id,
                relationship_type="sum",
                weight=1.0,
            ),
        ]
    )
    q_a = create_driver(db_session, key="qty_mix_a", name="Qty A", driver_type="volume", actor=seed_users["admin"])
    p_a = create_driver(db_session, key="price_mix_a", name="Price A", driver_type="price", actor=seed_users["admin"])
    q_b = create_driver(db_session, key="qty_mix_b", name="Qty B", driver_type="volume", actor=seed_users["admin"])
    p_b = create_driver(db_session, key="price_mix_b", name="Price B", driver_type="price", actor=seed_users["admin"])
    db_session.flush()
    upsert_driver_values(
        db_session,
        driver_id=q_a.id,
        rows=[{"period": "2024-01", "value": 100.0}, {"period": "2024-02", "value": 120.0}],
    )
    upsert_driver_values(
        db_session,
        driver_id=p_a.id,
        rows=[{"period": "2024-01", "value": 10.0}, {"period": "2024-02", "value": 10.0}],
    )
    upsert_driver_values(
        db_session,
        driver_id=q_b.id,
        rows=[{"period": "2024-01", "value": 50.0}, {"period": "2024-02", "value": 50.0}],
    )
    upsert_driver_values(
        db_session,
        driver_id=p_b.id,
        rows=[{"period": "2024-01", "value": 20.0}, {"period": "2024-02", "value": 22.0}],
    )
    for child, q, p in [(child_a, q_a, p_a), (child_b, q_b, p_b)]:
        assert_link(
            db_session,
            driver_id=q.id,
            line_item_id=child.id,
            relation="quantity",
            composition_group=f"mix_{child.id}",
            status="active",
            actor=seed_users["admin"],
        )
        assert_link(
            db_session,
            driver_id=p.id,
            line_item_id=child.id,
            relation="unit_price",
            composition_group=f"mix_{child.id}",
            status="active",
            actor=seed_users["admin"],
        )
    db_session.commit()

    out = attribute_variance(
        db_session,
        line_item_id=parent.id,
        period_from="2024-01",
        period_to="2024-02",
        basis="identity_qp",
    )
    assert out["method"] == "identity_qp_mix"
    assert out["buckets"]["mix"] is not None
    parts = [
        out["buckets"]["volume"],
        out["buckets"]["mix"],
        out["buckets"]["price"],
        out["buckets"]["unattributed"],
    ]
    assert sum(parts) == pytest.approx(out["total_delta"], abs=0.01)
    assert out["explained_pct"] >= 99.0


def test_create_what_if_scenario_service(db_session, seed_users):
    li = LineItem(account_code="REV-WHATIF", name="Revenue what-if", category="Revenue", display_order=3)
    db_session.add(li)
    db_session.flush()
    base = ForecastVersion(
        name="Base Scenario",
        status="draft",
        version_type="scheduled",
        scenario="base",
        horizon_months=2,
        created_by=seed_users["admin"].id,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(base)
    db_session.flush()
    db_session.add_all(
        [
            ForecastLineResult(version_id=base.id, line_item_id=li.id, period="2024-01", p10=90, p50=100.0, p90=110.0, model_p50=100.0),
            ForecastLineResult(version_id=base.id, line_item_id=li.id, period="2024-02", p10=95, p50=100.0, p90=115.0, model_p50=100.0),
        ]
    )
    d = create_driver(db_session, key="drv_elast", name="Elastic Driver", driver_type="macro", actor=seed_users["admin"])
    db_session.flush()
    upsert_driver_values(
        db_session,
        driver_id=d.id,
        rows=[{"period": "2024-01", "value": 100.0}, {"period": "2024-02", "value": 100.0}],
    )
    assert_link(
        db_session,
        driver_id=d.id,
        line_item_id=li.id,
        relation="elasticity",
        coefficient=0.5,
        status="active",
        actor=seed_users["admin"],
    )
    db_session.commit()

    out = create_what_if_scenario(
        db_session,
        base_version_id=base.id,
        scenario_label="downside",
        shocks=[DriverShock(driver_id=d.id, mode="pct", value=-10.0)],
        actor=seed_users["admin"],
    )
    assert out["scenario_version_id"]
    assert out["affected_line_periods"] > 0

    child = db_session.query(ForecastVersion).filter(ForecastVersion.id == out["scenario_version_id"]).first()
    assert child is not None
    assert child.version_type == "scenario"
    assert child.scenario == "downside"

    rows = (
        db_session.query(ForecastLineResult)
        .filter(ForecastLineResult.version_id == child.id, ForecastLineResult.line_item_id == li.id)
        .order_by(ForecastLineResult.period)
        .all()
    )
    assert rows[0].p50 != pytest.approx(100.0)
    assert rows[0].bounds_method == "scenario"
    assert rows[0].p10 == pytest.approx(90.0)  # base intervals retained


def test_identity_qp_reconciliation_bucket_sums_to_published_delta(db_session, seed_users):
    """MinT coherence delta is an explicit bucket; Q×P runs on pre-reconcile L."""
    li = LineItem(
        account_code="REV-RECON",
        name="Revenue Recon",
        category="Revenue",
        display_order=20,
    )
    db_session.add(li)
    db_session.flush()
    version = ForecastVersion(
        name="Recon Attr",
        status="draft",
        version_type="baseline",
        horizon_months=2,
        created_by=seed_users["admin"].id,
    )
    db_session.add(version)
    db_session.flush()
    # Pre-MinT L matches Q×P; published p50 includes +50 MinT move on period_to only.
    db_session.add_all(
        [
            ForecastLineResult(
                version_id=version.id,
                line_item_id=li.id,
                period="2024-01",
                p10=900,
                p50=1000.0,
                p90=1100,
                model_p50=1000.0,
                pre_reconcile_p50=1000.0,
                bounds_method="mint_full",
            ),
            ForecastLineResult(
                version_id=version.id,
                line_item_id=li.id,
                period="2024-02",
                p10=1200,
                p50=1370.0,  # pre 1320 + 50 MinT
                p90=1400,
                model_p50=1320.0,
                pre_reconcile_p50=1320.0,
                bounds_method="mint_full",
            ),
        ]
    )
    q = create_driver(
        db_session, key="qty_rev_recon", name="Quantity", driver_type="volume", actor=seed_users["admin"]
    )
    p = create_driver(
        db_session, key="price_rev_recon", name="Price", driver_type="price", actor=seed_users["admin"]
    )
    db_session.flush()
    upsert_driver_values(
        db_session,
        driver_id=q.id,
        rows=[{"period": "2024-01", "value": 100.0}, {"period": "2024-02", "value": 120.0}],
    )
    upsert_driver_values(
        db_session,
        driver_id=p.id,
        rows=[{"period": "2024-01", "value": 10.0}, {"period": "2024-02", "value": 11.0}],
    )
    assert_link(
        db_session,
        driver_id=q.id,
        line_item_id=li.id,
        relation="quantity",
        composition_group="rev_recon",
        status="active",
        actor=seed_users["admin"],
    )
    assert_link(
        db_session,
        driver_id=p.id,
        line_item_id=li.id,
        relation="unit_price",
        composition_group="rev_recon",
        status="active",
        actor=seed_users["admin"],
    )
    db_session.commit()

    out = attribute_variance(
        db_session,
        line_item_id=li.id,
        period_from="2024-01",
        period_to="2024-02",
        basis="identity_qp",
        convention="volume_first",
        version_id=version.id,
    )
    assert out["method"] == "identity_qp"
    assert out["buckets"]["volume"] == pytest.approx(200.0)
    assert out["buckets"]["price"] == pytest.approx(120.0)
    assert out["buckets"]["reconciliation"] == pytest.approx(50.0)
    assert out["buckets"]["unattributed"] == pytest.approx(0.0)
    assert out["total_delta"] == pytest.approx(370.0)  # published 1370-1000
    parts = [
        out["buckets"]["volume"],
        out["buckets"]["price"],
        out["buckets"]["reconciliation"],
        out["buckets"]["unattributed"],
    ]
    assert sum(parts) == pytest.approx(out["total_delta"], abs=0.01)


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_what_if_endpoint_smoke(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # bad request still validates endpoint wiring and permission path
    r = client.post(
        "/api/scenarios/what-if",
        headers=headers,
        json={"base_version_id": "missing", "scenario_label": "x", "shocks": []},
    )
    assert r.status_code == 400
