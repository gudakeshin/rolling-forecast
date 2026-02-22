"""Tests for the P&L dependency graph (DAG) manager."""

import pytest
from sqlalchemy.orm import Session

from app.services.dependency_graph import DependencyGraphManager
from app.models.line_item import LineItem, LineItemDependency
from app.models.forecast import ForecastVersion, ForecastLineResult


class TestDependencyGraph:
    """Test EC5: Circular dependency detection and downstream recalculation."""

    def _setup_dag(self, db_session):
        """Create a simple Revenue - COGS = Gross Profit DAG."""
        revenue = LineItem(
            account_code="TEST-REV", name="Revenue", category="Revenue",
            display_order=1, is_calculated=False
        )
        cogs = LineItem(
            account_code="TEST-COGS", name="COGS", category="COGS",
            display_order=2, is_calculated=False
        )
        gross_profit = LineItem(
            account_code="TEST-GP", name="Gross Profit", category="Profit",
            display_order=3, is_calculated=True, is_subtotal=True, allow_negative=True
        )
        db_session.add_all([revenue, cogs, gross_profit])
        db_session.flush()

        # Gross Profit = Revenue - COGS
        dep1 = LineItemDependency(
            dependent_item_id=gross_profit.id,
            source_item_id=revenue.id,
            relationship_type="sum",
            weight=1.0,
        )
        dep2 = LineItemDependency(
            dependent_item_id=gross_profit.id,
            source_item_id=cogs.id,
            relationship_type="subtract",
            weight=1.0,
        )
        db_session.add_all([dep1, dep2])
        db_session.commit()

        return revenue, cogs, gross_profit

    def test_load_graph(self, db_session):
        """Graph loads correctly from database."""
        revenue, cogs, gp = self._setup_dag(db_session)
        dag = DependencyGraphManager(db_session)

        graph = dag._load_graph()
        assert len(graph.nodes) >= 3
        assert len(graph.edges) >= 2

    def test_validate_acyclic(self, db_session):
        """Valid DAG should pass acyclicity check."""
        self._setup_dag(db_session)
        dag = DependencyGraphManager(db_session)

        is_valid, cycle = dag.validate_acyclic()
        assert is_valid is True
        assert cycle is None

    def test_downstream_dependents(self, db_session):
        """Should find Gross Profit as downstream of Revenue."""
        revenue, cogs, gp = self._setup_dag(db_session)
        dag = DependencyGraphManager(db_session)

        downstream = dag.get_downstream_dependents(revenue.id)
        assert gp.id in downstream

    def test_no_downstream_for_leaf(self, db_session):
        """Calculated items with no dependents have no downstream."""
        revenue, cogs, gp = self._setup_dag(db_session)
        dag = DependencyGraphManager(db_session)

        downstream = dag.get_downstream_dependents(gp.id)
        assert len(downstream) == 0

    def test_circular_dependency_rejected(self, db_session):
        """EC5: Adding a circular dependency should be rejected."""
        revenue, cogs, gp = self._setup_dag(db_session)
        dag = DependencyGraphManager(db_session)

        # Try to make Revenue depend on Gross Profit (circular)
        success, msg = dag.add_dependency(
            dependent_id=revenue.id,
            source_id=gp.id,
            relationship_type="sum",
        )
        assert success is False
        assert "circular" in msg.lower()

    def test_recalculate_dependents(self, db_session):
        """Recalculation after override should update calculated lines."""
        revenue, cogs, gp = self._setup_dag(db_session)
        dag = DependencyGraphManager(db_session)

        # Create a forecast version with results
        version = ForecastVersion(
            name="TEST-v1", status="draft", horizon_months=1,
        )
        db_session.add(version)
        db_session.flush()

        # Revenue = 1000, COGS = 600, GP should be 400
        rev_result = ForecastLineResult(
            version_id=version.id, line_item_id=revenue.id,
            period="2026-01", p50=1000.0, confidence_score=80, confidence_level="high",
        )
        cogs_result = ForecastLineResult(
            version_id=version.id, line_item_id=cogs.id,
            period="2026-01", p50=600.0, confidence_score=80, confidence_level="high",
        )
        gp_result = ForecastLineResult(
            version_id=version.id, line_item_id=gp.id,
            period="2026-01", p50=0.0, confidence_score=80, confidence_level="high",
            is_calculated=True,
        )
        db_session.add_all([rev_result, cogs_result, gp_result])
        db_session.commit()

        # Override revenue to 1200
        rev_result.p50 = 1200.0
        recalc_count = dag.recalculate_dependents(version.id, revenue.id, ["2026-01"])

        # GP should now be 1200 - 600 = 600
        db_session.refresh(gp_result)
        assert gp_result.p50 == 600.0
        assert recalc_count == 1
