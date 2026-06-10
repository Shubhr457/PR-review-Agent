"""
tests/test_secrets.py

Unit tests for AWS Secrets Manager integration (Milestone 5).
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.core.secrets import load_secrets_into_env, _DEFAULT_SECRET_NAME


class TestLoadSecretsIntoEnv:
    """Tests for the load_secrets_into_env function."""

    # ── No-op when not on Lambda ─────────────────────────────────────────────

    def test_noop_when_not_on_lambda(self):
        """Should do nothing when AWS_LAMBDA_FUNCTION_NAME is not set."""
        with patch.dict(os.environ, {}, clear=False):
            # Ensure the Lambda env var is NOT present.
            os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)
            # Should return without error and without calling boto3.
            load_secrets_into_env()

    # ── Successful secret loading ────────────────────────────────────────────

    def test_loads_secrets_on_lambda(self):
        """Should fetch and inject secrets when running on Lambda."""
        fake_secrets = {
            "GITHUB_TOKEN": "ghp_test_123",
            "OPENAI_API_KEY": "sk-test-456",
            "GITHUB_WEBHOOK_SECRET": "webhook-secret-789",
        }

        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps(fake_secrets),
        }

        with patch.dict(
            os.environ,
            {"AWS_LAMBDA_FUNCTION_NAME": "pr-review-agent-webhook"},
            clear=False,
        ):
            with patch("app.core.secrets.boto3.client", return_value=mock_client):
                load_secrets_into_env("test-secret")

            # Verify secrets were injected into os.environ.
            assert os.environ["GITHUB_TOKEN"] == "ghp_test_123"
            assert os.environ["OPENAI_API_KEY"] == "sk-test-456"
            assert os.environ["GITHUB_WEBHOOK_SECRET"] == "webhook-secret-789"

            # Verify boto3 was called correctly.
            mock_client.get_secret_value.assert_called_once_with(
                SecretId="test-secret"
            )

        # Clean up injected env vars.
        for key in fake_secrets:
            os.environ.pop(key, None)

    def test_uses_env_var_for_secret_name(self):
        """Should use PR_REVIEW_SECRET_NAME env var when no argument given."""
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"TEST_KEY": "test_value"}),
        }

        with patch.dict(
            os.environ,
            {
                "AWS_LAMBDA_FUNCTION_NAME": "test-fn",
                "PR_REVIEW_SECRET_NAME": "custom/secret/name",
            },
            clear=False,
        ):
            with patch("app.core.secrets.boto3.client", return_value=mock_client):
                load_secrets_into_env()  # No explicit name.

            mock_client.get_secret_value.assert_called_once_with(
                SecretId="custom/secret/name"
            )

        os.environ.pop("TEST_KEY", None)

    def test_uses_default_secret_name(self):
        """Should fall back to the default secret name."""
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"X": "1"}),
        }

        with patch.dict(
            os.environ,
            {"AWS_LAMBDA_FUNCTION_NAME": "test-fn"},
            clear=False,
        ):
            # Remove the custom name env var if it exists.
            os.environ.pop("PR_REVIEW_SECRET_NAME", None)

            with patch("app.core.secrets.boto3.client", return_value=mock_client):
                load_secrets_into_env()

            mock_client.get_secret_value.assert_called_once_with(
                SecretId=_DEFAULT_SECRET_NAME
            )

        os.environ.pop("X", None)

    def test_uppercases_keys(self):
        """Should uppercase all secret keys when injecting into environ."""
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"github_token": "ghp_lower"}),
        }

        with patch.dict(
            os.environ,
            {"AWS_LAMBDA_FUNCTION_NAME": "test-fn"},
            clear=False,
        ):
            with patch("app.core.secrets.boto3.client", return_value=mock_client):
                load_secrets_into_env("test")

            assert os.environ["GITHUB_TOKEN"] == "ghp_lower"

        os.environ.pop("GITHUB_TOKEN", None)

    # ── Error handling ───────────────────────────────────────────────────────

    def test_raises_on_client_error(self):
        """Should raise RuntimeError when Secrets Manager call fails."""
        mock_client = MagicMock()
        mock_client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}},
            "GetSecretValue",
        )

        with patch.dict(
            os.environ,
            {"AWS_LAMBDA_FUNCTION_NAME": "test-fn"},
            clear=False,
        ):
            with patch("app.core.secrets.boto3.client", return_value=mock_client):
                with pytest.raises(RuntimeError, match="Could not load secrets"):
                    load_secrets_into_env("missing-secret")

    def test_raises_on_invalid_json(self):
        """Should raise RuntimeError when secret value is not valid JSON."""
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": "not-json!!!",
        }

        with patch.dict(
            os.environ,
            {"AWS_LAMBDA_FUNCTION_NAME": "test-fn"},
            clear=False,
        ):
            with patch("app.core.secrets.boto3.client", return_value=mock_client):
                with pytest.raises(RuntimeError, match="not valid JSON"):
                    load_secrets_into_env("bad-json-secret")
