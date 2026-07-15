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

import hashlib
import json
import logging
from typing import Any, Dict, FrozenSet

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.security import verify_webhook_signature
from app.models.github_schemas import WebhookPayload
from app.services.idempotency import (
    IdempotencyConfigurationError,
    claim_delivery,
    release_delivery,
)
from app.services.sqs import SQSConfigurationError, enqueue_payload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])

# Actions that trigger a code review (FR-03).
HANDLED_ACTIONS: FrozenSet[str] = frozenset({"opened", "synchronize", "reopened"})


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def receive_webhook(
    request: Request,
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
        logger.warning("Webhook payload parsing failed.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # ── 4. Filter to handled PR actions ─────────────────────────────────────
    if payload.action not in HANDLED_ACTIONS:
        logger.debug("Ignoring unsupported webhook action.")
        return {
            "message": f"Action '{payload.action}' ignored.",
            "pr_number": payload.number,
        }

    # Queue every review. Lambda BackgroundTasks still run within the invocation,
    # so they cannot satisfy the GitHub acknowledgement deadline reliably.
    delivery_id = request.headers.get("X-GitHub-Delivery") or hashlib.sha256(body).hexdigest()
    revision_id = (
        f"revision:{payload.repository.id}:{payload.number}:{payload.pull_request.head.sha}"
    )
    claimed_keys = []
    try:
        for key in (delivery_id, revision_id):
            if not await claim_delivery(settings.webhook_deduplication_table, key):
                logger.info("Duplicate webhook delivery ignored.")
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content={"message": "Webhook already accepted."},
                )
            claimed_keys.append(key)
    except IdempotencyConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service unavailable.") from exc
    except Exception as exc:
        for key in claimed_keys:
            await release_delivery(settings.webhook_deduplication_table, key)
        logger.error("Webhook deduplication check failed.")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service unavailable.") from exc

    try:
        await enqueue_payload(payload, settings)
    except Exception as exc:
        for key in claimed_keys:
            await release_delivery(settings.webhook_deduplication_table, key)
        logger.error("Webhook queue submission failed.")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service unavailable.") from exc

    logger.info("Webhook queued for asynchronous review.")
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"message": "Webhook accepted for review."})
