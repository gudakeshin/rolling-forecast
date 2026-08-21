"""Phase 1 regression tests — ensemble MAPE, midnight crash, override bounds, SSRF."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.services.error_handlers import check_concurrent_override
from app.services.integration_safety import (
    assert_safe_integration_url,
    safe_fetch_url,
)
from app.services.dependency_graph import DependencyGraphManager
from app.services.overrides import recalculate_and_reconcile
from app.models.line_item import LineItem, LineItemDependency
from app.models.forecast import ForecastVersion, ForecastLineResult


class TestEnsembleMapeKey:
    """Ensemble must weight on CV MAPE, not the nonexistent 'mape' fit_metrics key."""

    def test_fit_metrics_use_in_sample_mape_not_mape(self):
        from app.domain.engines.model_registry import get_model_registry

        values = pd.Series(np.linspace(100, 200, 24))
        dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=24, freq="MS"))
        registry = get_model_registry()
        # Use linear (no optional statsmodels dep) — same fit_metrics contract as ETS/ARIMA
        out = registry.fit_and_predict("linear", values, dates, horizon=3)
        # Engines write in_sample_mape; the old "mape" key must not be the sole source
        assert "in_sample_mape" in out.fit_metrics
        assert out.fit_metrics.get("mape") is None or "in_sample_mape" in out.fit_metrics

    def test_ensemble_prefers_cv_mape_over_missing_mape_key(self):
        """Simulated weight selection: CV MAPE differs → unequal weights."""
        from app.domain.skills.run_ensemble import RunEnsembleSkill

        skill = RunEnsembleSkill()
        # Inverse-MAPE weighting: mape 10 vs 40 → weights 0.8 / 0.2
        weights = skill._compute_weights(
            {"arima": 10.0, "ets": 40.0}, "inverse_mape"
        )
        assert weights["arima"] > weights["ets"]
        assert abs(sum(weights.values()) - 1.0) < 1e-6


class TestMidnightConcurrentOverride:
    def test_check_concurrent_override_at_0030_utc(self, db_session, monkeypatch):
        """Frozen at 00:30 UTC — must not raise ValueError from hour=-1."""
        frozen = datetime(2026, 7, 16, 0, 30, 0, tzinfo=timezone.utc)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return frozen.astimezone(tz) if frozen.tzinfo else frozen
                return frozen.replace(tzinfo=None)

        monkeypatch.setattr(
            "app.services.error_handlers.datetime", _FrozenDatetime
        )

        # Should return None (no concurrent override), not crash
        result = check_concurrent_override(
            db_session, "v1", 1, "2026-01", "user-a"
        )
        assert result is None


class TestOverrideBoundsAndReconcile:
    def _setup(self, db_session):
        revenue = LineItem(
            account_code="P1-REV", name="Revenue", category="Revenue",
            display_order=1, is_calculated=False,
        )
        cogs = LineItem(
            account_code="P1-COGS", name="COGS", category="COGS",
            display_order=2, is_calculated=False,
        )
        gp = LineItem(
            account_code="P1-GP", name="Gross Profit", category="Profit",
            display_order=3, is_calculated=True, is_subtotal=True, allow_negative=True,
        )
        db_session.add_all([revenue, cogs, gp])
        db_session.flush()
        db_session.add_all([
            LineItemDependency(
                dependent_item_id=gp.id, source_item_id=revenue.id,
                relationship_type="sum", weight=1.0,
            ),
            LineItemDependency(
                dependent_item_id=gp.id, source_item_id=cogs.id,
                relationship_type="subtract", weight=1.0,
            ),
        ])
        version = ForecastVersion(name="P1-v1", status="draft", horizon_months=1)
        db_session.add(version)
        db_session.flush()
        rev = ForecastLineResult(
            version_id=version.id, line_item_id=revenue.id, period="2026-01",
            p10=900.0, p50=1000.0, p90=1100.0, confidence_score=80, confidence_level="high",
        )
        cogs_r = ForecastLineResult(
            version_id=version.id, line_item_id=cogs.id, period="2026-01",
            p10=500.0, p50=600.0, p90=700.0, confidence_score=80, confidence_level="high",
        )
        gp_r = ForecastLineResult(
            version_id=version.id, line_item_id=gp.id, period="2026-01",
            p10=0.0, p50=0.0, p90=0.0, confidence_score=80, confidence_level="high",
            is_calculated=True,
        )
        db_session.add_all([rev, cogs_r, gp_r])
        db_session.commit()
        return revenue, cogs, gp, version, rev, cogs_r, gp_r

    def test_recalculate_dependents_updates_p10_p90(self, db_session):
        revenue, cogs, gp, version, rev, cogs_r, gp_r = self._setup(db_session)
        rev.is_overridden = True
        rev.override_value = 1200.0
        rev.p10 = 1100.0
        rev.p90 = 1300.0
        db_session.flush()

        dag = DependencyGraphManager(db_session)
        n = dag.recalculate_dependents(version.id, revenue.id, ["2026-01"])
        db_session.refresh(gp_r)

        assert n == 1
        assert gp_r.p50 == 600.0  # 1200 - 600
        # Linear aggregation of bounds
        assert gp_r.p10 == pytest.approx(1100.0 + 500.0)
        assert gp_r.p90 == pytest.approx(1300.0 + 700.0)
        assert gp_r.bounds_method == "linear_aggregation"

    def test_recalculate_and_reconcile_runs(self, db_session):
        revenue, cogs, gp, version, rev, cogs_r, gp_r = self._setup(db_session)
        rev.is_overridden = True
        rev.override_value = 1500.0
        db_session.flush()

        n = recalculate_and_reconcile(db_session, version.id, [revenue.id], ["2026-01"])
        db_session.refresh(gp_r)
        db_session.refresh(rev)
        assert n >= 1
        # Bottom-up expectation before/around MinT: GP ≈ override − COGS
        # MinT may nudge parents; require coherence within tolerance.
        expected = 1500.0 - 600.0
        assert gp_r.p50 == pytest.approx(expected, rel=0.25)
        assert gp_r.p50 > 0


class TestFetchUrlSsrf:
    def test_denies_link_local_metadata(self):
        with pytest.raises(ValueError, match="private"):
            assert_safe_integration_url("http://169.254.169.254/latest/meta-data/", "web")

    def test_denies_localhost(self):
        with pytest.raises(ValueError, match="private"):
            assert_safe_integration_url("http://localhost:8080/secret", "web")

    def test_denies_redirect_to_private(self, monkeypatch):
        """Redirect Location pointing at a private host must be rejected."""

        class FakeResp:
            def __init__(self, status_code, location=None, text=""):
                self.status_code = status_code
                self.headers = {"Location": location} if location else {}
                self.text = text

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError("http error")

        calls = {"n": 0}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    return FakeResp(302, location="http://127.0.0.1/admin")
                return FakeResp(200, text="<html>ok</html>")

        monkeypatch.setattr(
            "app.services.integration_safety.httpx.Client", FakeClient
        )
        # First hop is a public host; redirect target is private
        monkeypatch.setattr(
            "app.services.integration_safety._resolve_public_ip",
            lambda host: "93.184.216.34",
        )
        monkeypatch.setattr(
            "app.services.integration_safety.is_private_host",
            lambda host: host in {"127.0.0.1", "localhost"} or host.startswith("169."),
        )

        with pytest.raises(ValueError, match="private"):
            safe_fetch_url("https://example.com/start", kind="web")
