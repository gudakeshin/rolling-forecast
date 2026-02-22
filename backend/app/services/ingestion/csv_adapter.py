"""CSV/Excel adapter for actuals data ingestion."""

import hashlib
import logging
from typing import Any

import pandas as pd
import numpy as np

from app.services.ingestion.base import IActualsProvider, IngestionResult

logger = logging.getLogger(__name__)

# Expected column mappings (flexible naming)
COLUMN_ALIASES = {
    "account_code": ["account_code", "account_id", "gl_code", "code", "acct_code"],
    "account_name": ["account_name", "name", "description", "line_item", "gl_name"],
    "category": ["category", "type", "account_type", "pl_category"],
    "business_unit": ["business_unit", "bu", "department", "cost_center", "segment"],
    "geography": ["geography", "region", "geo", "country"],
    "product_line": ["product_line", "product", "product_group"],
    "period": ["period", "date", "month", "fiscal_period"],
    "value": ["value", "amount", "balance", "actual"],
    "currency": ["currency", "ccy", "curr"],
}


class CSVActualsProvider(IActualsProvider):
    """Adapter for ingesting actuals from CSV or Excel files."""

    @property
    def source_type(self) -> str:
        return "csv"

    async def pull_actuals(self, source_config: dict[str, Any]) -> IngestionResult:
        """Load and validate actuals from a CSV/Excel file."""
        file_path = source_config.get("file_path", "")

        try:
            # Read file
            if file_path.endswith(".csv"):
                df = pd.read_csv(file_path)
            elif file_path.endswith((".xlsx", ".xls")):
                df = pd.read_excel(file_path)
            else:
                return IngestionResult(
                    success=False, error=f"Unsupported file format: {file_path}"
                )

            # Normalize column names
            df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
            df = self._map_columns(df)

            # Compute file hash for lineage
            file_hash = self._compute_hash(df)

            # Validate
            warnings = await self.validate(df)

            # Ensure period format is YYYY-MM
            df["period"] = df["period"].apply(self._normalize_period)

            # Fill missing currency
            if "currency" not in df.columns:
                df["currency"] = "USD"

            # Fill optional columns
            for col in ["geography", "product_line"]:
                if col not in df.columns:
                    df[col] = None

            # Compute metadata
            periods = sorted(df["period"].unique())
            all_expected_periods = self._get_expected_periods(periods[0], periods[-1])
            missing = [p for p in all_expected_periods if p not in periods]

            completeness = (len(periods) / len(all_expected_periods) * 100) if all_expected_periods else 100.0

            return IngestionResult(
                success=True,
                dataframe=df,
                row_count=len(df),
                period_start=periods[0],
                period_end=periods[-1],
                periods_count=len(periods),
                missing_periods=missing,
                completeness_pct=round(completeness, 1),
                file_hash=file_hash,
                warnings=warnings,
                metadata={
                    "columns": list(df.columns),
                    "unique_accounts": df["account_code"].nunique(),
                    "unique_bus": df["business_unit"].nunique() if "business_unit" in df.columns else 0,
                    "categories": df["category"].unique().tolist() if "category" in df.columns else [],
                },
            )

        except Exception as e:
            logger.error(f"CSV ingestion failed: {e}", exc_info=True)
            return IngestionResult(success=False, error=str(e))

    async def validate(self, df: pd.DataFrame) -> list[str]:
        """Validate the DataFrame for data quality issues."""
        warnings = []

        # Check required columns
        required = ["account_code", "account_name", "period", "value"]
        missing_cols = [c for c in required if c not in df.columns]
        if missing_cols:
            warnings.append(f"Missing required columns: {missing_cols}")

        # Check for null values in critical columns
        for col in ["account_code", "period", "value"]:
            if col in df.columns:
                null_count = df[col].isnull().sum()
                if null_count > 0:
                    warnings.append(f"Column '{col}' has {null_count} null values")

        # Check for unexpected zero balances
        if "value" in df.columns:
            zero_accounts = (
                df.groupby("account_code")["value"]
                .apply(lambda x: (x == 0).all())
            )
            all_zero = zero_accounts[zero_accounts].index.tolist()
            if all_zero:
                warnings.append(
                    f"{len(all_zero)} accounts have all-zero balances: {all_zero[:5]}{'...' if len(all_zero) > 5 else ''}"
                )

        # Check data freshness
        if "period" in df.columns:
            latest_period = df["period"].max()
            warnings.append(f"Most recent actuals period: {latest_period}")

        return warnings

    def _map_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map flexible column names to standard names."""
        rename_map = {}
        for standard_name, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in df.columns and standard_name not in df.columns:
                    rename_map[alias] = standard_name
                    break
        if rename_map:
            df = df.rename(columns=rename_map)
        return df

    def _normalize_period(self, period_val) -> str:
        """Normalize various period formats to YYYY-MM."""
        period_str = str(period_val).strip()
        # Try common formats
        for fmt in ["%Y-%m", "%Y/%m", "%m/%Y", "%Y-%m-%d", "%m/%d/%Y"]:
            try:
                dt = pd.to_datetime(period_str, format=fmt)
                return dt.strftime("%Y-%m")
            except (ValueError, TypeError):
                continue
        # Fallback: try pandas auto-detect
        try:
            dt = pd.to_datetime(period_str)
            return dt.strftime("%Y-%m")
        except Exception:
            return period_str  # Return as-is

    def _compute_hash(self, df: pd.DataFrame) -> str:
        """Compute SHA-256 hash of the DataFrame for lineage tracking."""
        content = df.to_csv(index=False).encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def _get_expected_periods(self, start: str, end: str) -> list[str]:
        """Generate a list of all expected YYYY-MM periods between start and end."""
        try:
            date_range = pd.date_range(start=start + "-01", end=end + "-01", freq="MS")
            return [d.strftime("%Y-%m") for d in date_range]
        except Exception:
            return []
