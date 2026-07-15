"""
app/services/sqs.py

AWS SQS integration for asynchronous review processing of large PRs.
Implements the SQS message producer (enqueue) and consumer (Lambda entrypoint).
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict

import boto3

from app.core.config import Settings, get_settings, validate_required_settings
from app.core.secrets import load_secrets_into_env
from app.models.github_schemas import WebhookPayload
from app.routers.reviews import run_inline_review

logger = logging.getLogger(__name__)


class SQSConfigurationError(RuntimeError):
    """Raised when SQS routing is requested but not configured."""


def _send_sqs_message(queue_url: str, body_str: str, region_name: str) -> None:
    """Synchronous helper executed inside a thread pool to avoid blocking ASGI."""
    sqs = boto3.client("sqs", region_name=region_name)
    sqs.send_message(QueueUrl=queue_url, MessageBody=body_str)


async def enqueue_payload(payload: WebhookPayload, settings: Settings) -> None:
    """Serialize WebhookPayload to JSON and send it to SQS.

    Runs boto3 operations in an execution thread.
    """
    if not settings.sqs_queue_url:
        raise SQSConfigurationError("SQS_QUEUE_URL is required for large PR reviews.")

    # Extract region from queue URL (e.g. sqs.us-east-1.amazonaws.com)
    region_name = "us-east-1"
    try:
        if ".amazonaws.com" in settings.sqs_queue_url:
            # URL format: https://sqs.<region>.amazonaws.com/<account>/<queue>
            parts = settings.sqs_queue_url.split(".")
            if len(parts) > 1 and parts[1]:
                region_name = parts[1]
    except Exception:
        logger.warning(
            "Failed parsing region from SQS Queue URL: %s. Defaulting to us-east-1.",
            settings.sqs_queue_url,
        )

    body_str = payload.model_dump_json()

    logger.info("Enqueueing review job to SQS (region: %s).", region_name)

    await asyncio.to_thread(
        _send_sqs_message,
        settings.sqs_queue_url,
        body_str,
        region_name,
    )


async def process_sqs_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Consume record batch from AWS SQS Event Source Mapping.

    Deserializes payload and executes run_inline_review directly.
    """
    # Load secrets on cold start/execution if SQS Lambda runs standalone
    load_secrets_into_env()
    get_settings.cache_clear()
    settings = get_settings()
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        validate_required_settings(settings)

    records = event.get("Records", [])
    logger.info("Processing SQS event containing %d record(s).", len(records))

    failures = []

    for index, record in enumerate(records):
        body = record.get("body")
        if not body:
            logger.warning("Empty SQS record body found. Skipping record.")
            continue

        try:
            raw_payload = json.loads(body)
            payload = WebhookPayload.model_validate(raw_payload)
            completed = await run_inline_review(payload, settings)
            if not completed:
                raise RuntimeError("Review could not be completed or failed open.")

        except Exception:
            # With ReportBatchItemFailures enabled, SQS retries only failed records.
            logger.error("Failed to process SQS message record.")
            failures.append(
                {
                    "itemIdentifier": record.get("messageId") or str(index),
                }
            )

    return {"batchItemFailures": failures}


def sqs_lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda entry-point for SQS trigger integrations.

    Synchronously wraps the async event consumer.
    """
    result = asyncio.run(process_sqs_event(event))
    return {
        "statusCode": 200,
        "body": "Processed SQS records successfully.",
        **result,
    }
