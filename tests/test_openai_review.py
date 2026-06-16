"""
tests/test_openai_review.py

Unit tests for OpenAI review service.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.review_schemas import ReviewComment, Severity
from app.services.openai_review import FileReviewResponse, OpenAIReviewService


@pytest.mark.asyncio
async def test_review_file_diff_empty_patch() -> None:
    service = OpenAIReviewService(api_key="fake-key")
    service.client.beta.chat.completions.parse = AsyncMock()

    comments = await service.review_file_diff("app.py", "")
    assert comments == []
    service.client.beta.chat.completions.parse.assert_not_called()


@pytest.mark.asyncio
async def test_review_file_diff_success() -> None:
    service = OpenAIReviewService(api_key="fake-key")

    mock_parsed = FileReviewResponse(
        comments=[
            ReviewComment(
                file="app.py",
                line=42,
                severity=Severity.BUG,
                comment="Potential null pointer dereference.",
            ),
            ReviewComment(
                file="app.py",
                line=50,
                severity=Severity.SUGGESTION,
                comment="Rename var to camelCase.",
            ),
        ]
    )

    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock(message=MagicMock(parsed=mock_parsed))]

    service.client.beta.chat.completions.parse = AsyncMock(
        return_value=mock_completion
    )

    comments = await service.review_file_diff("app.py", "some diff data")

    assert len(comments) == 2
    assert comments[0].file == "app.py"
    assert comments[0].line == 42
    assert comments[0].severity == Severity.BUG
    assert comments[0].comment == "Potential null pointer dereference."

    assert comments[1].file == "app.py"
    assert comments[1].line == 50
    assert comments[1].severity == Severity.SUGGESTION
    assert comments[1].comment == "Rename var to camelCase."

    service.client.beta.chat.completions.parse.assert_called_once()


@pytest.mark.asyncio
async def test_review_file_diff_api_error() -> None:
    service = OpenAIReviewService(api_key="fake-key")

    service.client.beta.chat.completions.parse = AsyncMock(
        side_effect=RuntimeError("OpenAI API Outage")
    )

    with pytest.raises(RuntimeError) as exc_info:
        await service.review_file_diff("app.py", "some patch")

    assert "OpenAI API Outage" in str(exc_info.value)
