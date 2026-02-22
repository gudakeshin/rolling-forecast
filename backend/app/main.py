"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, SessionLocal
from app.domain.registry import register_all_skills
from app.models.user import Role, User
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def seed_roles_and_admin(db):
    """Seed default roles and admin user if they don't exist."""
    roles_data = [
        {"name": "admin", "description": "System administrator",
         "can_input": True, "can_generate": True, "can_override": True,
         "can_review": True, "can_publish": True, "can_admin": True},
        {"name": "analyst", "description": "FP&A Analyst",
         "can_input": True, "can_generate": True, "can_override": True,
         "can_review": False, "can_publish": False, "can_admin": False},
        {"name": "reviewer", "description": "Finance Director / Reviewer",
         "can_input": True, "can_generate": True, "can_override": True,
         "can_review": True, "can_publish": False, "can_admin": False},
        {"name": "publisher", "description": "Can publish forecasts",
         "can_input": True, "can_generate": True, "can_override": True,
         "can_review": True, "can_publish": True, "can_admin": False},
        {"name": "input_provider", "description": "BU Head / Input Provider",
         "can_input": True, "can_generate": False, "can_override": False,
         "can_review": False, "can_publish": False, "can_admin": False},
    ]

    for role_data in roles_data:
        existing = db.query(Role).filter(Role.name == role_data["name"]).first()
        if not existing:
            db.add(Role(**role_data))

    db.commit()

    # Create default admin user
    admin_role = db.query(Role).filter(Role.name == "admin").first()
    admin = db.query(User).filter(User.username == "admin").first()
    if not admin and admin_role:
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        admin = User(
            email="admin@forecast.local",
            username="admin",
            hashed_password=pwd_context.hash("admin"),
            full_name="System Admin",
            role_id=admin_role.id,
        )
        db.add(admin)

    # Create a demo analyst
    analyst_role = db.query(Role).filter(Role.name == "analyst").first()
    analyst = db.query(User).filter(User.username == "analyst").first()
    if not analyst and analyst_role:
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        analyst = User(
            email="analyst@forecast.local",
            username="analyst",
            hashed_password=pwd_context.hash("analyst"),
            full_name="Demo Analyst",
            business_unit="North America",
            role_id=analyst_role.id,
        )
        db.add(analyst)

    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # Startup
    logger.info("Starting Rolling Forecast API...")

    # Initialize database
    init_db()
    logger.info("Database initialized")

    # Seed roles and admin
    db = SessionLocal()
    try:
        seed_roles_and_admin(db)
        logger.info("Default roles and users seeded")
    finally:
        db.close()

    # Register all skills
    registry = register_all_skills()
    logger.info(f"Skills registry initialized with {len(registry.list_names())} skills")

    # Generate seed data if not exists
    seed_path = os.path.join(settings.seed_data_dir, "sample_actuals.csv")
    if not os.path.exists(seed_path):
        from seed_data.generate_seed import generate_seed_data
        generate_seed_data(seed_path)
        logger.info("Seed data generated")

    # Ensure upload directories exist
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.context_upload_dir, exist_ok=True)

    yield

    # Shutdown
    logger.info("Shutting down Rolling Forecast API...")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="AI-powered Rolling Forecast Generation & Refresh platform",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware
from app.middleware import RequestLoggingMiddleware, register_exception_handlers

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)

# Include routers
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.upload import router as upload_router
from app.api.panel import router as panel_router
from app.api.dashboard import router as dashboard_router
from app.api.skills import router as skills_router
from app.api.context import router as context_router

app.include_router(health_router)
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(upload_router, prefix="/api")
app.include_router(panel_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(skills_router)
app.include_router(context_router, prefix="/api")
