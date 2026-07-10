"""Database engine, session, and base model configuration."""

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=(settings.app_env == "development"),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Run Alembic migrations to head. Raises on failure (callers may catch in non-prod)."""
    from alembic.config import Config
    from alembic import command

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ini_path = os.path.join(backend_dir, "alembic.ini")
    if not os.path.exists(ini_path):
        raise FileNotFoundError(f"alembic.ini not found at {ini_path}")
    cfg = Config(ini_path)
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
    logger.info("Alembic migrations applied")


def init_db():
    """Initialize schema.

    Production: Alembic migrations only (never create_all) — fail hard on error.
    Test: create_all only (avoid Alembic dual-engine SQLite locks).
    Development: try migrations, then create_all for convenience.
    """
    # Import models so metadata is populated
    import app.models  # noqa: F401

    if settings.app_env.lower() == "test":
        Base.metadata.create_all(bind=engine)
        return

    if settings.is_production:
        # Production relies solely on Alembic — never mask migration drift with create_all
        run_migrations()
        return

    try:
        run_migrations()
    except Exception:
        logger.exception("Alembic migration failed in development; using create_all")
    Base.metadata.create_all(bind=engine)
