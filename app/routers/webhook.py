"""
app/routers/webhook.py

POST /webhook — GitHub pull_request event receiver.

Responsibilities:
  1. Validate the X-Hub-Signature-256 HMAC signature.
  2. Parse the payload into a Pydantic WebhookPayload model.
  3. Filter to only handled PR actions (opened / synchronize / reopened).
  4. Acknowledge GitHub within 10 seconds (FR-02).
  5. Dispatch review as BackgroundTask or SQS job (FR-08).
"""

import json
import logging
from typing import Any, Dict, FrozenSet

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.security import verify_webhook_signature
from app.models.github_schemas import WebhookPayload
from app.routers.reviews import run_inline_review

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])

# Actions that trigger a code review (FR-03).
HANDLED_ACTIONS: FrozenSet[str] = frozenset({"opened", "synchronize", "reopened"})


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def receive_webhook(
    request: Request,
    _background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """
    Receive and validate a GitHub pull_request webhook.

    - Returns 200 quickly so GitHub does not retry (FR-02).
    - Returns 401 on bad/missing signature (FR-01).
    - Returns 422 on payload schema mismatch (FR-04).
    - Ignores non-PR or non-handled actions silently (FR-03).
    """
    # ── 1. Read raw body (must happen before any JSON parsing) ───────────────
    body: bytes = await request.body()

    # ── 2. Verify HMAC-SHA256 signature ─────────────────────────────────────
    signature_header = request.headers.get("X-Hub-Signature-256")
    verify_webhook_signature(body, settings.github_webhook_secret, signature_header)

    # ── 3. Parse JSON into Pydantic model ────────────────────────────────────
    try:
        raw: Dict[str, Any] = json.loads(body)
        payload = WebhookPayload.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Webhook payload parsing failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # ── 4. Filter to handled PR actions ─────────────────────────────────────
    if payload.action not in HANDLED_ACTIONS:
        logger.debug(
            "Ignoring PR #%s action '%s' on %s",
            payload.number,
            payload.action,
            payload.repository.full_name,
        )
        return {
            "message": f"Action '{payload.action}' ignored.",
            "pr_number": payload.number,
        }

    logger.info(
        "PR #%s '%s' on %s — action: %s | changed files: %s",
        payload.number,
        payload.pull_request.title,
        payload.repository.full_name,
        payload.action,
        payload.pull_request.changed_files,
    )

    # ── 5. Dispatch review (FR-08) ────────────────────────────────────────────
    if payload.pull_request.changed_files > settings.large_pr_threshold:
        # Large PR → SQS queue (Milestone 4 will implement sqs.enqueue).
        logger.info(
            "PR #%s has %d files (> %d) — queuing to SQS (stub).",
            payload.number,
            payload.pull_request.changed_files,
            settings.large_pr_threshold,
        )
        # TODO (Milestone 4): await sqs.enqueue(payload, settings)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "message": "Large PR queued for async review.",
                "action": payload.action,
                "pr_number": payload.number,
                "repo": payload.repository.full_name,
                "changed_files": payload.pull_request.changed_files,
            },
        )

    # Small / normal PR → inline BackgroundTask.
    _background_tasks.add_task(run_inline_review, payload, settings)

    return {
        "message": "Webhook received — review dispatched.",
        "action": payload.action,
        "pr_number": payload.number,
        "repo": payload.repository.full_name,
        "changed_files": payload.pull_request.changed_files,
    }
