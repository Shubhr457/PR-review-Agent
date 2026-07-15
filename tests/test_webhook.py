"""
tests/test_webhook.py

Tests for POST /webhook (Milestone 1).

Covers:
  - Signature validation (FR-01)
  - Response time contract (validated structurally, not by wall clock)
  - PR action filtering (FR-03)
  - Payload schema validation (FR-04)
  - Happy-path for all three handled actions
"""

import json
from typing import Any, Dict

import pytest
from unittest.mock import AsyncMock, patch
from starlette.testclient import TestClient

from tests.conftest import minimal_pr_payload, webhook_headers

# ── Signature validation (FR-01) ─────────────────────────────────────────────


def test_missing_signature_returns_401(client: TestClient) -> None:
    payload = json.dumps(minimal_pr_payload()).encode()
    response = client.post(
        "/webhook",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    data: Dict[str, Any] = response.json()
    assert "Missing" in data["detail"]


def test_wrong_signature_returns_401(client: TestClient) -> None:
    payload = json.dumps(minimal_pr_payload()).encode()
    headers = webhook_headers(payload, secret="wrong-secret")
    response = client.post("/webhook", content=payload, headers=headers)
    assert response.status_code == 401
    data: Dict[str, Any] = response.json()
    assert "Invalid" in data["detail"]


def test_tampered_body_returns_401(client: TestClient) -> None:
    """Signature is computed over original body but we send a different body."""
    original = json.dumps(minimal_pr_payload()).encode()
    headers = webhook_headers(original)
    tampered = original + b" "  # body changed after signing
    response = client.post("/webhook", content=tampered, headers=headers)
    assert response.status_code == 401


# ── Action filtering (FR-03) ─────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
def test_handled_actions_return_202(client: TestClient, action: str) -> None:
    payload = json.dumps(minimal_pr_payload(action=action)).encode()
    headers = webhook_headers(payload)
    response = client.post("/webhook", content=payload, headers=headers)
    assert response.status_code == 202
    data: Dict[str, Any] = response.json()
    assert "accepted" in data["message"].lower()


# ── Dispatch routing (FR-08 / Milestone 2) ───────────────────────────────────


def test_large_pr_returns_202(client: TestClient) -> None:
    """PRs exceeding LARGE_PR_THRESHOLD should return 202 (SQS stub)."""
    # Default threshold in test settings is 15 (from Settings defaults).
    payload = json.dumps(minimal_pr_payload(changed_files=20)).encode()
    headers = webhook_headers(payload)
    response = client.post("/webhook", content=payload, headers=headers)
    assert response.status_code == 202
    data: Dict[str, Any] = response.json()
    assert "accepted" in data["message"].lower()


def test_small_pr_returns_202_with_dispatch(client: TestClient) -> None:
    """Every PR is queued so GitHub receives an immediate acknowledgement."""
    payload = json.dumps(minimal_pr_payload(changed_files=5)).encode()
    headers = webhook_headers(payload)
    response = client.post("/webhook", content=payload, headers=headers)
    assert response.status_code == 202
    data: Dict[str, Any] = response.json()
    assert "accepted" in data["message"].lower()


def test_duplicate_delivery_is_not_queued_twice(client: TestClient) -> None:
    payload = json.dumps(minimal_pr_payload()).encode()
    headers = {**webhook_headers(payload), "X-GitHub-Delivery": "delivery-1"}
    with (
        patch("app.routers.webhook.claim_delivery", AsyncMock(return_value=False)),
        patch("app.routers.webhook.enqueue_payload", AsyncMock()) as enqueue,
    ):
        response = client.post("/webhook", content=payload, headers=headers)

    assert response.status_code == 202
    assert "already" in response.json()["message"].lower()
    enqueue.assert_not_awaited()


def test_same_pr_revision_is_not_queued_twice(client: TestClient) -> None:
    payload = json.dumps(minimal_pr_payload()).encode()
    headers = {**webhook_headers(payload), "X-GitHub-Delivery": "delivery-3"}
    with (
        patch("app.routers.webhook.claim_delivery", AsyncMock(side_effect=[True, False])),
        patch("app.routers.webhook.enqueue_payload", AsyncMock()) as enqueue,
    ):
        response = client.post("/webhook", content=payload, headers=headers)

    assert response.status_code == 202
    enqueue.assert_not_awaited()


def test_queue_failure_releases_delivery_for_github_retry(client: TestClient) -> None:
    payload = json.dumps(minimal_pr_payload()).encode()
    headers = {**webhook_headers(payload), "X-GitHub-Delivery": "delivery-2"}
    with (
        patch("app.routers.webhook.enqueue_payload", AsyncMock(side_effect=RuntimeError("queue unavailable"))),
        patch("app.routers.webhook.release_delivery", AsyncMock()) as release,
    ):
        response = client.post("/webhook", content=payload, headers=headers)

    assert response.status_code == 503
    assert release.await_count == 2


@pytest.mark.parametrize(
    "action", ["closed", "labeled", "assigned", "review_requested"]
)
def test_unhandled_actions_are_ignored(client: TestClient, action: str) -> None:
    payload = json.dumps(minimal_pr_payload(action=action)).encode()
    headers = webhook_headers(payload)
    response = client.post("/webhook", content=payload, headers=headers)
    assert response.status_code == 200
    data: Dict[str, Any] = response.json()
    assert "ignored" in data["message"].lower()
    assert data["pr_number"] == 42


# ── Payload validation (FR-04) ───────────────────────────────────────────────


def test_invalid_json_returns_422(client: TestClient) -> None:
    body = b"this is not json"
    headers = webhook_headers(body)
    response = client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 422


def test_missing_required_field_returns_422(client: TestClient) -> None:
    """Omit 'repository' which is required by WebhookPayload."""
    bad_payload = minimal_pr_payload()
    del bad_payload["repository"]
    body = json.dumps(bad_payload).encode()
    headers = webhook_headers(body)
    response = client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 422


def test_extra_unknown_fields_are_ignored(client: TestClient) -> None:
    """Extra fields GitHub may add should not break parsing (extra='ignore')."""
    payload = minimal_pr_payload()
    payload["totally_new_github_field"] = {"some": "data"}
    payload["pull_request"]["some_future_field"] = True
    body = json.dumps(payload).encode()
    headers = webhook_headers(body)
    response = client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 202


# ── Response shape ────────────────────────────────────────────────────────────


def test_webhook_response_contains_expected_keys(client: TestClient) -> None:
    payload = json.dumps(minimal_pr_payload()).encode()
    headers = webhook_headers(payload)
    data: Dict[str, Any] = client.post(
        "/webhook", content=payload, headers=headers
    ).json()
    assert "message" in data


def test_webhook_does_not_echo_pr_metadata(client: TestClient) -> None:
    payload = json.dumps(minimal_pr_payload(changed_files=7)).encode()
    headers = webhook_headers(payload)
    data: Dict[str, Any] = client.post(
        "/webhook", content=payload, headers=headers
    ).json()
    assert set(data) == {"message"}


# ── Edge cases ────────────────────────────────────────────────────────────────


def test_empty_body_returns_401_or_422(client: TestClient) -> None:
    """Empty body has no valid signature and no valid JSON."""
    response = client.post(
        "/webhook",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    # Missing signature header → 401 before we even try to parse JSON.
    assert response.status_code in (401, 422)


def test_pr_with_null_body_field(client: TestClient) -> None:
    """PR body (description) is optional and can be null."""
    payload = minimal_pr_payload()
    payload["pull_request"]["body"] = None
    body = json.dumps(payload).encode()
    headers = webhook_headers(body)
    response = client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 202
