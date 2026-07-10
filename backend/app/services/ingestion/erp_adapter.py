"""ERP / warehouse actuals adapters implementing IActualsProvider."""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.services.integration_safety import row_cap, validate_select_query, resolve_erp_url

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
    Expects a SQLAlchemy-compatible connection URL (from the connection registry)
    and a SELECT query that returns:
    account_code, account_name, category, period, value [, business_unit, currency]
    """

    def __init__(self, connection_url: str | None = None):
        self.connection_url = connection_url

    async def pull_actuals(self, params: dict[str, Any]) -> ActualsPullResult:
        # connection_url must come from the registry (constructor), never from caller params
        url = self.connection_url
        query = params.get("query")
        if not url or not query:
            return ActualsPullResult(
                success=False,
                error="warehouse adapter requires a registered connection and query",
            )
        try:
            safe_query = validate_select_query(query)
        except ValueError as e:
            return ActualsPullResult(success=False, error=str(e))

        try:
            from sqlalchemy import create_engine, text

            engine = create_engine(url)
            with engine.connect() as conn:
                # Enforce statement timeout where the dialect supports it
                try:
                    conn.execute(text("SET statement_timeout = 30000"))  # postgres ms
                except Exception:
                    pass
                df = pd.read_sql(text(safe_query), conn)
        except Exception as e:
            logger.exception("Warehouse pull failed")
            return ActualsPullResult(success=False, error=str(e))

        if len(df) > row_cap():
            return ActualsPullResult(
                success=False,
                error=f"Query returned {len(df)} rows; maximum allowed is {row_cap()}",
            )

        return self._normalize(df, source_name=params.get("source_name", "warehouse"))

    def _normalize(self, df: pd.DataFrame, source_name: str) -> ActualsPullResult:
        required = {"account_code", "period", "value"}
        missing = required - set(c.lower() for c in df.columns)
        df = df.rename(columns={c: c.lower() for c in df.columns})
        if missing:
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
    Generic ERP REST adapter. Base URL comes from the connection registry;
    callers supply a relative path only. Private IPs and redirects are blocked.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = base_url
        self.token = token

    async def pull_actuals(self, params: dict[str, Any]) -> ActualsPullResult:
        base = self.base_url
        relative = params.get("relative_path") or params.get("path") or ""
        if not base:
            return ActualsPullResult(success=False, error="erp adapter requires a registered base URL")
        try:
            url = resolve_erp_url(base, relative)
        except ValueError as e:
            return ActualsPullResult(success=False, error=str(e))

        headers = dict(params.get("headers") or {})
        token = self.token or params.get("token")
        # Ignore caller-supplied token when registry token exists; never accept
        # caller token as a substitute for a missing registry secret in hardened mode
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif token:
            # Legacy fallback only when adapter constructed without registry token
            headers["Authorization"] = f"Bearer {token}"

        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=False,
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code in (301, 302, 303, 307, 308):
                    return ActualsPullResult(
                        success=False,
                        error="ERP redirect blocked for SSRF protection",
                    )
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
        if len(df) > row_cap():
            return ActualsPullResult(
                success=False,
                error=f"ERP returned {len(df)} rows; maximum allowed is {row_cap()}",
            )
        return WarehouseSQLAdapter()._normalize(df, source_name=params.get("source_name", "erp"))


def get_actuals_provider(source_type: str, **kwargs: Any) -> IActualsProvider:
    from app.services.ingestion.csv_adapter import CSVActualsProvider

    if source_type in ("csv", "excel", "file"):
        return CSVActualsProvider()
    if source_type in ("warehouse", "snowflake", "sql"):
        return WarehouseSQLAdapter(kwargs.get("connection_url"))
    if source_type in ("erp", "rest", "api"):
        return ERPRESTAdapter(
            base_url=kwargs.get("base_url") or kwargs.get("connection_url"),
            token=kwargs.get("token"),
        )
    return CSVActualsProvider()
