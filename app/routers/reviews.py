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
from app.services.diff_processor import extract_reviewable_new_lines, prepare_diffs
from app.services.github import GitHubService
import asyncio
from app.services.openai_review import OpenAIReviewService
from app.services.token_manager import calculate_total_tokens, filter_by_token_budget

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
        # ── 0. Set commit status to pending ──────────────────────────────
        await github.post_commit_status(
            owner=owner,
            repo=repo,
            sha=commit_sha,
            state="pending",
            description="PR Review Agent is reviewing changes...",
        )

        # ── 1. Fetch changed files ───────────────────────────────────────
        raw_files = await github.fetch_pr_files(owner, repo, pr_number)

        # ── 2. Filter / prioritize / truncate ────────────────────────────
        prepared: List[Dict[str, str]] = prepare_diffs(
            files=raw_files,
            skip_extensions=settings.skip_extensions_list,
            max_files=settings.max_files_per_pr,
            max_diff_chars=settings.max_diff_chars,
        )
        prepared = filter_by_token_budget(prepared, settings.max_total_tokens)

        if not prepared:
            logger.info(
                "PR #%d on %s/%s: no reviewable files after filtering/budgeting.",
                pr_number, owner, repo,
            )
            # Post an APPROVE review with no comments.
            review = ReviewResult(
                pr_number=pr_number,
                repo_full_name=payload.repository.full_name,
                comments=[],
            )
            await github.post_review(owner, repo, pr_number, review, commit_sha)
            await github.post_commit_status(
                owner=owner,
                repo=repo,
                sha=commit_sha,
                state="success",
                description="Skipped: no reviewable files.",
            )
            return

        logger.info(
            "PR #%d on %s/%s: %d files ready for review.",
            pr_number, owner, repo, len(prepared),
        )

        # ── 3. AI Review (Milestone 3 integration) ───────────────────────
        # Estimate total tokens for logging.
        total_tokens = calculate_total_tokens(prepared)
        logger.info(
            "Estimated total tokens for review on PR #%d: %d",
            pr_number,
            total_tokens,
        )

        openai_service = OpenAIReviewService(api_key=settings.openai_api_key)

        async def review_single_file(diff: Dict[str, str]):
            filename = diff["filename"]
            patch = diff["patch"]
            try:
                comments = await openai_service.review_file_diff(
                    filename,
                    patch,
                    model=settings.openai_model,
                )
                valid_lines = extract_reviewable_new_lines(patch)
                valid_comments = [
                    comment for comment in comments if comment.line in valid_lines
                ]
                dropped_count = len(comments) - len(valid_comments)
                if dropped_count:
                    logger.warning(
                        "Dropped %d invalid AI review comment(s) for %s.",
                        dropped_count,
                        filename,
                    )
                return valid_comments
            except Exception as exc:
                # FR-12: Handle OpenAI API errors gracefully per file
                logger.error(
                    "Gracefully handling review failure for file %s: %s",
                    filename,
                    exc,
                )
                return []

        # FR-11: Process all files concurrently
        tasks = [review_single_file(diff) for diff in prepared]
        results = await asyncio.gather(*tasks)

        # Flatten list of lists
        comments = []
        for file_comments in results:
            comments.extend(file_comments)

        review = ReviewResult(
            pr_number=pr_number,
            repo_full_name=payload.repository.full_name,
            comments=comments,
        )

        # ── 4. Post review to GitHub ─────────────────────────────────────
        await github.post_review(owner, repo, pr_number, review, commit_sha)

        # ── 5. Update commit status to success ───────────────────────────
        await github.post_commit_status(
            owner=owner,
            repo=repo,
            sha=commit_sha,
            state="success",
            description="Review completed successfully.",
        )

    except Exception as exc:
        # ── FR-16: fail open ─────────────────────────────────────────────
        logger.exception(
            "Review failed for PR #%d on %s/%s.",
            pr_number, owner, repo,
        )
        try:
            await github.post_commit_status(
                owner=owner,
                repo=repo,
                sha=commit_sha,
                state="success",
                description=f"Fail-open: {str(exc)}",
            )
        except Exception:
            logger.exception("Could not post fail-open commit status.")

        await github.post_failure_comment(
            owner, repo, pr_number, str(exc),
        )
