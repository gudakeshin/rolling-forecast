"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, SessionLocal
from app.domain.registry import register_all_skills
from app.models.user import Role, User
from passlib.context import CryptContext

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Prefer structlog JSON in non-dev; fall back to stdlib basicConfig."""
    level = getattr(logging, settings.log_level, logging.INFO)
    try:
        import structlog

        processors: list = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.CallsiteParameterAdder(),
        ]
        if settings.app_env == "development":
            processors.append(structlog.dev.ConsoleRenderer())
        else:
            processors.append(structlog.processors.JSONRenderer())

        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(level),
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
        logging.basicConfig(level=level, format="%(message)s")
    except ImportError:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )


_configure_logging()


def seed_roles_and_admin(db):
    """Seed default roles and (in non-prod) demo users."""
    roles_data = [
        {"name": "admin", "description": "System administrator",
         "can_input": True, "can_generate": True, "can_override": True,
         "can_review": True, "can_publish": True, "can_admin": True,
         "can_view_all_bus": True, "can_manage_drivers": True},
        {"name": "analyst", "description": "FP&A Analyst",
         "can_input": True, "can_generate": True, "can_override": True,
         "can_review": False, "can_publish": False, "can_admin": False,
         "can_view_all_bus": False, "can_manage_drivers": True},
        {"name": "reviewer", "description": "Finance Director / Reviewer",
         "can_input": True, "can_generate": True, "can_override": True,
         "can_review": True, "can_publish": False, "can_admin": False,
         "can_view_all_bus": True, "can_manage_drivers": True},
        {"name": "publisher", "description": "Can publish forecasts",
         "can_input": True, "can_generate": True, "can_override": True,
         "can_review": True, "can_publish": True, "can_admin": False,
         "can_view_all_bus": True, "can_manage_drivers": True},
        {"name": "input_provider", "description": "BU Head / Input Provider",
         "can_input": True, "can_generate": False, "can_override": False,
         "can_review": False, "can_publish": False, "can_admin": False,
         "can_view_all_bus": False, "can_manage_drivers": False},
    ]

    for role_data in roles_data:
        existing = db.query(Role).filter(Role.name == role_data["name"]).first()
        if not existing:
            db.add(Role(**role_data))
        else:
            # Keep can_view_all_bus / can_manage_drivers in sync for seeded roles
            if hasattr(existing, "can_view_all_bus"):
                existing.can_view_all_bus = role_data.get("can_view_all_bus", False)
            if hasattr(existing, "can_manage_drivers"):
                existing.can_manage_drivers = role_data.get("can_manage_drivers", False)

    db.commit()

    # Seed default approval workflow
    from app.models.approval import ApprovalWorkflow

    wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.name == "Standard Forecast Approval").first()
    if not wf:
        db.add(ApprovalWorkflow(
            name="Standard Forecast Approval",
            description="Analyst submit → Reviewer → Publisher",
            is_active=True,
            require_sod=True,
            levels=[
                {"level": 1, "role": "reviewer", "label": "Finance Director"},
                {"level": 2, "role": "publisher", "label": "FP&A Publisher / CFO delegate"},
            ],
        ))
        db.commit()

    seed_users = settings.seed_demo_users and not settings.is_production
    if not seed_users:
        logger.info("Skipping demo user seed (production or SEED_DEMO_USERS=false)")
        return

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    admin_role = db.query(Role).filter(Role.name == "admin").first()
    admin = db.query(User).filter(User.username == "admin").first()
    if not admin and admin_role:
        db.add(User(
            email="admin@forecast.local",
            username="admin",
            hashed_password=pwd_context.hash("admin"),
            full_name="System Admin",
            role_id=admin_role.id,
        ))

    analyst_role = db.query(Role).filter(Role.name == "analyst").first()
    analyst = db.query(User).filter(User.username == "analyst").first()
    if not analyst and analyst_role:
        db.add(User(
            email="analyst@forecast.local",
            username="analyst",
            hashed_password=pwd_context.hash("analyst"),
            full_name="Demo Analyst",
            business_unit="North America",
            role_id=analyst_role.id,
        ))

    reviewer_role = db.query(Role).filter(Role.name == "reviewer").first()
    reviewer = db.query(User).filter(User.username == "reviewer").first()
    if not reviewer and reviewer_role:
        db.add(User(
            email="reviewer@forecast.local",
            username="reviewer",
            hashed_password=pwd_context.hash("reviewer"),
            full_name="Demo Reviewer",
            role_id=reviewer_role.id,
        ))

    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    logger.info("Starting Rolling Forecast API...")

    if settings.is_production:
        settings.validate_production_secrets()

    # Fail fast on bad Anthropic key/model (hard in prod; warn in dev)
    settings.validate_llm_config()

    init_db()
    logger.info("Database initialized")

    db = SessionLocal()
    try:
        seed_roles_and_admin(db)
        logger.info("Default roles and users seeded")
    finally:
        db.close()

    registry = register_all_skills()
    logger.info(f"Skills registry initialized with {len(registry.list_names())} skills")

    seed_path = os.path.join(settings.seed_data_dir, "sample_actuals.csv")
    if not os.path.exists(seed_path) and not settings.is_production:
        from seed_data.generate_seed import generate_seed_data
        generate_seed_data(seed_path)
        logger.info("Seed data generated")

    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.context_upload_dir, exist_ok=True)
    os.makedirs(settings.export_dir, exist_ok=True)

    # Optional OpenTelemetry
    if settings.otel_enabled:
        try:
            from app.services.observability import setup_observability
            setup_observability(app)
        except Exception:
            logger.exception("Failed to initialize observability")

    yield

    logger.info("Shutting down Rolling Forecast API...")


app = FastAPI(
    title=settings.app_name,
    description="AI-powered Rolling Forecast Generation & Refresh platform",
    version="0.2.0",
    lifespan=lifespan,
)

from app.middleware import RequestLoggingMiddleware, register_exception_handlers

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
)
register_exception_handlers(app)

# Rate limiting (Redis-backed when REDIS_URL is set)
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.rate_limit import limiter

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.upload import router as upload_router
from app.api.panel import router as panel_router
from app.api.dashboard import router as dashboard_router
from app.api.skills import router as skills_router
from app.api.context import router as context_router
from app.api.admin import router as admin_router
from app.api.admin_integrations import router as admin_integrations_router
from app.api.admin_fx import router as admin_fx_router
from app.api.executive import router as executive_router
from app.api.approvals import router as approvals_router
from app.api.integrations import router as integrations_router
from app.api.locks import router as locks_router
from app.api.jobs import router as jobs_router
from app.api.drivers import router as drivers_router
from app.api.scenarios import router as scenarios_router
from app.api.memory import router as memory_router
from app.api.heuristics import router as heuristics_router

app.include_router(health_router)
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(upload_router, prefix="/api")
app.include_router(panel_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(skills_router)
app.include_router(context_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(admin_integrations_router, prefix="/api")
app.include_router(admin_fx_router, prefix="/api")
app.include_router(executive_router, prefix="/api")
app.include_router(approvals_router, prefix="/api")
app.include_router(integrations_router, prefix="/api")
app.include_router(locks_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(drivers_router, prefix="/api")
app.include_router(scenarios_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(heuristics_router, prefix="/api")

# Prometheus metrics (skip in tests — instrumentator breaks on Starlette Mount routes)
if settings.app_env != "test":
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_group_status_codes=True,
            excluded_handlers=["/metrics", "/livez", "/readyz", "/health"],
        ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except ImportError:
        pass
