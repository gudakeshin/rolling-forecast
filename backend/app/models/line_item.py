"""P&L line item and dependency graph models."""

from sqlalchemy import String, Integer, Boolean, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class LineItem(Base):
    """A P&L line item (account) that can be forecasted."""

    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # "Revenue", "COGS", "OpEx", "EBITDA", etc.
    subcategory: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    geography: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_line: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Calculation properties
    is_calculated: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # True if derived from other lines
    formula: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # e.g., "revenue - cogs" or formula definition
    sign_convention: Mapped[str] = mapped_column(
        String(20), default="positive"
    )  # "positive" or "negative" (for expenses)
    allow_negative: Mapped[bool] = mapped_column(Boolean, default=False)

    # Display
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    indent_level: Mapped[int] = mapped_column(
        Integer, default=0
    )  # For P&L hierarchy display
    is_subtotal: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    actuals_records: Mapped[list["ActualsRecord"]] = relationship(
        back_populates="line_item"
    )
    forecast_results: Mapped[list["ForecastLineResult"]] = relationship(
        back_populates="line_item"
    )
    overrides: Mapped[list["Override"]] = relationship(back_populates="line_item")

    # Dependencies: lines that this item depends on (parents in the DAG)
    dependencies: Mapped[list["LineItemDependency"]] = relationship(
        back_populates="dependent_item",
        foreign_keys="LineItemDependency.dependent_item_id",
    )
    # Dependents: lines that depend on this item (children in the DAG)
    dependents: Mapped[list["LineItemDependency"]] = relationship(
        back_populates="source_item",
        foreign_keys="LineItemDependency.source_item_id",
    )


class LineItemDependency(Base):
    """Edge in the P&L dependency DAG: dependent_item depends on source_item."""

    __tablename__ = "line_item_dependencies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dependent_item_id: Mapped[int] = mapped_column(
        ForeignKey("line_items.id"), nullable=False
    )
    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("line_items.id"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(50), default="sum"
    )  # "sum", "subtract", "multiply", "formula"
    weight: Mapped[float | None] = mapped_column(default=1.0)  # For weighted sums

    dependent_item: Mapped["LineItem"] = relationship(
        back_populates="dependencies",
        foreign_keys=[dependent_item_id],
    )
    source_item: Mapped["LineItem"] = relationship(
        back_populates="dependents",
        foreign_keys=[source_item_id],
    )
