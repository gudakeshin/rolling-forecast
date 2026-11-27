"""One shared Redis client; no silent in-memory fallback outside development."""

from __future__ import annotations

import pytest

from app.config import settings
from app.services import redis_client


@pytest.fixture(autouse=True)
def _reset():
    redis_client.reset_redis_client()
    yield
    redis_client.reset_redis_client()


def test_dev_without_redis_uses_the_fallback(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "require_async_jobs", False)
    assert redis_client.get_redis() is None


def test_production_without_redis_refuses_the_fallback(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(redis_client.RedisUnavailable):
        redis_client.get_redis("token denylist")


def test_production_with_unreachable_redis_raises(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(redis_client.RedisUnavailable):
        redis_client.get_redis("job queue")


def test_the_client_is_shared_across_calls(monkeypatch):
    class _Fake:
        def ping(self):
            return True

    created: list[_Fake] = []

    def _from_url(url, **kwargs):
        created.append(_Fake())
        return created[-1]

    import redis

    monkeypatch.setattr(settings, "redis_url", "redis://example:6379/0")
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(_from_url))
    first = redis_client.get_redis()
    second = redis_client.get_redis()
    assert first is second and len(created) == 1
