"""Shared test fixtures for the Rolling Forecast test suite."""

import os
import pytest
import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Override database before importing app modules
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_app.db")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-ci-at-least-32-chars")
os.environ.setdefault("SEED_DEMO_USERS", "true")

from app.database import Base
from app.models.user import Role, User
from app.models.line_item import LineItem, LineItemDependency
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.override import Override
from app.domain.base_skill import SkillContext
from passlib.context import CryptContext


# Unit-test DB is isolated from the app's test_app.db used by TestClient
UNIT_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def db_engine():
    """In-memory DB for unit tests — never touches the app TestClient database file."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        UNIT_DB_URL,
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    """Create a database session for each test."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def seed_roles(db_session):
    """Seed default roles (idempotent — lifespan may have already created them)."""
    specs = [
        dict(name="admin", description="Admin", can_input=True, can_generate=True,
             can_override=True, can_review=True, can_publish=True, can_admin=True,
             can_view_all_bus=True),
        dict(name="analyst", description="Analyst", can_input=True, can_generate=True,
             can_override=True, can_review=False, can_publish=False, can_admin=False,
             can_view_all_bus=False),
    ]
    out = {}
    for spec in specs:
        existing = db_session.query(Role).filter(Role.name == spec["name"]).first()
        if existing:
            for k, v in spec.items():
                if k != "name" and hasattr(existing, k):
                    setattr(existing, k, v)
            out[spec["name"]] = existing
        else:
            role = Role(**spec)
            db_session.add(role)
            out[spec["name"]] = role
    db_session.commit()
    return out


@pytest.fixture
def seed_users(db_session, seed_roles):
    """Seed test users (idempotent)."""
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def _user(username, email, role_key, **extra):
        existing = db_session.query(User).filter(User.username == username).first()
        if existing:
            return existing
        u = User(
            email=email,
            username=username,
            hashed_password=pwd_ctx.hash(username),
            full_name=extra.pop("full_name", username),
            role_id=seed_roles[role_key].id,
            **extra,
        )
        db_session.add(u)
        return u

    admin = _user("admin", "admin@test.local", "admin", full_name="Test Admin")
    analyst = _user(
        "analyst", "analyst@test.local", "analyst",
        full_name="Test Analyst", business_unit="North America",
    )
    db_session.commit()
    return {"admin": admin, "analyst": analyst}


@pytest.fixture
def seed_line_items(db_session):
    """Seed test line items for P&L."""
    items = [
        LineItem(account_code="REV-001", name="Product Revenue", category="Revenue",
                 display_order=1, allow_negative=False),
        LineItem(account_code="REV-002", name="Service Revenue", category="Revenue",
                 display_order=2, allow_negative=False),
        LineItem(account_code="COGS-001", name="Cost of Goods Sold", category="COGS",
                 display_order=3, sign_convention="negative"),
        LineItem(account_code="OPEX-001", name="Salaries & Benefits", category="OpEx",
                 display_order=4, sign_convention="negative"),
        LineItem(account_code="OPEX-002", name="Marketing Expense", category="OpEx",
                 display_order=5, sign_convention="negative"),
        LineItem(account_code="EBITDA", name="EBITDA", category="EBITDA",
                 display_order=10, is_calculated=True, is_subtotal=True, indent_level=0,
                 allow_negative=True),
    ]
    db_session.add_all(items)
    db_session.commit()
    return {li.account_code: li for li in items}


@pytest.fixture
def seed_actuals(db_session, seed_line_items):
    """Seed 24 months of actuals data."""
    import numpy as np

    dataset = ActualsDataset(
        source_type="csv",
        source_name="test_actuals.csv",
        file_hash="test_hash_abc123",
        row_count=0,
        period_start="2024-01",
        period_end="2025-12",
        periods_count=24,
        completeness_pct=100.0,
    )
    db_session.add(dataset)
    db_session.flush()

    np.random.seed(42)
    total_records = 0

    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue

        base_values = {
            "REV-001": 500000,
            "REV-002": 200000,
            "COGS-001": 300000,
            "OPEX-001": 150000,
            "OPEX-002": 80000,
        }
        base = base_values.get(code, 100000)

        for month in range(24):
            year = 2024 + month // 12
            m = (month % 12) + 1
            period = f"{year}-{m:02d}"

            # Add some trend and noise
            trend = base * (1 + 0.005 * month)
            seasonality = base * 0.05 * np.sin(2 * np.pi * month / 12)
            noise = np.random.normal(0, base * 0.03)
            value = trend + seasonality + noise

            record = ActualsRecord(
                dataset_id=dataset.id,
                line_item_id=li.id,
                period=period,
                value=round(value, 2),
                currency="USD",
            )
            db_session.add(record)
            total_records += 1

    dataset.row_count = total_records
    db_session.commit()
    return dataset


class MockContextManager:
    """Lightweight context manager for testing without a full Conversation model."""

    def __init__(self):
        self._memory: dict = {}

    def get_memory(self, key, default=None):
        return self._memory.get(key, default)

    def set_memory(self, key, value):
        self._memory[key] = value

    def get_active_version_id(self):
        return self.get_memory("active_version_id")

    def set_active_version_id(self, version_id):
        self.set_memory("active_version_id", version_id)

    def has_permission(self, permission):
        return True  # Allow all permissions in tests


@pytest.fixture
def context_manager():
    """Create a test context manager."""
    return MockContextManager()


@pytest.fixture
def skill_context(db_session, seed_users, context_manager):
    """Create a SkillContext for testing skills."""
    return SkillContext(
        db=db_session,
        context_manager=context_manager,
        user_id=seed_users["analyst"].id,
        user_role="analyst",
        conversation_id="test-conversation-001",
    )
