"""
tests/test_sqs.py

Unit tests for AWS SQS service producer, consumer, and Lambda handler.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.github_schemas import WebhookPayload
from app.services.sqs import (
    SQSConfigurationError,
    enqueue_payload,
    process_sqs_event,
    sqs_lambda_handler,
)
from tests.conftest import minimal_pr_payload


@pytest.mark.asyncio
@patch("app.services.sqs.boto3.client")
async def test_enqueue_payload_success(mock_boto_client) -> None:
    # 1. Setup mock payload and settings
    raw_payload = minimal_pr_payload(changed_files=20)
    payload = WebhookPayload.model_validate(raw_payload)

    settings = Settings(
        sqs_queue_url="https://sqs.us-east-2.amazonaws.com/123456789012/test-queue"
    )

    # 2. Setup mock SQS client
    mock_sqs = MagicMock()
    mock_boto_client.return_value = mock_sqs

    # 3. Call enqueue
    await enqueue_payload(payload, settings)

    # 4. Assertions
    # Verify client was created with region parsed from URL (us-east-2)
    mock_boto_client.assert_called_once_with("sqs", region_name="us-east-2")

    # Verify send_message was called with correct arguments
    mock_sqs.send_message.assert_called_once()
    args, kwargs = mock_sqs.send_message.call_args
    assert kwargs["QueueUrl"] == settings.sqs_queue_url

    # Verify message body is valid json deserializable back to our payload
    body_data = json.loads(kwargs["MessageBody"])
    assert body_data["number"] == 42
    assert body_data["repository"]["full_name"] == "octocat/Hello-World"


@pytest.mark.asyncio
@patch("app.services.sqs.boto3.client")
async def test_enqueue_payload_missing_queue_url(mock_boto_client) -> None:
    raw_payload = minimal_pr_payload(changed_files=20)
    payload = WebhookPayload.model_validate(raw_payload)
    settings = Settings(sqs_queue_url="")  # empty URL

    with pytest.raises(SQSConfigurationError):
        await enqueue_payload(payload, settings)

    # SQS client should not be instantiated
    mock_boto_client.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.sqs.load_secrets_into_env")
@patch("app.services.sqs.run_inline_review")
async def test_process_sqs_event_success(
    mock_run_inline_review, mock_load_secrets
) -> None:
    # 1. Construct SQS event payload
    raw_payload = minimal_pr_payload(changed_files=5)
    body_str = json.dumps(raw_payload)
    event = {"Records": [{"body": body_str}]}

    # 2. Run event processor
    result = await process_sqs_event(event)

    # 3. Assertions
    # Secrets should be loaded
    mock_load_secrets.assert_called_once()

    # run_inline_review should be called
    mock_run_inline_review.assert_called_once()
    args, kwargs = mock_run_inline_review.call_args
    called_payload = args[0]
    called_settings = args[1]

    assert isinstance(called_payload, WebhookPayload)
    assert called_payload.number == 42
    assert isinstance(called_settings, Settings)
    assert result == {"batchItemFailures": []}


@pytest.mark.asyncio
@patch("app.services.sqs.load_secrets_into_env")
@patch("app.services.sqs.run_inline_review")
async def test_process_sqs_event_failure_propagates(
    mock_run_inline_review, mock_load_secrets
) -> None:
    # Verify that exceptions raised during processing are propagated so SQS fails the batch
    body_str = json.dumps(minimal_pr_payload())
    event = {"Records": [{"messageId": "msg-1", "body": body_str}]}

    mock_run_inline_review.side_effect = RuntimeError("Failed to review")

    result = await process_sqs_event(event)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}


@pytest.mark.asyncio
@patch("app.services.sqs.load_secrets_into_env")
@patch("app.services.sqs.run_inline_review", return_value=False)
async def test_process_sqs_event_retries_when_fail_open_cannot_be_confirmed(
    mock_run_inline_review, mock_load_secrets
) -> None:
    event = {"Records": [{"messageId": "msg-2", "body": json.dumps(minimal_pr_payload())}]}

    result = await process_sqs_event(event)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-2"}]}


@patch("app.services.sqs.process_sqs_event")
def test_sqs_lambda_handler(mock_process_sqs_event) -> None:
    # Setup mock process coroutine
    mock_process_sqs_event.return_value = {"batchItemFailures": []}

    event = {"Records": []}
    response = sqs_lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert "Processed SQS records successfully" in response["body"]
    assert response["batchItemFailures"] == []
    mock_process_sqs_event.assert_called_once_with(event)
