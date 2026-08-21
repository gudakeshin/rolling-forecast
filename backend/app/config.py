"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # Database
    database_url: str = "sqlite:///./rolling_forecast.db"
    # Connection pool (non-SQLite only). Defaults sized for the 2-worker
    # uvicorn prod command; raise pool_size before raising --workers.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    # Auth
    jwt_secret_key: str = "dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    # Access token TTL — 480 min (8h) preserves local/dev UX; set JWT_EXPIRY_MINUTES=30 in prod
    jwt_expiry_minutes: int = 480
    jwt_refresh_days: int = 14
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
    # Host header allowlist (comma-separated). Opt-in: the middleware is only
    # installed when this is set, so existing deployments are unaffected.
    # Loopback is always appended so container healthchecks keep working.
    allowed_hosts: str = ""

    # App
    app_name: str = "Rolling Forecast Assistant"
    app_env: str = "development"
    log_level: str = "INFO"

    # Observability
    otel_enabled: bool = False
    otel_service_name: str = "rolling-forecast"
    otel_exporter_otlp_endpoint: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Redis (locks / rate-limit / OIDC store / arq job queue)
    redis_url: str = ""
    # When true, generate_baseline must enqueue via arq; missing Redis → clear failure (no silent sync).
    # Also forced on when APP_ENV=production via `async_jobs_required` (compose may still set the env).
    require_async_jobs: bool = False

    # Warehouse / ERP (legacy env fallbacks — prefer connection registry)
    warehouse_connection_url: str = ""
    erp_api_url: str = ""
    erp_api_token: str = ""
    integration_secret_key: str = ""  # Fernet material; falls back to jwt_secret_key

    # Upload limits
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MB

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
    # arq worker: hard job timeout + progress stall watchdog
    job_timeout_seconds: int = 3600
    job_progress_stall_minutes: int = 10
    # Model selection rollout (independent switches)
    # mape = legacy MAPE-only; multi = MASE → pinball → complexity_rank
    selection_metric: str = "multi"
    enable_benchmark_models: bool = True
    # Per-line-item wall-clock budget for model comparison (seconds)
    selection_wall_clock_seconds: float = 90.0
    # Trailing-actuals share of CoA total to treat a line as material (expensive models)
    materiality_share: float = 0.005
    # Max |Q·P − L| / |L| to admit identity Q×P decomposition (Phase 4/6b)
    qp_coherence_tolerance: float = 0.02
    # Phase 8: exogenous regressors (driver-based forecasting)
    enable_driver_forecasting: bool = False
    # Minimum effective training points per regressor for exog admit gate.
    exog_min_points_per_regressor: int = 10
    # Soft per-conversation LLM token budget (approx chars/4). Soft-warn then hard-stop.
    conversation_token_budget: int = 200_000
    # M1 core memory caps (chars)
    core_memory_char_limit_persona: int = 1000
    core_memory_char_limit_organization: int = 4000
    core_memory_char_limit_user: int = 1500
    core_memory_char_limit_business_unit: int = 2500
    core_memory_prompt_char_budget: int = 6000
    min_history_months: int = 12
    ideal_history_months: int = 24
    panel_page_size: int = 100
    audit_retention_days: int = 2555
    # Pre-fit outlier cleaning (STL remainder + MAD winsorize)
    outlier_cleaning_enabled: bool = True
    outlier_mad_z: float = 3.5
    # Structural break: require this many post-break points before truncating
    structural_break_min_post_points: int = 12

    # Context Engine
    chroma_persist_dir: str = str(Path(__file__).parent.parent / "data" / "chroma")
    embedding_model: str = "all-MiniLM-L6-v2"
    # "onnx" uses the all-MiniLM-L6-v2 graph bundled with Chroma — same weights
    # as the sentence-transformers build, but without the torch dependency.
    # "sentence_transformers" is opt-in and needs that extra installed.
    embedding_backend: str = "onnx"
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
    def async_jobs_required(self) -> bool:
        """True when Redis/arq is mandatory (explicit flag or production)."""
        return self.require_async_jobs or self.is_production

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        """Configured hosts plus loopback (Docker HEALTHCHECK hits localhost)."""
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        if not hosts:
            return []
        for loopback in ("localhost", "127.0.0.1"):
            if loopback not in hosts:
                hosts.append(loopback)
        return hosts

    def validate_production_secrets(self) -> None:
        if not self.is_production:
            return
        weak = {"dev-secret-key-change-in-production", "change-me-in-production", ""}
        if self.jwt_secret_key in weak or len(self.jwt_secret_key) < 32:
            raise RuntimeError(
                "JWT_SECRET_KEY must be set to a strong secret (>=32 chars) in production"
            )
        # Reject obviously weak / default DB credentials in the URL
        db = (self.database_url or "").lower()
        if "forecast:forecast@" in db or "postgres:postgres@" in db or "password@" in db:
            raise RuntimeError(
                "DATABASE_URL must not use default/weak credentials in production"
            )
        if self.oidc_enabled and (
            not self.oidc_issuer or not self.oidc_client_id or not self.oidc_client_secret
        ):
            raise RuntimeError("OIDC is enabled but issuer/client_id/client_secret are incomplete")
        if not self.cors_origin_list:
            raise RuntimeError("CORS_ORIGINS must be set explicitly in production")
        if "*" in self.cors_origin_list:
            raise RuntimeError("CORS_ORIGINS must not include '*' when credentials are enabled")

    def validate_llm_config(self, *, fail_hard: bool | None = None) -> None:
        """Fail fast when Anthropic key/model are misconfigured.

        Always checks that the API key is present (hard fail in production).
        In production, also makes a cheap 1-token call to verify the model id.
        Skipped entirely when APP_ENV=test.
        """
        import logging

        log = logging.getLogger("app.config")
        hard = self.is_production if fail_hard is None else fail_hard

        if self.app_env.lower() == "test":
            return

        if not self.anthropic_api_key:
            msg = (
                "ANTHROPIC_API_KEY is not set — chat and agent skills will fail. "
                f"Configured model: {self.anthropic_model!r}"
            )
            if hard:
                raise RuntimeError(msg)
            log.warning("═" * 60)
            log.warning("LLM CONFIG WARNING: %s", msg)
            log.warning("═" * 60)
            return

        log.info("LLM config: model=%s key=present", self.anthropic_model)

        # Live model-id check only in production (or when fail_hard forced)
        if not hard:
            return

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.anthropic_api_key, timeout=15.0)
            client.messages.create(
                model=self.anthropic_model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            log.info("LLM model validation OK — model=%s", self.anthropic_model)
        except Exception as exc:
            msg = (
                f"Anthropic model validation failed for model={self.anthropic_model!r}: {exc}. "
                "Fix ANTHROPIC_API_KEY / ANTHROPIC_MODEL before serving traffic."
            )
            raise RuntimeError(msg) from exc


settings = Settings()
