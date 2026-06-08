"""
tests/test_github_service.py

Unit tests for the async GitHub API client.

Uses httpx.MockTransport to intercept outgoing requests — no real
network calls are made.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
import pytest

from app.models.review_schemas import ReviewComment, ReviewResult, Severity
from app.services.github import GitHubService, GitHubServiceError

# ── Fixtures / helpers ───────────────────────────────────────────────────────


def _mock_transport(
    handler,
) -> httpx.MockTransport:
    """Build an httpx MockTransport from an async handler function."""
    return httpx.MockTransport(handler)


def _github_service_with_transport(transport: httpx.MockTransport) -> GitHubService:
    """Create a GitHubService that uses the given mock transport."""
    svc = GitHubService(token="ghp_test", base_url="https://api.github.com")
    # Monkey-patch the _client method to inject our transport.
    original_client = svc._client

    def patched_client():
        client = original_client()
        client._transport = transport
        return client

    svc._client = patched_client
    return svc


# ── Sample data ──────────────────────────────────────────────────────────────


SAMPLE_FILES: List[Dict[str, Any]] = [
    {
        "filename": "app/main.py",
        "status": "modified",
        "changes": 15,
        "patch": "@@ -1,5 +1,8 @@\n+import os\n",
    },
    {
        "filename": "tests/test_main.py",
        "status": "added",
        "changes": 42,
        "patch": "@@ -0,0 +1,42 @@\n+def test_foo():\n",
    },
]

SAMPLE_REVIEW = ReviewResult(
    pr_number=7,
    repo_full_name="acme/widgets",
    comments=[
        ReviewComment(
            file="app/main.py",
            line=3,
            severity=Severity.BUG,
            comment="Potential null dereference.",
        ),
    ],
)


# ── fetch_pr_files ───────────────────────────────────────────────────────────


class TestFetchPrFiles:
    @pytest.mark.asyncio
    async def test_returns_file_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/repos/acme/widgets/pulls/7/files" in str(request.url)
            return httpx.Response(200, json=SAMPLE_FILES)

        svc = _github_service_with_transport(_mock_transport(handler))
        files = await svc.fetch_pr_files("acme", "widgets", 7)
        assert len(files) == 2
        assert files[0]["filename"] == "app/main.py"

    @pytest.mark.asyncio
    async def test_paginates(self) -> None:
        """If the first page is full (per_page items), fetch the next page."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Return a full page (per_page=2).
                return httpx.Response(200, json=SAMPLE_FILES)
            # Second page is empty → stop.
            return httpx.Response(200, json=[])

        svc = _github_service_with_transport(_mock_transport(handler))
        files = await svc.fetch_pr_files("acme", "widgets", 7, per_page=2)
        assert len(files) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_raises_on_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Not Found"})

        svc = _github_service_with_transport(_mock_transport(handler))
        with pytest.raises(GitHubServiceError) as exc_info:
            await svc.fetch_pr_files("acme", "widgets", 999)
        assert exc_info.value.status_code == 404


# ── post_review ──────────────────────────────────────────────────────────────


class TestPostReview:
    @pytest.mark.asyncio
    async def test_posts_request_changes_with_comments(self) -> None:
        captured: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"id": 1})

        svc = _github_service_with_transport(_mock_transport(handler))
        result = await svc.post_review(
            "acme", "widgets", 7, SAMPLE_REVIEW, "abc123"
        )

        assert "/repos/acme/widgets/pulls/7/reviews" in captured["url"]
        body = captured["body"]
        assert body["event"] == "REQUEST_CHANGES"
        assert body["commit_id"] == "abc123"
        assert len(body["comments"]) == 1
        assert body["comments"][0]["path"] == "app/main.py"
        assert body["comments"][0]["line"] == 3
        assert "🔴" in body["comments"][0]["body"]

    @pytest.mark.asyncio
    async def test_posts_approve_when_no_comments(self) -> None:
        empty_review = ReviewResult(
            pr_number=7, repo_full_name="acme/widgets", comments=[]
        )
        captured: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": 2})

        svc = _github_service_with_transport(_mock_transport(handler))
        await svc.post_review("acme", "widgets", 7, empty_review, "abc123")

        assert captured["body"]["event"] == "APPROVE"
        assert captured["body"]["comments"] == []

    @pytest.mark.asyncio
    async def test_raises_on_403(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Forbidden"})

        svc = _github_service_with_transport(_mock_transport(handler))
        with pytest.raises(GitHubServiceError) as exc_info:
            await svc.post_review("acme", "widgets", 7, SAMPLE_REVIEW, "abc123")
        assert exc_info.value.status_code == 403


# ── post_failure_comment ─────────────────────────────────────────────────────


class TestPostFailureComment:
    @pytest.mark.asyncio
    async def test_posts_issue_comment(self) -> None:
        captured: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["url"] = str(request.url)
            return httpx.Response(201, json={"id": 10})

        svc = _github_service_with_transport(_mock_transport(handler))
        result = await svc.post_failure_comment(
            "acme", "widgets", 7, "Something broke"
        )

        assert "/repos/acme/widgets/issues/7/comments" in captured["url"]
        assert "Something broke" in captured["body"]["body"]
        assert "not blocked" in captured["body"]["body"].lower()
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_on_double_failure(self) -> None:
        """If the failure comment itself fails, return None (don't crash)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        svc = _github_service_with_transport(_mock_transport(handler))
        result = await svc.post_failure_comment(
            "acme", "widgets", 7, "original error"
        )
        assert result is None
