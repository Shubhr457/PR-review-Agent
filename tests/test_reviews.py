"""
tests/test_reviews.py

Integration tests for the review orchestrator (app/routers/reviews.py).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.github_schemas import WebhookPayload
from app.models.review_schemas import ReviewComment, Severity
from app.routers.reviews import run_inline_review
from tests.conftest import minimal_pr_payload


@pytest.mark.asyncio
@patch("app.routers.reviews.GitHubService")
@patch("app.routers.reviews.OpenAIReviewService")
async def test_run_inline_review_pipeline_success(
    mock_openai_service_class, mock_github_service_class
) -> None:
    # 1. Setup settings & payload
    settings = Settings(
        github_token="fake-token",
        openai_api_key="fake-key",
        openai_model="gpt-4o",
        max_files_per_pr=2,
        max_diff_chars=1000,
    )
    raw_payload = minimal_pr_payload(changed_files=2)
    payload = WebhookPayload.model_validate(raw_payload)

    # 2. Setup mock GitHub service
    mock_github = MagicMock()
    mock_github_service_class.return_value = mock_github
    mock_github.fetch_pr_files = AsyncMock(
        return_value=[
            {"filename": "a.py", "changes": 5, "patch": "diff-a"},
            {"filename": "b.py", "changes": 10, "patch": "diff-b"},
        ]
    )
    mock_github.post_review = AsyncMock(return_value={"id": 123})
    mock_github.post_commit_status = AsyncMock()

    # 3. Setup mock OpenAI service
    mock_openai = MagicMock()
    mock_openai_service_class.return_value = mock_openai

    comments_a = [
        ReviewComment(
            file="a.py",
            line=5,
            severity=Severity.BUG,
            comment="Bug in a.py",
        )
    ]
    comments_b = [
        ReviewComment(
            file="b.py",
            line=12,
            severity=Severity.SUGGESTION,
            comment="Suggestion in b.py",
        )
    ]

    # Mock review_file_diff to return comments depending on the filename
    async def side_effect(filename, patch, model):
        if filename == "a.py":
            return comments_a
        elif filename == "b.py":
            return comments_b
        return []

    mock_openai.review_file_diff = AsyncMock(side_effect=side_effect)

    # 4. Run the orchestrator
    await run_inline_review(payload, settings)

    # 5. Assertions
    mock_github.fetch_pr_files.assert_called_once_with("octocat", "Hello-World", 42)

    # Verify OpenAI was called for each file
    assert mock_openai.review_file_diff.call_count == 2
    mock_openai.review_file_diff.assert_any_call("a.py", "diff-a", model="gpt-4o")
    mock_openai.review_file_diff.assert_any_call("b.py", "diff-b", model="gpt-4o")

    # Verify GitHub post_review was called with aggregated comments
    mock_github.post_review.assert_called_once()
    args, kwargs = mock_github.post_review.call_args
    review_result = args[3]

    assert review_result.pr_number == 42
    assert len(review_result.comments) == 2
    assert review_result.comments[0].file == "b.py"
    assert review_result.comments[0].comment == "Suggestion in b.py"
    assert review_result.comments[1].file == "a.py"
    assert review_result.comments[1].comment == "Bug in a.py"


@pytest.mark.asyncio
@patch("app.routers.reviews.GitHubService")
@patch("app.routers.reviews.OpenAIReviewService")
async def test_run_inline_review_graceful_openai_file_failure(
    mock_openai_service_class, mock_github_service_class
) -> None:
    # Verify FR-12: if one file fails to review, we continue with the rest
    settings = Settings(
        github_token="fake-token",
        openai_api_key="fake-key",
        max_files_per_pr=5,
    )
    raw_payload = minimal_pr_payload(changed_files=2)
    payload = WebhookPayload.model_validate(raw_payload)

    # Mock GitHub
    mock_github = MagicMock()
    mock_github_service_class.return_value = mock_github
    mock_github.fetch_pr_files = AsyncMock(
        return_value=[
            {"filename": "a.py", "changes": 5, "patch": "diff-a"},
            {"filename": "b.py", "changes": 10, "patch": "diff-b"},
        ]
    )
    mock_github.post_review = AsyncMock()
    mock_github.post_commit_status = AsyncMock()

    # Mock OpenAI: a.py fails, b.py succeeds
    mock_openai = MagicMock()
    mock_openai_service_class.return_value = mock_openai

    comments_b = [
        ReviewComment(
            file="b.py",
            line=12,
            severity=Severity.WARNING,
            comment="Warning in b.py",
        )
    ]

    async def side_effect(filename, patch, model):
        if filename == "a.py":
            raise RuntimeError("API Rate Limit Exceeded")
        return comments_b

    mock_openai.review_file_diff = AsyncMock(side_effect=side_effect)

    # Execute
    await run_inline_review(payload, settings)

    # Post review should still be called with b.py comments only
    mock_github.post_review.assert_called_once()
    args, kwargs = mock_github.post_review.call_args
    review_result = args[3]

    assert len(review_result.comments) == 1
    assert review_result.comments[0].file == "b.py"
    assert review_result.comments[0].comment == "Warning in b.py"
