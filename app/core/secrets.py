"""
app/core/secrets.py

AWS Secrets Manager integration (Milestone 5).

At Lambda cold-start this module fetches all required secrets from AWS
Secrets Manager and injects them as environment variables so that
`app.core.config.Settings` can pick them up transparently.

During local development the .env file is used instead — this module is
a no-op when the AWS_LAMBDA_FUNCTION_NAME environment variable is not set.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def load_secrets_into_env(secret_name: Optional[str] = None) -> None:
    """
    Fetch secrets from AWS Secrets Manager and set them as environment variables.

    This is intentionally a stub for Milestone 1.  Full implementation will
    be added in Milestone 5 alongside the SAM/Terraform deployment work.

    Args:
        secret_name: Name or ARN of the Secrets Manager secret.
                     Defaults to the PR_REVIEW_SECRET_NAME env var.
    """
    # Only run inside an actual Lambda environment.
    if not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        logger.debug("Not running on Lambda — skipping Secrets Manager fetch.")
        return

    # ── Milestone 5: replace this stub ──────────────────────────────────────
    # import boto3
    # client = boto3.client("secretsmanager")
    # secret_name = secret_name or os.environ["PR_REVIEW_SECRET_NAME"]
    # response = client.get_secret_value(SecretId=secret_name)
    # secrets: dict = json.loads(response["SecretString"])
    # for key, value in secrets.items():
    #     os.environ[key.upper()] = str(value)
    # logger.info("Secrets loaded from Secrets Manager: %s", secret_name)
    # ─────────────────────────────────────────────────────────────────────────

    logger.warning(
        "secrets.load_secrets_into_env is a stub — implement in Milestone 5."
    )
