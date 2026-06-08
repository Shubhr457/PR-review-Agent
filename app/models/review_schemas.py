"""
app/models/review_schemas.py

Pydantic models for AI-generated review comments and review results.

The OpenAI service (Milestone 3) will return structured JSON matching
ReviewComment.  ReviewResult aggregates all file-level comments for a PR
before they are posted back to GitHub.
"""

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class Severity(str, Enum):
    BUG = "bug"
    WARNING = "warning"
    SUGGESTION = "suggestion"

    @property
    def emoji(self) -> str:
        return {
            Severity.BUG: "🔴",
            Severity.WARNING: "🟡",
            Severity.SUGGESTION: "🔵",
        }[self]


class ReviewComment(BaseModel):
    """A single inline comment produced by the AI for one line of a diff."""

    file: str = Field(..., description="Relative file path within the repository.")
    line: int = Field(..., ge=1, description="Diff line number the comment targets.")
    severity: Severity
    comment: str = Field(..., min_length=1)

    @property
    def formatted_body(self) -> str:
        """Return a GitHub-ready comment body with severity label."""
        return f"{self.severity.emoji} **{self.severity.value.capitalize()}**\n\n{self.comment}"


class ReviewResult(BaseModel):
    """Aggregate review produced for a single PR."""

    pr_number: int
    repo_full_name: str
    comments: List[ReviewComment] = Field(default_factory=list)

    @property
    def approved(self) -> bool:
        """Approve the PR only when the AI found zero issues."""
        return len(self.comments) == 0

    @property
    def github_event(self) -> str:
        """Maps to GitHub Review event string."""
        return "APPROVE" if self.approved else "REQUEST_CHANGES"
