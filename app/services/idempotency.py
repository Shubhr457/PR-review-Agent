"""DynamoDB-backed idempotency for GitHub webhook deliveries."""

import asyncio
import logging
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class IdempotencyConfigurationError(RuntimeError):
    """Raised when webhook deduplication is not configured."""


def _claim_delivery(table_name: str, delivery_id: str, ttl: int) -> bool:
    """Atomically record a delivery ID; return False when it already exists."""
    table = boto3.resource("dynamodb").Table(table_name)
    try:
        table.put_item(
            Item={"delivery_id": delivery_id, "expires_at": ttl},
            ConditionExpression="attribute_not_exists(delivery_id)",
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def _release_delivery(table_name: str, delivery_id: str) -> None:
    boto3.resource("dynamodb").Table(table_name).delete_item(
        Key={"delivery_id": delivery_id}
    )


async def claim_delivery(table_name: str, delivery_id: str) -> bool:
    """Claim a delivery for 24 hours, or report that it was already claimed."""
    if not table_name:
        raise IdempotencyConfigurationError("Webhook deduplication is not configured.")
    return await asyncio.to_thread(
        _claim_delivery, table_name, delivery_id, int(time.time()) + 86_400
    )


async def release_delivery(table_name: str, delivery_id: str) -> None:
    """Release a failed enqueue so GitHub can safely retry the delivery."""
    if table_name:
        await asyncio.to_thread(_release_delivery, table_name, delivery_id)
