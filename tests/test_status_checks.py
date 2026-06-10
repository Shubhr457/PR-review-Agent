"""
tests/test_status_checks.py

Unit tests for GitHub Commit Status check integration (Milestone 6).
Covers:
  - post_commit_status endpoint and payload structure
  - Status check transitions (pending -> success) under normal flow
  - Status check transitions (pending -> success) under error/fail-open flow
"""

from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from app.core.config import Settings
from app.models.github_schemas import WebhookPayload
from app.routers.reviews import run_inline_review
from app.services.github import GitHubService
from tests.conftest import minimal_pr_payload

# ── Service-level unit tests ──────────────────────────────────────────────────

def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)

def _github_service_with_transport(transport: httpx.MockTransport) -> GitHubService:
    svc = GitHubService(token="ghp_test", base_url="https://api.github.com")
    original_client = svc._client

    def patched_client():
        client = original_client()
        client._transport = transport
        return client

    svc._client = patched_client
    return svc

@pytest.mark.asyncio
async def test_post_commit_status_payload() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        assert request.method == "POST"
        assert "/repos/acme/widgets/statuses/1a2b3c4d5e" in str(request.url)
        
        # Verify request body
        import json
        body = json.loads(request.read().decode())
        assert body["state"] == "pending"
        assert body["context"] == "PR Review Agent"
        assert body["description"] == "Reviewing changes..."
        assert body["target_url"] == "https://example.com"
        
        return httpx.Response(201, json={"id": 12345})

    svc = _github_service_with_transport(_mock_transport(handler))
    response = await svc.post_commit_status(
        owner="acme",
        repo="widgets",
        sha="1a2b3c4d5e",
        state="pending",
        description="Reviewing changes...",
        target_url="https://example.com"
    )
    assert called
    assert response["id"] == 12345

# ── Router-level orchestration tests ──────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.routers.reviews.GitHubService")
@patch("app.routers.reviews.OpenAIReviewService")
async def test_run_inline_review_status_check_transitions_success(
    mock_openai_service_class, mock_github_service_class
) -> None:
    settings = Settings(
        github_token="fake-token",
        openai_api_key="fake-key",
        openai_model="gpt-4o",
        max_files_per_pr=2,
    )
    payload = WebhookPayload.model_validate(minimal_pr_payload(changed_files=1))

    # Mock GitHub
    mock_github = MagicMock()
    mock_github_service_class.return_value = mock_github
    mock_github.fetch_pr_files = AsyncMock(
        return_value=[{"filename": "a.py", "changes": 5, "patch": "diff-a"}]
    )
    mock_github.post_review = AsyncMock(return_value={"id": 123})
    mock_github.post_commit_status = AsyncMock()

    # Mock OpenAI
    mock_openai = MagicMock()
    mock_openai_service_class.return_value = mock_openai
    mock_openai.review_file_diff = AsyncMock(return_value=[])

    # Run
    await run_inline_review(payload, settings)

    # Assert status checks were updated
    assert mock_github.post_commit_status.call_count == 2
    
    # First call: pending
    first_call_args = mock_github.post_commit_status.call_args_list[0][1]
    assert first_call_args["state"] == "pending"
    assert "reviewing" in first_call_args["description"].lower()

    # Second call: success
    second_call_args = mock_github.post_commit_status.call_args_list[1][1]
    assert second_call_args["state"] == "success"
    assert "completed" in second_call_args["description"].lower()


@pytest.mark.asyncio
@patch("app.routers.reviews.GitHubService")
@patch("app.routers.reviews.OpenAIReviewService")
async def test_run_inline_review_status_check_transitions_fail_open(
    mock_openai_service_class, mock_github_service_class
) -> None:
    settings = Settings(
        github_token="fake-token",
        openai_api_key="fake-key",
    )
    payload = WebhookPayload.model_validate(minimal_pr_payload(changed_files=1))

    # Mock GitHub: throw exception during fetch_pr_files
    mock_github = MagicMock()
    mock_github_service_class.return_value = mock_github
    mock_github.fetch_pr_files = AsyncMock(side_effect=RuntimeError("GitHub down!"))
    mock_github.post_commit_status = AsyncMock()
    mock_github.post_failure_comment = AsyncMock()

    # Run
    await run_inline_review(payload, settings)

    # Assert status checks were updated
    assert mock_github.post_commit_status.call_count == 2
    
    # First call: pending
    first_call_args = mock_github.post_commit_status.call_args_list[0][1]
    assert first_call_args["state"] == "pending"

    # Second call: success (fail-open)
    second_call_args = mock_github.post_commit_status.call_args_list[1][1]
    assert second_call_args["state"] == "success"
    assert "fail-open" in second_call_args["description"].lower()
    
    # Assert failure comment was posted
    mock_github.post_failure_comment.assert_called_once()
