"""Abstract interface for actuals data providers (adapter pattern)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import pandas as pd


@dataclass
class IngestionResult:
    """Result of an actuals data ingestion."""
    success: bool
    dataframe: pd.DataFrame | None = None
    row_count: int = 0
    period_start: str = ""
    period_end: str = ""
    periods_count: int = 0
    missing_periods: list[str] = field(default_factory=list)
    completeness_pct: float = 100.0
    file_hash: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class IActualsProvider(ABC):
    """
    Interface for actuals data sources.
    
    Each data source (CSV, API, warehouse, ERP) implements this interface.
    The core system never knows which specific source it's talking to.
    """

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Type identifier for this provider (e.g., 'csv', 'api', 'warehouse')."""
        ...

    @abstractmethod
    async def pull_actuals(self, source_config: dict[str, Any]) -> IngestionResult:
        """
        Pull actuals from the source.
        
        Args:
            source_config: Provider-specific configuration (file path, API URL, etc.)
            
        Returns:
            IngestionResult with validated DataFrame and metadata.
            Expected DataFrame columns:
                - account_code: str
                - account_name: str
                - category: str (Revenue, COGS, OpEx, etc.)
                - business_unit: str
                - geography: str (optional)
                - product_line: str (optional)
                - period: str (YYYY-MM format)
                - value: float
                - currency: str (default USD)
        """
        ...

    @abstractmethod
    async def validate(self, df: pd.DataFrame) -> list[str]:
        """
        Validate the ingested data.
        
        Returns list of warning messages (empty if all good).
        """
        ...
