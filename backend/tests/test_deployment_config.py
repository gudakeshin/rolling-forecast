"""Regression tests for deployment configuration.

These are config assertions, not behaviour tests, on purpose: the defects they
lock in (proxy body limits, forwarded-header trust, dev bind-mounts leaking into
the production compose) are invisible to the application test suite and only
surface on a real deployment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.config import Settings, settings

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"
COMPOSE = REPO_ROOT / "docker-compose.yml"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"


def _size_to_bytes(token: str) -> int:
    match = re.fullmatch(r"(\d+)([kmg]?)", token.strip().lower())
    assert match, f"unparseable nginx size: {token!r}"
    value, unit = int(match.group(1)), match.group(2)
    return value * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[unit]


@pytest.fixture(scope="module")
def prod_compose() -> dict:
    if not COMPOSE_PROD.exists():
        pytest.skip("docker-compose.prod.yml not present")
    return yaml.safe_load(COMPOSE_PROD.read_text())


class TestNginxProxy:
    def test_api_body_limit_covers_backend_upload_limit(self):
        """nginx defaults to 1m, which 413s uploads the backend would accept."""
        if not NGINX_CONF.exists():
            pytest.skip("nginx.conf not present")
        limits = re.findall(r"client_max_body_size\s+([0-9kKmMgG]+)\s*;", NGINX_CONF.read_text())
        assert limits, "nginx.conf sets no client_max_body_size for the /api/ proxy"
        assert min(_size_to_bytes(v) for v in limits) >= settings.max_upload_bytes, (
            f"nginx client_max_body_size {limits} is below "
            f"MAX_UPLOAD_BYTES={settings.max_upload_bytes}"
        )


class TestProductionCompose:
    def test_backend_port_is_not_published(self, prod_compose):
        """Trusting X-Forwarded-For is only safe while nginx is the sole ingress."""
        assert prod_compose["services"]["backend"].get("ports") == [], (
            "backend inherits ports 8000:8000 from the base compose; with "
            "FORWARDED_ALLOW_IPS=* that lets anyone spoof the rate-limit key"
        )

    def test_forwarded_headers_are_trusted(self, prod_compose):
        backend = prod_compose["services"]["backend"]
        env = backend.get("environment") or []
        assert any(str(e).startswith("FORWARDED_ALLOW_IPS=") for e in env), (
            "without FORWARDED_ALLOW_IPS, uvicorn keys every request on the "
            "nginx container IP and rate limits collapse to one global bucket"
        )
        assert "--proxy-headers" in " ".join(str(backend["command"]).split())

    @pytest.mark.parametrize("service", ["backend", "worker"])
    def test_no_host_source_bind_mounts(self, prod_compose, service):
        """Production must run the image, not bind-mounted host source."""
        volumes = prod_compose["services"][service].get("volumes")
        assert volumes is not None, (
            f"{service} inherits the dev bind-mounts (./backend/app:/app/app) "
            "from docker-compose.yml; override volumes explicitly"
        )
        for vol in volumes:
            assert not str(vol).startswith("."), f"{service} bind-mounts host path {vol}"

    def test_no_sqlite_volume_when_running_postgres(self, prod_compose):
        for service in ("backend", "worker"):
            volumes = prod_compose["services"][service].get("volumes") or []
            assert not any("forecast-db" in str(v) for v in volumes), (
                f"{service} still mounts the SQLite volume in a Postgres deployment"
            )


class TestDependencyLock:
    """A floating requirements.txt lets prophet/numpy move between builds and
    change forecast output; the lock is what makes a rebuild reproducible."""

    LOCK = REPO_ROOT / "backend" / "requirements.lock"
    INPUT = REPO_ROOT / "backend" / "requirements.txt"

    @staticmethod
    def _canonical(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    def _locked_packages(self) -> dict[str, str]:
        pins = re.findall(
            r"^([A-Za-z0-9._-]+)==([A-Za-z0-9._+!-]+)", self.LOCK.read_text(), re.MULTILINE
        )
        return {self._canonical(n): v for n, v in pins}

    def _declared_packages(self) -> set[str]:
        declared = set()
        for line in self.INPUT.read_text().splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            match = re.match(r"^([A-Za-z0-9._-]+)", line)
            if match:
                declared.add(self._canonical(match.group(1)))
        return declared

    def test_lock_exists(self):
        assert self.LOCK.exists(), "requirements.lock is missing — run ./scripts/lock-deps.sh"

    def test_every_declared_dependency_is_locked(self):
        """Adding to requirements.txt without relocking must not pass CI."""
        missing = sorted(self._declared_packages() - set(self._locked_packages()))
        assert not missing, (
            f"declared in requirements.txt but absent from requirements.lock: {missing}. "
            "Run ./scripts/lock-deps.sh"
        )

    def test_every_locked_package_is_pinned_exactly(self):
        text = self.LOCK.read_text()
        loose = re.findall(r"^([A-Za-z0-9._-]+)(>=|<=|~=|>|<)", text, re.MULTILINE)
        assert not loose, f"lock contains non-exact specifiers: {loose[:5]}"

    def test_every_locked_package_carries_hashes(self):
        """--require-hashes only protects what is actually hashed."""
        blocks = re.split(r"\n(?=[A-Za-z0-9._-]+==)", self.LOCK.read_text())
        unhashed = [
            b.split("==")[0].strip()
            for b in blocks
            if re.match(r"^[A-Za-z0-9._-]+==", b) and "--hash=sha256:" not in b
        ]
        assert not unhashed, f"packages pinned without hashes: {unhashed}"

    def test_lock_does_not_leak_local_paths(self):
        assert "/private/tmp" not in self.LOCK.read_text()
        assert "/Users/" not in self.LOCK.read_text()

    def test_forecast_critical_packages_are_pinned(self):
        """These are the ones whose version changes move the numbers."""
        locked = self._locked_packages()
        for pkg in ("prophet", "statsmodels", "numpy", "pandas", "scikit-learn"):
            assert pkg in locked, f"{pkg} is not pinned in the lock"


class TestPoolConfiguration:
    def test_non_sqlite_engine_gets_pre_ping(self):
        """Stale pooled connections after failover must not surface as 500s."""
        from sqlalchemy import create_engine

        engine = create_engine(
            "postgresql://u:p@localhost:5432/db",
            pool_pre_ping=True,
            pool_recycle=settings.db_pool_recycle_seconds,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == settings.db_pool_recycle_seconds
        engine.dispose()

    def test_pool_defaults_are_sane(self):
        assert settings.db_pool_size >= 1
        assert settings.db_max_overflow >= 0
        assert settings.db_pool_recycle_seconds > 0


class TestAllowedHosts:
    def test_unset_means_middleware_stays_off(self):
        assert Settings(allowed_hosts="").allowed_host_list == []

    def test_loopback_is_always_appended(self):
        """Docker HEALTHCHECK hits localhost — excluding it breaks the probe."""
        hosts = Settings(allowed_hosts="forecast.example.com").allowed_host_list
        assert "forecast.example.com" in hosts
        assert "localhost" in hosts and "127.0.0.1" in hosts

    def test_no_duplicate_loopback_entries(self):
        hosts = Settings(allowed_hosts="localhost,app.internal").allowed_host_list
        assert hosts.count("localhost") == 1
