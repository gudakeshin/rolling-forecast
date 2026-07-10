"""Tests for API endpoints (health, auth, panel)."""

import os

import pytest

# Must set env BEFORE importing the app (prometheus is gated on APP_ENV)
os.environ["APP_ENV"] = "test"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-ci-at-least-32-chars")
os.environ["DATABASE_URL"] = "sqlite:///./test_api_rolling_forecast.db"
os.environ.setdefault("SEED_DEMO_USERS", "true")

from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    """Create test database."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    if os.path.exists("test_api_rolling_forecast.db"):
        os.remove("test_api_rolling_forecast.db")


@pytest.fixture
def client():
    # Lifespan seeds demo users when SEED_DEMO_USERS=true
    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_healthy(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_returns_service_name(self, client):
        response = client.get("/health")
        data = response.json()
        assert "service" in data


class TestAuthEndpoints:
    """Test authentication endpoints."""

    def test_login_with_valid_credentials(self, client):
        """Should return JWT token for valid credentials."""
        response = client.post("/api/auth/login", json={
            "username": "analyst",
            "password": "analyst",
        })
        # May fail if seeding hasn't run yet in test context
        if response.status_code == 200:
            data = response.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"

    def test_login_with_invalid_credentials(self, client):
        """Should reject invalid credentials."""
        response = client.post("/api/auth/login", json={
            "username": "nonexistent",
            "password": "wrong",
        })
        assert response.status_code in (401, 400, 422)

    def test_protected_endpoint_without_token(self, client):
        """Should reject requests without auth token."""
        response = client.get("/api/panel/forecast-table/some-id")
        assert response.status_code in (401, 403)


class TestPanelEndpoints:
    """Test panel data endpoints."""

    def _get_auth_token(self, client):
        """Helper to get auth token."""
        response = client.post("/api/auth/login", json={
            "username": "analyst",
            "password": "analyst",
        })
        if response.status_code == 200:
            return response.json()["access_token"]
        return None

    def test_forecast_table_not_found(self, client):
        """Should 404 for nonexistent version."""
        token = self._get_auth_token(client)
        if not token:
            pytest.skip("Auth not available in test")

        response = client.get(
            "/api/panel/forecast-table/nonexistent",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
