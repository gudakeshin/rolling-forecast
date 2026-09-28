"""Shared test fixtures for the Rolling Forecast test suite."""

import os
import pytest
import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# Override database before importing app modules
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_app.db")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-ci-at-least-32-chars")
os.environ.setdefault("SEED_DEMO_USERS", "true")

from app.database import Base
from app.models.user import Role, User
from app.models.business_unit import BusinessUnit
from app.models.line_item import LineItem, LineItemDependency
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.override import Override
from app.domain.base_skill import SkillContext
from passlib.context import CryptContext


# Unit-test DB is isolated from the app's test_app.db used by TestClient
UNIT_DB_URL = "sqlite:///:memory:"


def ensure_app_db_schema_columns() -> None:
    """Persistent test_app.db may predate new columns; create_all won't ALTER."""
    from app.database import engine

    try:
        flr_cols = {c["name"] for c in inspect(engine).get_columns("forecast_line_results")}
    except Exception:
        return
    with engine.begin() as conn:
        if "pre_reconcile_p50" not in flr_cols:
            conn.execute(text("ALTER TABLE forecast_line_results ADD COLUMN pre_reconcile_p50 FLOAT"))
        fv_cols = {c["name"] for c in inspect(engine).get_columns("forecast_versions")}
        if "selection_rule" not in fv_cols:
            conn.execute(text("ALTER TABLE forecast_versions ADD COLUMN selection_rule VARCHAR(64)"))
        if "model_preset_id" not in fv_cols:
            conn.execute(
                text("ALTER TABLE forecast_versions ADD COLUMN model_preset_id VARCHAR(36)")
            )
        if "model_mase" not in flr_cols:
            conn.execute(text("ALTER TABLE forecast_line_results ADD COLUMN model_mase FLOAT"))
        if "model_pinball" not in flr_cols:
            conn.execute(text("ALTER TABLE forecast_line_results ADD COLUMN model_pinball FLOAT"))
        if "is_target_bearing" not in {
            c["name"] for c in inspect(engine).get_columns("line_items")
        }:
            conn.execute(
                text("ALTER TABLE line_items ADD COLUMN is_target_bearing BOOLEAN DEFAULT 1")
            )
        ad_cols = {c["name"] for c in inspect(engine).get_columns("actuals_datasets")}
        if "is_pinned" not in ad_cols:
            conn.execute(
                text("ALTER TABLE actuals_datasets ADD COLUMN is_pinned BOOLEAN DEFAULT 0")
            )
        if "integration_connection_id" not in ad_cols:
            conn.execute(
                text("ALTER TABLE actuals_datasets ADD COLUMN integration_connection_id VARCHAR(36)")
            )
        conn.execute(text("CREATE TABLE IF NOT EXISTS business_units ("
                           "id VARCHAR(36) PRIMARY KEY, name VARCHAR(100) UNIQUE NOT NULL, "
                           "created_at DATETIME NOT NULL)"))
        if "business_unit_id" not in ad_cols:
            conn.execute(text("ALTER TABLE actuals_datasets ADD COLUMN business_unit_id VARCHAR(36)"))
        if "storage_path" not in ad_cols:
            conn.execute(text("ALTER TABLE actuals_datasets ADD COLUMN storage_path TEXT"))
        if "business_unit_id" not in {
            c["name"] for c in inspect(engine).get_columns("users")
        }:
            conn.execute(text("ALTER TABLE users ADD COLUMN business_unit_id VARCHAR(36)"))
        if "business_unit_id" not in {
            c["name"] for c in inspect(engine).get_columns("line_items")
        }:
            conn.execute(text("ALTER TABLE line_items ADD COLUMN business_unit_id VARCHAR(36)"))
        if "business_unit_id" not in fv_cols:
            conn.execute(text("ALTER TABLE forecast_versions ADD COLUMN business_unit_id VARCHAR(36)"))
        try:
            drv_cols = {c["name"] for c in inspect(engine).get_columns("drivers")}
            if "business_unit_id" not in drv_cols:
                conn.execute(text("ALTER TABLE drivers ADD COLUMN business_unit_id VARCHAR(36)"))
        except Exception:
            pass
        try:
            di_cols = {c["name"] for c in inspect(engine).get_columns("driver_inputs")}
            if "business_unit_id" not in di_cols:
                conn.execute(text("ALTER TABLE driver_inputs ADD COLUMN business_unit_id VARCHAR(36)"))
        except Exception:
            pass
        try:
            dfc_cols = {c["name"] for c in inspect(engine).get_columns("driver_form_configs")}
            if "business_unit_id" not in dfc_cols:
                conn.execute(
                    text("ALTER TABLE driver_form_configs ADD COLUMN business_unit_id VARCHAR(36)")
                )
        except Exception:
            pass
        try:
            ic_cols = {c["name"] for c in inspect(engine).get_columns("integration_connections")}
            if "business_unit_id" not in ic_cols:
                conn.execute(
                    text("ALTER TABLE integration_connections ADD COLUMN business_unit_id VARCHAR(36)")
                )
        except Exception:
            pass

        # Backfill pre-existing rows (from before business_unit_id existed)
        # into one shared demo company -- otherwise a fresh column add alone
        # leaves them NULL, and under the "no shared bucket" scoping policy
        # (app/services/permissions.py) the demo analyst would see none of
        # them. Matches the name main.py's seed_roles_and_admin assigns the
        # demo analyst, so both resolve to the same row.
        row = conn.execute(
            text("SELECT id FROM business_units WHERE name = 'North America'")
        ).first()
        if row:
            demo_bu_id = row[0]
        else:
            import uuid as _uuid
            from datetime import datetime as _dt, timezone as _tz

            demo_bu_id = str(_uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO business_units (id, name, created_at) "
                    "VALUES (:id, 'North America', :created_at)"
                ),
                {"id": demo_bu_id, "created_at": _dt.now(_tz.utc)},
            )
        for table in ("users", "line_items", "drivers", "driver_inputs", "driver_form_configs"):
            try:
                conn.execute(
                    text(
                        f"UPDATE {table} SET business_unit_id = :bu_id "
                        f"WHERE business_unit_id IS NULL"
                    ),
                    {"bu_id": demo_bu_id},
                )
            except Exception:
                pass


