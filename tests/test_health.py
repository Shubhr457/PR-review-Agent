"""
tests/test_health.py

Tests for GET /health (Milestone 1 + production hardening).

_check_github is mocked in every test — no real network calls are made.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

_GITHUB_OK: Dict[str, Any] = {
    "ok": True,
    "detail": "reachable (rate limit remaining: 5000)",
}
_GITHUB_FAIL: Dict[str, Any] = {"ok": False, "detail": "HTTP 401"}

# Convenience alias for the patch target.
_PATCH = "app.routers.health._check_github"


def test_health_returns_200(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(return_value=_GITHUB_OK)):
        response = client.get("/health")
    assert response.status_code == 200


def test_health_response_shape(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(return_value=_GITHUB_OK)):
        data: Dict[str, Any] = client.get("/health").json()
    assert data["status"] in ("ok", "degraded")
    assert "version" in data
    assert "uptime_seconds" in data
    assert "dependencies" in data


def test_health_version_matches_settings(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(return_value=_GITHUB_OK)):
        data: Dict[str, Any] = client.get("/health").json()
    assert data["version"] == "1.0.0-test"


def test_health_dependency_keys(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(return_value=_GITHUB_OK)):
        data: Dict[str, Any] = client.get("/health").json()
    assert set(data["dependencies"].keys()) == {"github_api", "openai_api", "sqs", "deduplication"}


def test_health_all_dependencies_ok(client: TestClient) -> None:
    """All deps should be ok because override_settings provides valid-looking values."""
    with patch(_PATCH, new=AsyncMock(return_value=_GITHUB_OK)):
        data: Dict[str, Any] = client.get("/health").json()
    deps = data["dependencies"]
    assert deps["github_api"]["ok"] is True
    assert deps["openai_api"]["ok"] is True
    assert deps["sqs"]["ok"] is True
    assert deps["deduplication"]["ok"] is True
    assert data["status"] == "ok"


def test_health_status_degraded_when_github_down(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(return_value=_GITHUB_FAIL)):
        data: Dict[str, Any] = client.get("/health").json()
    assert data["status"] == "degraded"
    assert data["dependencies"]["github_api"]["ok"] is False


def test_health_uptime_is_non_negative(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(return_value=_GITHUB_OK)):
        data: Dict[str, Any] = client.get("/health").json()
    assert data["uptime_seconds"] >= 0


def test_health_openai_key_format_check(client: TestClient) -> None:
    """OpenAI dep should fail when key format is wrong (no API call made)."""
    from app.core.config import Settings
    from app.routers.health import _check_openai

    bad_settings = Settings(openai_api_key="invalid-key-format")
    result = _check_openai(bad_settings)
    assert result["ok"] is False
    assert "format" in result["detail"]


def test_liveness_does_not_depend_on_external_services(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_sqs_missing(client: TestClient) -> None:
    """SQS dep should fail when queue URL is empty."""
    from app.core.config import Settings
    from app.routers.health import _check_sqs

    no_sqs = Settings(sqs_queue_url="")
    result = _check_sqs(no_sqs)
    assert result["ok"] is False
