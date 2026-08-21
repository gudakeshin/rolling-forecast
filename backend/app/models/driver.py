"""Causal driver entities — separate from BU assumption DriverInput forms."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base

# Allowed enumerations (enforced in API/services; stored as strings for SQLite)
DRIVER_TYPES = frozenset({"volume", "price", "rate", "headcount", "macro", "index", "other"})
AGGREGATIONS = frozenset({"sum", "average", "end_of_period"})
VALUE_TYPES = frozenset({"actual", "forecast", "plan", "scenario"})
LINK_RELATIONS = frozenset({"elasticity", "level", "ratio", "quantity", "unit_price"})
TRANSFORMS = frozenset({"level", "log", "diff", "log_diff"})
LINK_STATUSES = frozenset({"candidate", "active", "rejected", "superseded"})

# Sentinel for version-less rows — NULLs break unique indexes under upsert
NO_VERSION_ID = ""


class Driver(Base):
    """A non-financial (or volume/price) series that can drive P&L lines."""

    __tablename__ = "drivers"
    __table_args__ = (
        UniqueConstraint("key", name="uq_drivers_key"),
        Index("ix_drivers_business_unit", "business_unit"),
        Index("ix_drivers_driver_type", "driver_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    driver_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    aggregation: Mapped[str] = mapped_column(String(32), nullable=False, default="sum")
    business_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    geography: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_line: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)  # csv|fred|yfinance|manual
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    values: Mapped[list["DriverValue"]] = relationship(
        back_populates="driver", cascade="all, delete-orphan"
    )
    links: Mapped[list["DriverLink"]] = relationship(
        back_populates="driver", cascade="all, delete-orphan"
    )


class DriverValue(Base):
    """Point-in-time value for a driver (actual / forecast / plan / scenario)."""

    __tablename__ = "driver_values"
    __table_args__ = (
        UniqueConstraint(
            "driver_id",
            "period",
            "value_type",
            "version_id",
            name="uq_driver_values_driver_period_type_version",
        ),
        Index("ix_driver_values_driver_period", "driver_id", "period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    p90: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_type: Mapped[str] = mapped_column(String(32), nullable=False, default="actual")
    # Empty string sentinel — NOT NULL; required for upsert uniqueness
    version_id: Mapped[str] = mapped_column(String(36), nullable=False, default=NO_VERSION_ID)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    driver: Mapped["Driver"] = relationship(back_populates="values")


class DriverDiscoveryRun(Base):
    """Auditable discovery config + full ranked candidate list (incl. rejects)."""

    __tablename__ = "driver_discovery_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    line_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("line_items.id"), nullable=True
    )
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    links: Mapped[list["DriverLink"]] = relationship(back_populates="discovery_run")


class DriverLink(Base):
    """Statistical or asserted link from a driver to a line item."""

    __tablename__ = "driver_links"
    __table_args__ = (
        Index("ix_driver_links_line_item", "line_item_id"),
        Index("ix_driver_links_status", "status"),
        Index("ix_driver_links_composition", "composition_group"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    line_item_id: Mapped[int] = mapped_column(ForeignKey("line_items.id"), nullable=False)
    link_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual"
    )  # manual | discovered
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="level")
    lag: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coefficient: Mapped[float | None] = mapped_column(Float, nullable=True)
    coefficient_se: Mapped[float | None] = mapped_column(Float, nullable=True)
    elasticity: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_stat: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_value_adj: Mapped[float | None] = mapped_column(Float, nullable=True)
    r2: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_obs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transform: Mapped[str] = mapped_column(String(32), nullable=False, default="level")
    fit_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hac_lags: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    discovery_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("driver_discovery_runs.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    # Groups quantity + unit_price links that multiply to form the line
    composition_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    driver: Mapped["Driver"] = relationship(back_populates="links")
    discovery_run: Mapped["DriverDiscoveryRun | None"] = relationship(
        back_populates="links"
    )
