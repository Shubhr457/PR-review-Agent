"""
app/routers/reviews.py

Review orchestration router (Milestone 2 / 3 placeholder).

This module will expose the internal trigger endpoint used by the SQS
consumer Lambda and will host BackgroundTask helpers for inline reviews.

All logic here is stubbed for Milestone 1.
"""

from fastapi import APIRouter

router = APIRouter(tags=["reviews"])

# ── Milestone 2+ ─────────────────────────────────────────────────────────────
# async def run_inline_review(payload: WebhookPayload, settings: Settings) -> None:
#     """Triggered as a BackgroundTask for PRs with <= LARGE_PR_THRESHOLD files."""
#     ...
#
# async def run_sqs_review(job: dict, settings: Settings) -> None:
#     """Triggered by the SQS consumer Lambda for large PRs."""
#     ...
