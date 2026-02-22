"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # Database
    database_url: str = "sqlite:///./rolling_forecast.db"

    # Auth
    jwt_secret_key: str = "dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 480

    # App
    app_name: str = "Rolling Forecast Assistant"
    app_env: str = "development"
    log_level: str = "INFO"

    # Paths
    upload_dir: str = str(Path(__file__).parent.parent / "uploads")
    seed_data_dir: str = str(Path(__file__).parent.parent / "seed_data")

    # Forecast defaults
    default_horizon_months: int = 12
    confidence_threshold_low: int = 50
    confidence_threshold_medium: int = 70
    max_forecast_generation_minutes: int = 30
    min_history_months: int = 12
    ideal_history_months: int = 24

    # Context Engine
    chroma_persist_dir: str = str(Path(__file__).parent.parent / "data" / "chroma")
    embedding_model: str = "all-MiniLM-L6-v2"
    context_chunk_size: int = 1500
    context_chunk_overlap: int = 200
    context_top_k: int = 5
    context_upload_dir: str = str(Path(__file__).parent.parent / "uploads" / "context")

    # External APIs
    perplexity_api_key: str = ""
    fred_api_key: str = ""
    financial_datasets_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
