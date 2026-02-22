"""Dependency graph manager using NetworkX for P&L line item recalculation."""

import logging
from typing import Any
import networkx as nx
from sqlalchemy.orm import Session

from app.models.line_item import LineItem, LineItemDependency
from app.models.forecast import ForecastLineResult

logger = logging.getLogger(__name__)


class DependencyGraphManager:
    """
    Manages the P&L line item dependency DAG.
    
    When an override is applied to a line item, all downstream dependent items
    must be recalculated. This class handles:
    - Loading the graph from the database
    - Validating acyclicity
    - Finding downstream dependents
    - Recalculating affected line items
    """

    def __init__(self, db: Session):
        self.db = db
        self._graph: nx.DiGraph | None = None

    def _load_graph(self) -> nx.DiGraph:
        """Load the dependency graph from the database."""
        if self._graph is not None:
            return self._graph

        G = nx.DiGraph()

        # Add all line items as nodes
        items = self.db.query(LineItem).all()
        for item in items:
            G.add_node(item.id, name=item.name, category=item.category,
                       is_calculated=item.is_calculated, formula=item.formula)

        # Add edges from dependencies
        deps = self.db.query(LineItemDependency).all()
        for dep in deps:
            # Edge goes from source -> dependent (source feeds into dependent)
            G.add_edge(
                dep.source_item_id,
                dep.dependent_item_id,
                relationship_type=dep.relationship_type,
                weight=dep.weight,
            )

        self._graph = G
        return G

    def validate_acyclic(self) -> tuple[bool, list[int] | None]:
        """
        Check if the graph is acyclic (DAG).
        Returns (is_valid, cycle_nodes_if_invalid).
        """
        G = self._load_graph()
        if nx.is_directed_acyclic_graph(G):
            return True, None
        else:
            cycles = list(nx.simple_cycles(G))
            return False, cycles[0] if cycles else None

    def get_downstream_dependents(self, line_item_id: int) -> list[int]:
        """
        Get all downstream dependents of a line item in topological order.
        These are the items that need recalculation when the source changes.
        """
        G = self._load_graph()
        if line_item_id not in G:
            return []

        # Get all descendants (transitive closure downstream)
        descendants = nx.descendants(G, line_item_id)

        # Return in topological order (so we recalculate parents before children)
        if not descendants:
            return []

        subgraph = G.subgraph(descendants)
        try:
            return list(nx.topological_sort(subgraph))
        except nx.NetworkXUnfeasible:
            logger.error(f"Cycle detected in subgraph for item {line_item_id}")
            return list(descendants)

    def recalculate_dependents(
        self,
        version_id: str,
        source_line_item_id: int,
        periods: list[str] | None = None,
    ) -> int:
        """
        Recalculate all downstream dependents after an override.
        
        Args:
            version_id: Forecast version to update
            source_line_item_id: The overridden line item
            periods: Specific periods to recalculate (all if None)
            
        Returns:
            Number of line results recalculated
        """
        G = self._load_graph()
        downstream = self.get_downstream_dependents(source_line_item_id)

        if not downstream:
            return 0

        recalc_count = 0

        for dependent_id in downstream:
            # Get the dependency edges leading into this node
            predecessors = list(G.predecessors(dependent_id))
            node_data = G.nodes[dependent_id]

            if not node_data.get("is_calculated"):
                continue

            # Get the periods to recalculate
            if periods:
                target_periods = periods
            else:
                # Get all periods for this line item in this version
                results = (
                    self.db.query(ForecastLineResult.period)
                    .filter(
                        ForecastLineResult.version_id == version_id,
                        ForecastLineResult.line_item_id == dependent_id,
                    )
                    .distinct()
                    .all()
                )
                target_periods = [r[0] for r in results]

            for period in target_periods:
                # Gather source values
                source_values = {}
                for pred_id in predecessors:
                    pred_result = (
                        self.db.query(ForecastLineResult)
                        .filter(
                            ForecastLineResult.version_id == version_id,
                            ForecastLineResult.line_item_id == pred_id,
                            ForecastLineResult.period == period,
                        )
                        .first()
                    )
                    if pred_result:
                        # Use override value if present, otherwise model value
                        val = pred_result.override_value if pred_result.is_overridden else pred_result.p50
                        edge_data = G.edges[pred_id, dependent_id]
                        source_values[pred_id] = {
                            "value": val,
                            "relationship": edge_data.get("relationship_type", "sum"),
                            "weight": edge_data.get("weight", 1.0),
                        }

                if not source_values:
                    continue

                # Calculate new value
                new_value = self._calculate_value(source_values, node_data.get("formula"))

                # Update the forecast result
                result = (
                    self.db.query(ForecastLineResult)
                    .filter(
                        ForecastLineResult.version_id == version_id,
                        ForecastLineResult.line_item_id == dependent_id,
                        ForecastLineResult.period == period,
                    )
                    .first()
                )

                if result:
                    result.p50 = new_value
                    result.is_calculated = True
                    recalc_count += 1

        self.db.flush()
        return recalc_count

    def _calculate_value(
        self, source_values: dict[int, dict], formula: str | None
    ) -> float:
        """Calculate a dependent value from its sources."""
        if formula:
            # TODO: Parse and evaluate formula expressions
            # For now, fall back to sum/subtract logic
            pass

        total = 0.0
        for source_id, info in source_values.items():
            relationship = info["relationship"]
            value = info["value"] * info["weight"]

            if relationship == "sum" or relationship == "add":
                total += value
            elif relationship == "subtract":
                total -= value
            elif relationship == "multiply":
                total *= value if total != 0 else value
            else:
                total += value

        return total

    def add_dependency(
        self, dependent_id: int, source_id: int, relationship_type: str = "sum", weight: float = 1.0
    ) -> tuple[bool, str]:
        """
        Add a dependency edge. Validates that it doesn't create a cycle.
        Returns (success, message).
        """
        G = self._load_graph()

        # Check if adding this edge would create a cycle
        G_test = G.copy()
        G_test.add_edge(source_id, dependent_id)

        if not nx.is_directed_acyclic_graph(G_test):
            return False, f"Adding this dependency would create a circular reference"

        # Add to database
        dep = LineItemDependency(
            dependent_item_id=dependent_id,
            source_item_id=source_id,
            relationship_type=relationship_type,
            weight=weight,
        )
        self.db.add(dep)
        self.db.flush()

        # Invalidate cached graph
        self._graph = None

        return True, "Dependency added successfully"