@pytest.fixture(scope="session", autouse=True)
def _patch_persistent_app_db_schema():
    """Keep TestClient's sqlite file compatible with current ORM columns."""
    ensure_app_db_schema_columns()
    yield


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
             can_view_all_bus=True, can_manage_drivers=True),
        dict(name="analyst", description="Analyst", can_input=True, can_generate=True,
             can_override=True, can_review=False, can_publish=False, can_admin=False,
             can_view_all_bus=False, can_manage_drivers=True),
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
def seed_business_unit(db_session):
    """Seed the one company most tests operate as/within (idempotent)."""
    existing = db_session.query(BusinessUnit).filter(BusinessUnit.name == "North America").first()
    if existing:
        return existing
    bu = BusinessUnit(name="North America")
    db_session.add(bu)
    db_session.commit()
    return bu


@pytest.fixture
def seed_users(db_session, seed_roles, seed_business_unit):
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
            business_unit_id=seed_business_unit.id,
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
def seed_line_items(db_session, seed_business_unit):
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
    for li in items:
        li.business_unit_id = seed_business_unit.id
    db_session.add_all(items)
    db_session.commit()
    return {li.account_code: li for li in items}


@pytest.fixture
def seed_actuals(db_session, seed_line_items, seed_business_unit):
    """Seed 24 months of actuals data."""
    import numpy as np

    dataset = ActualsDataset(
        source_type="csv",
        source_name="test_actuals.csv",
        file_hash="test_hash_abc123",
        business_unit_id=seed_business_unit.id,
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

    def __init__(self, permissions: set[str] | None = None):
        self._memory: dict = {}
        # None = allow all (legacy default). Pass an explicit set to enforce RBAC in tests.
        self.permissions = permissions
        self.user = None

    def get_memory(self, key, default=None):
        return self._memory.get(key, default)

    def set_memory(self, key, value):
        self._memory[key] = value

    def get_active_version_id(self):
        return self.get_memory("active_version_id")

    def set_active_version_id(self, version_id):
        self.set_memory("active_version_id", version_id)

    def has_permission(self, permission):
        if self.permissions is None:
            return True
        return permission in self.permissions


@pytest.fixture
def context_manager():
    """Create a test context manager."""
    return MockContextManager()


@pytest.fixture
def skill_context(db_session, seed_users, context_manager):
    """Create a SkillContext for testing skills (analyst — no review permission)."""
    analyst = seed_users["analyst"]
    context_manager.user = analyst
    context_manager.permissions = {"input", "generate", "override"}
    return SkillContext(
        db=db_session,
        context_manager=context_manager,
        user_id=analyst.id,
        user_role="analyst",
        conversation_id="test-conversation-001",
        user=analyst,
    )
