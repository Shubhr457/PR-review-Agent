"""
app/routers/reviews.py

Review orchestration router (Milestone 2).

Provides the BackgroundTask entry-point that:
  1. Fetches changed files from GitHub.
  2. Filters, prioritizes, and truncates diffs.
  3. (Milestone 3) Sends each diff to OpenAI for analysis.
  4. Posts the review back to GitHub.
  5. Posts a fail-open comment on any unhandled error (FR-16).
"""

from __future__ import annotations

import logging
from typing import List, Dict

from fastapi import APIRouter

from app.core.config import Settings
from app.models.github_schemas import WebhookPayload
from app.models.review_schemas import ReviewResult
from app.services.diff_processor import prepare_diffs
from app.services.github import GitHubService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reviews"])


async def run_inline_review(
    payload: WebhookPayload,
    settings: Settings,
) -> None:
    """Background task: fetch diffs, review, and post results.

    This is called as a FastAPI ``BackgroundTask`` for PRs with
    ``changed_files <= LARGE_PR_THRESHOLD``.
    """
    owner = payload.repository.full_name.split("/")[0]
    repo = payload.repository.name
    pr_number = payload.number
    commit_sha = payload.pull_request.head.sha

    github = GitHubService(
        token=settings.github_token,
        base_url=settings.github_api_base_url,
    )

    try:
        # ── 1. Fetch changed files ───────────────────────────────────────
        raw_files = await github.fetch_pr_files(owner, repo, pr_number)

        # ── 2. Filter / prioritize / truncate ────────────────────────────
        prepared: List[Dict[str, str]] = prepare_diffs(
            files=raw_files,
            skip_extensions=settings.skip_extensions_list,
            max_files=settings.max_files_per_pr,
            max_diff_chars=settings.max_diff_chars,
        )

        if not prepared:
            logger.info(
                "PR #%d on %s/%s: no reviewable files after filtering.",
                pr_number, owner, repo,
            )
            # Post an APPROVE review with no comments.
            review = ReviewResult(
                pr_number=pr_number,
                repo_full_name=payload.repository.full_name,
                comments=[],
            )
            await github.post_review(owner, repo, pr_number, review, commit_sha)
            return

        logger.info(
            "PR #%d on %s/%s: %d files ready for review.",
            pr_number, owner, repo, len(prepared),
        )

        # ── 3. AI Review (Milestone 3 stub) ──────────────────────────────
        # In Milestone 3 each diff will be sent to OpenAI concurrently.
        # For now we log the prepared diffs and post an empty (APPROVE) review.
        for diff in prepared:
            logger.info(
                "  → %s (%d chars)",
                diff["filename"],
                len(diff["patch"]),
            )

        # Placeholder review — no AI comments yet.
        review = ReviewResult(
            pr_number=pr_number,
            repo_full_name=payload.repository.full_name,
            comments=[],  # Milestone 3 will populate this.
        )

        # ── 4. Post review to GitHub ─────────────────────────────────────
        await github.post_review(owner, repo, pr_number, review, commit_sha)

    except Exception as exc:
        # ── FR-16: fail open ─────────────────────────────────────────────
        logger.exception(
            "Review failed for PR #%d on %s/%s.",
            pr_number, owner, repo,
        )
        await github.post_failure_comment(
            owner, repo, pr_number, str(exc),
        )
