"""IngestActuals skill -- loads and validates actuals data from CSV/API."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.services.ingestion.csv_adapter import CSVActualsProvider
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)


class IngestActualsSkill(BaseSkill):
    """Skill to ingest historical actuals data from CSV files or APIs."""

    @property
    def name(self) -> str:
        return "ingest_actuals"

    @property
    def description(self) -> str:
        return (
            "Upload and validate historical financial actuals data. "
            "Accepts a CSV or Excel file path, validates data completeness and freshness, "
            "creates or updates line items in the chart of accounts, and stores the data "
            "with full lineage tracking. Use this when the user wants to load actuals data "
            "or upload financial data for forecasting."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the CSV or Excel file containing actuals data",
                },
                "source_type": {
                    "type": "string",
                    "description": "Data source type: csv (default) or api",
                    "default": "csv",
                    "enum": ["csv", "api"],
                },
            },
            "required": ["file_path"],
        }

    @property
    def required_role(self) -> str:
        return "generate"

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        """Ingest actuals from the specified source."""
        file_path = params.get("file_path", "")
        source_type = params.get("source_type", "csv")

        if not file_path:
            return SkillResult.fail("No file path provided. Please upload a file first.")

        db: Session = context.db

        # Use CSV adapter
        provider = CSVActualsProvider()
        result = await provider.pull_actuals({"file_path": file_path})

        if not result.success:
            return SkillResult.fail(
                f"Failed to ingest actuals: {result.error}",
                error=result.error,
            )

        df = result.dataframe

        # Create ActualsDataset record
        dataset = ActualsDataset(
            source_type=source_type,
            source_name=file_path.split("/")[-1],
            file_hash=result.file_hash,
            row_count=result.row_count,
            period_start=result.period_start,
            period_end=result.period_end,
            periods_count=result.periods_count,
            missing_periods=",".join(result.missing_periods) if result.missing_periods else None,
            completeness_pct=result.completeness_pct,
        )
        db.add(dataset)
        db.flush()  # Get ID

        # Create/update LineItems
        existing_items = {li.account_code: li for li in db.query(LineItem).all()}
        line_item_map = {}  # account_code -> LineItem
        new_items_count = 0

        accounts = df.groupby("account_code").first().reset_index()
        for _, row in accounts.iterrows():
            code = str(row["account_code"])
            if code in existing_items:
                line_item_map[code] = existing_items[code]
            else:
                li = LineItem(
                    account_code=code,
                    name=str(row.get("account_name", code)),
                    category=str(row.get("category", "Other")),
                    business_unit=str(row.get("business_unit", "")) if "business_unit" in row.index else None,
                    geography=str(row.get("geography", "")) if "geography" in row.index and pd.notna(row.get("geography")) else None,
                    product_line=str(row.get("product_line", "")) if "product_line" in row.index and pd.notna(row.get("product_line")) else None,
                )
                db.add(li)
                db.flush()
                line_item_map[code] = li
                existing_items[code] = li
                new_items_count += 1

        # Store actuals records
        records = []
        for _, row in df.iterrows():
            code = str(row["account_code"])
            li = line_item_map.get(code)
            if li:
                records.append(ActualsRecord(
                    dataset_id=dataset.id,
                    line_item_id=li.id,
                    period=str(row["period"]),
                    value=float(row["value"]),
                    currency=str(row.get("currency", "USD")),
                ))

        db.bulk_save_objects(records)
        db.commit()

        # Update working memory
        context.context_manager.set_memory("last_dataset_id", dataset.id)

        # Build response
        metadata = result.metadata
        bus = metadata.get("unique_bus", 0)
        categories = metadata.get("categories", [])

        content_blocks = [
            self._text_block(
                f"Successfully loaded actuals data from **{dataset.source_name}**"
            ),
            self._table_block(
                title="Ingestion Summary",
                columns=[
                    {"key": "metric", "label": "Metric"},
                    {"key": "value", "label": "Value"},
                ],
                rows=[
                    {"metric": "Total records", "value": str(result.row_count)},
                    {"metric": "Date range", "value": f"{result.period_start} to {result.period_end}"},
                    {"metric": "Periods", "value": str(result.periods_count)},
                    {"metric": "Line items", "value": str(len(line_item_map))},
                    {"metric": "New line items", "value": str(new_items_count)},
                    {"metric": "Business units", "value": str(bus)},
                    {"metric": "Categories", "value": ", ".join(categories[:5])},
                    {"metric": "Completeness", "value": f"{result.completeness_pct}%"},
                    {"metric": "Data hash", "value": result.file_hash[:12] + "..."},
                ],
            ),
        ]

        if result.missing_periods:
            content_blocks.append(
                self._text_block(
                    f"**Warning:** Missing periods detected: {', '.join(result.missing_periods[:5])}"
                    + (f" and {len(result.missing_periods) - 5} more" if len(result.missing_periods) > 5 else "")
                )
            )

        if result.warnings:
            for w in result.warnings:
                content_blocks.append(self._text_block(f"Note: {w}"))

        return SkillResult.ok(
            message=f"Loaded {result.row_count} actuals records ({result.periods_count} periods, {len(line_item_map)} line items) from {dataset.source_name}",
            data={
                "dataset_id": dataset.id,
                "row_count": result.row_count,
                "periods_count": result.periods_count,
                "line_items_count": len(line_item_map),
                "period_start": result.period_start,
                "period_end": result.period_end,
                "file_hash": result.file_hash,
            },
            content_blocks=content_blocks,
        )


# Need this import for pd.notna
import pandas as pd
