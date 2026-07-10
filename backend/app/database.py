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
    """Run Alembic migrations to head when possible."""
    try:
        from alembic.config import Config
        from alembic import command

        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ini_path = os.path.join(backend_dir, "alembic.ini")
        if not os.path.exists(ini_path):
            logger.warning("alembic.ini not found; skipping migrations")
            return
        cfg = Config(ini_path)
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        command.upgrade(cfg, "head")
        logger.info("Alembic migrations applied")
    except Exception:
        logger.exception("Alembic migration failed; falling back to create_all")


def init_db():
    """Initialize schema.

    Production: prefer Alembic migrations (run on deploy / startup).
    Development: create_all for convenience after attempting migrations.
    """
    # Import models so metadata is populated
    import app.models  # noqa: F401

    if settings.is_production:
        run_migrations()
        # Safety net for new tables not yet in a migration
        Base.metadata.create_all(bind=engine)
    else:
        try:
            run_migrations()
        except Exception:
            pass
        Base.metadata.create_all(bind=engine)
