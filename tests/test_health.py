"""
tests/test_health.py

Tests for GET /health (Milestone 1).
"""

from typing import Any, Dict

from starlette.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_shape(client: TestClient) -> None:
    data: Dict[str, Any] = client.get("/health").json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "uptime_seconds" in data
    assert "dependencies" in data


def test_health_version_matches_settings(client: TestClient) -> None:
    data: Dict[str, Any] = client.get("/health").json()
    assert data["version"] == "1.0.0-test"


def test_health_dependency_keys(client: TestClient) -> None:
    data: Dict[str, Any] = client.get("/health").json()
    deps: Dict[str, str] = data["dependencies"]
    assert set(deps.keys()) == {"github_api", "openai_api", "sqs"}


def test_health_dependencies_configured(client: TestClient) -> None:
    """All deps should be 'configured' because override_settings provides values."""
    data: Dict[str, Any] = client.get("/health").json()
    deps: Dict[str, str] = data["dependencies"]
    assert deps["github_api"] == "configured"
    assert deps["openai_api"] == "configured"
    assert deps["sqs"] == "configured"


def test_health_uptime_is_non_negative(client: TestClient) -> None:
    data: Dict[str, Any] = client.get("/health").json()
    assert data["uptime_seconds"] >= 0
