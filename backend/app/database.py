"""Database engine, session, and base model configuration."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings


# SQLite needs check_same_thread=False for FastAPI's async context
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


def init_db():
    """Create all tables.

    In development: uses create_all() for convenience.
    In production: use Alembic migrations instead:
        cd backend
        alembic upgrade head

    For existing databases being migrated to Alembic, stamp the current
    revision to avoid re-creating tables:
        alembic stamp head
    """
    Base.metadata.create_all(bind=engine)
