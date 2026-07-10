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
    allow_open_registration: bool = True  # Forced False when app_env=production
    seed_demo_users: bool = True  # Forced False when app_env=production

    # OIDC / SSO
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/oidc/callback"
    oidc_scopes: str = "openid profile email"

    # CORS (comma-separated)
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # App
    app_name: str = "Rolling Forecast Assistant"
    app_env: str = "development"
    log_level: str = "INFO"

    # Observability
    otel_enabled: bool = False
    otel_service_name: str = "rolling-forecast"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Warehouse / ERP
    warehouse_connection_url: str = ""
    erp_api_url: str = ""
    erp_api_token: str = ""

    # Distribution
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    distribution_from_email: str = "forecasts@example.com"

    # Paths
    upload_dir: str = str(Path(__file__).parent.parent / "uploads")
    seed_data_dir: str = str(Path(__file__).parent.parent / "seed_data")
    export_dir: str = str(Path(__file__).parent.parent / "exports")

    # Forecast defaults
    default_horizon_months: int = 12
    confidence_threshold_low: int = 50
    confidence_threshold_medium: int = 70
    max_forecast_generation_minutes: int = 30
    min_history_months: int = 12
    ideal_history_months: int = 24
    panel_page_size: int = 100
    audit_retention_days: int = 2555

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

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_production_secrets(self) -> None:
        if not self.is_production:
            return
        weak = {"dev-secret-key-change-in-production", "change-me-in-production", ""}
        if self.jwt_secret_key in weak or len(self.jwt_secret_key) < 32:
            raise RuntimeError(
                "JWT_SECRET_KEY must be set to a strong secret (>=32 chars) in production"
            )


settings = Settings()
