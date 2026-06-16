"""
app/core/secrets.py

AWS Secrets Manager integration (Milestone 5).

At Lambda cold-start this module fetches all required secrets from AWS
Secrets Manager and injects them as environment variables so that
`app.core.config.Settings` can pick them up transparently.

During local development the .env file is used instead — this module is
a no-op when the AWS_LAMBDA_FUNCTION_NAME environment variable is not set.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Default secret name if not overridden via env var.
_DEFAULT_SECRET_NAME = "pr-review-agent/secrets"


def load_secrets_into_env(secret_name: Optional[str] = None) -> None:
    """Fetch secrets from AWS Secrets Manager and set them as environment variables.

    Only runs inside an actual Lambda environment (``AWS_LAMBDA_FUNCTION_NAME``
    is set).  In local dev the ``.env`` file supplies all configuration and
    this function is a no-op.

    The Secrets Manager secret is expected to store a flat JSON object::

        {
            "GITHUB_TOKEN": "ghp_...",
            "OPENAI_API_KEY": "sk-...",
            "GITHUB_WEBHOOK_SECRET": "...",
            "SQS_QUEUE_URL": "https://sqs...."
        }

    Each key is uppercased and written to ``os.environ`` so
    ``pydantic-settings`` picks them up automatically.

    Args:
        secret_name: Name or ARN of the Secrets Manager secret.
                     Defaults to the ``PR_REVIEW_SECRET_NAME`` env var,
                     or ``pr-review-agent/secrets`` if that is also unset.
    """
    # Only run inside an actual Lambda environment.
    if not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        logger.debug("Not running on Lambda — skipping Secrets Manager fetch.")
        return

    secret_name = (
        secret_name or os.getenv("PR_REVIEW_SECRET_NAME") or _DEFAULT_SECRET_NAME
    )

    logger.info("Loading secrets from Secrets Manager: %s", secret_name)

    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_name)
    except ClientError as exc:
        logger.exception(
            "Failed to fetch secret '%s' from Secrets Manager.", secret_name
        )
        raise RuntimeError(
            f"Could not load secrets from Secrets Manager: {exc}"
        ) from exc

    # Parse the JSON payload.
    try:
        secrets: Dict[str, Any] = json.loads(response["SecretString"])
    except (json.JSONDecodeError, KeyError) as exc:
        logger.exception("Secret '%s' does not contain valid JSON.", secret_name)
        raise RuntimeError(f"Secret '{secret_name}' is not valid JSON: {exc}") from exc

    # Inject each key into os.environ (uppercased).
    injected = 0
    for key, value in secrets.items():
        env_key = key.upper()
        os.environ[env_key] = str(value)
        injected += 1

    logger.info(
        "Injected %d secret(s) from '%s' into environment.", injected, secret_name
    )
