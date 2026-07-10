"""ERP / warehouse actuals adapters implementing IActualsProvider."""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ActualsPullResult:
    success: bool
    dataframe: pd.DataFrame | None = None
    error: str | None = None
    file_hash: str = ""
    row_count: int = 0
    period_start: str | None = None
    period_end: str | None = None
    periods_count: int = 0
    missing_periods: list[str] = field(default_factory=list)
    completeness_pct: float = 100.0
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class IActualsProvider(ABC):
    @abstractmethod
    async def pull_actuals(self, params: dict[str, Any]) -> ActualsPullResult:
        ...


class WarehouseSQLAdapter(IActualsProvider):
    """
    Generic SQL warehouse adapter (Snowflake / BigQuery / Redshift / Postgres).
    Expects a SQLAlchemy-compatible connection URL and a query that returns:
    account_code, account_name, category, period, value [, business_unit, currency]
    """

    def __init__(self, connection_url: str | None = None):
        self.connection_url = connection_url

    async def pull_actuals(self, params: dict[str, Any]) -> ActualsPullResult:
        url = params.get("connection_url") or self.connection_url
        query = params.get("query")
        if not url or not query:
            return ActualsPullResult(
                success=False,
                error="warehouse adapter requires connection_url and query",
            )
        try:
            from sqlalchemy import create_engine, text

            engine = create_engine(url)
            with engine.connect() as conn:
                df = pd.read_sql(text(query), conn)
        except Exception as e:
            logger.exception("Warehouse pull failed")
            return ActualsPullResult(success=False, error=str(e))

        return self._normalize(df, source_name=params.get("source_name", "warehouse"))

    def _normalize(self, df: pd.DataFrame, source_name: str) -> ActualsPullResult:
        required = {"account_code", "period", "value"}
        missing = required - set(c.lower() for c in df.columns)
        # normalize columns to lower
        df = df.rename(columns={c: c.lower() for c in df.columns})
        if missing:
            # try after lower
            missing = required - set(df.columns)
        if missing:
            return ActualsPullResult(
                success=False,
                error=f"Query result missing columns: {sorted(missing)}",
            )
        if "account_name" not in df.columns:
            df["account_name"] = df["account_code"]
        if "category" not in df.columns:
            df["category"] = "Other"
        if "currency" not in df.columns:
            df["currency"] = "USD"

        periods = sorted(df["period"].astype(str).unique())
        payload = df.to_csv(index=False).encode()
        file_hash = hashlib.sha256(payload).hexdigest()
        return ActualsPullResult(
            success=True,
            dataframe=df,
            file_hash=file_hash,
            row_count=len(df),
            period_start=periods[0] if periods else None,
            period_end=periods[-1] if periods else None,
            periods_count=len(periods),
            metadata={
                "source": source_name,
                "pulled_at": datetime.now(timezone.utc).isoformat(),
                "unique_bus": int(df["business_unit"].nunique()) if "business_unit" in df.columns else 0,
                "categories": sorted(df["category"].dropna().unique().tolist())[:20],
            },
        )


class ERPRESTAdapter(IActualsProvider):
    """
    Generic ERP REST adapter. Fetches JSON from an authenticated endpoint.
    Expected JSON: list of {account_code, account_name, category, period, value, ...}
    """

    async def pull_actuals(self, params: dict[str, Any]) -> ActualsPullResult:
        url = params.get("url")
        if not url:
            return ActualsPullResult(success=False, error="erp adapter requires url")
        headers = params.get("headers") or {}
        token = params.get("token")
        if token:
            headers = {**headers, "Authorization": f"Bearer {token}"}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.exception("ERP REST pull failed")
            return ActualsPullResult(success=False, error=str(e))

        if isinstance(data, dict):
            data = data.get("records") or data.get("data") or data.get("items") or []
        if not isinstance(data, list):
            return ActualsPullResult(success=False, error="ERP response is not a list of records")
        df = pd.DataFrame(data)
        return WarehouseSQLAdapter()._normalize(df, source_name=params.get("source_name", "erp"))


def get_actuals_provider(source_type: str, **kwargs: Any) -> IActualsProvider:
    from app.services.ingestion.csv_adapter import CSVActualsProvider

    if source_type in ("csv", "excel", "file"):
        return CSVActualsProvider()
    if source_type in ("warehouse", "snowflake", "sql"):
        return WarehouseSQLAdapter(kwargs.get("connection_url"))
    if source_type in ("erp", "rest", "api"):
        return ERPRESTAdapter()
    return CSVActualsProvider()
