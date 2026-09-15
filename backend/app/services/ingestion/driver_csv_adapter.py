"""CSV/Excel adapter for causal driver series ingestion."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import pandas as pd

from app.services.ingestion.csv_adapter import CSVActualsProvider

logger = logging.getLogger(__name__)

DRIVER_COLUMN_ALIASES = {
    "driver_key": ["driver_key", "key", "driver_id", "driver_code", "code"],
    "name": ["name", "driver_name", "description", "label"],
    "period": ["period", "date", "month", "fiscal_period"],
    "value": ["value", "amount", "balance", "actual"],
    "currency": ["currency", "ccy", "curr"],
    "unit": ["unit", "uom"],
    "driver_type": ["driver_type", "type", "kind"],
    "business_unit": ["business_unit", "bu", "department", "cost_center", "segment"],
    "geography": ["geography", "region", "geo", "country"],
    "product_line": ["product_line", "product", "product_group"],
    "aggregation": ["aggregation", "agg"],
}


class CSVDriverProvider:
    """Parse driver CSV/XLSX into a normalized DataFrame + lineage hash."""

    def __init__(self) -> None:
        # Reuse period normalization / calendar range from actuals adapter
        self._period_helper = CSVActualsProvider()

    async def pull_drivers(self, source_config: dict[str, Any]) -> dict[str, Any]:
        file_path = source_config.get("file_path", "")
        try:
            if file_path.endswith(".csv"):
                df = pd.read_csv(file_path)
            elif file_path.endswith((".xlsx", ".xls")):
                df = pd.read_excel(file_path)
            else:
                return {"success": False, "error": f"Unsupported file format: {file_path}"}

            if df.empty:
                return {"success": False, "error": "File contains no rows"}

            df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
            df = self._map_columns(df)

            missing = [c for c in ("driver_key", "period", "value") if c not in df.columns]
            if missing:
                return {
                    "success": False,
                    "error": f"Missing required columns: {missing}",
                    "columns": list(df.columns),
                }

            df["period"] = df["period"].apply(self._period_helper._normalize_period)
            df["driver_key"] = df["driver_key"].astype(str).str.strip()
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            null_vals = int(df["value"].isna().sum())
            df = df.dropna(subset=["driver_key", "period", "value"])

            if "currency" not in df.columns:
                df["currency"] = None
            if "name" not in df.columns:
                df["name"] = df["driver_key"]
            if "driver_type" not in df.columns:
                df["driver_type"] = "other"
            for col in ("unit", "business_unit", "geography", "product_line", "aggregation"):
                if col not in df.columns:
                    df[col] = None

            warnings: list[str] = []
            if null_vals:
                warnings.append(f"Dropped {null_vals} rows with non-numeric value")

            periods = sorted(df["period"].unique())
            expected = self._period_helper._get_expected_periods(periods[0], periods[-1])
            missing_periods = [p for p in expected if p not in periods]
            completeness = (
                (len(periods) / len(expected) * 100) if expected else 100.0
            )
            file_hash = hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()

            return {
                "success": True,
                "dataframe": df,
                "row_count": len(df),
                "period_start": periods[0],
                "period_end": periods[-1],
                "periods_count": len(periods),
                "missing_periods": missing_periods,
                "completeness_pct": round(completeness, 1),
                "file_hash": file_hash,
                "warnings": warnings,
                "unique_drivers": int(df["driver_key"].nunique()),
            }
        except Exception as e:
            logger.error("Driver CSV parse failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def _map_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        rename_map = {}
        for standard, aliases in DRIVER_COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in df.columns and standard not in df.columns:
                    rename_map[alias] = standard
                    break
        return df.rename(columns=rename_map) if rename_map else df
