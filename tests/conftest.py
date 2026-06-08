"""
tests/conftest.py

Shared pytest fixtures for Milestone 1 tests.
"""

import hashlib
import hmac
# import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from main import create_app

# ── Test secret used across all webhook tests ─────────────────────────────────
TEST_WEBHOOK_SECRET = "test-webhook-secret-m1"


# ── Override settings so tests never need a real .env file ───────────────────


def override_settings() -> Settings:
    return Settings(
        github_token="ghp_test_token",
        openai_api_key="sk-test",
        github_webhook_secret=TEST_WEBHOOK_SECRET,
        sqs_queue_url="https://sqs.us-east-1.amazonaws.com/000000000000/test-queue",
        enable_docs=True,
        app_version="1.0.0-test",
    )


@pytest.fixture(scope="session")
def app():
    """Create the FastAPI app with test settings injected."""
    _app = create_app()
    _app.dependency_overrides[get_settings] = override_settings
    return _app


@pytest.fixture(scope="session")
def client(app):
    """Synchronous TestClient (no real event loop needed for M1)."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_signature(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    """Compute the X-Hub-Signature-256 header value for a payload."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def webhook_headers(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> dict:
    """Return headers dict with a valid GitHub signature."""
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": make_signature(body, secret),
        "X-GitHub-Event": "pull_request",
    }


# ── Canonical minimal PR webhook payload ─────────────────────────────────────


def minimal_pr_payload(
    action: str = "opened",
    pr_number: int = 42,
    changed_files: int = 3,
) -> dict:
    """Return the smallest valid pull_request webhook payload."""
    return {
        "action": action,
        "number": pr_number,
        "pull_request": {
            "number": pr_number,
            "title": "feat: add amazing feature",
            "state": "open",
            "body": "This is a test PR.",
            "user": {"login": "octocat", "id": 1},
            "head": {
                "ref": "feature-branch",
                "sha": "abc123",
                "repo": {
                    "id": 1,
                    "name": "Hello-World",
                    "full_name": "octocat/Hello-World",
                    "clone_url": "https://github.com/octocat/Hello-World.git",
                    "default_branch": "main",
                },
            },
            "base": {
                "ref": "main",
                "sha": "def456",
                "repo": {
                    "id": 1,
                    "name": "Hello-World",
                    "full_name": "octocat/Hello-World",
                    "clone_url": "https://github.com/octocat/Hello-World.git",
                    "default_branch": "main",
                },
            },
            "url": "https://api.github.com/repos/octocat/Hello-World/pulls/42",
            "html_url": "https://github.com/octocat/Hello-World/pull/42",
            "diff_url": "https://github.com/octocat/Hello-World/pull/42.diff",
            "additions": 10,
            "deletions": 2,
            "changed_files": changed_files,
        },
        "repository": {
            "id": 1,
            "name": "Hello-World",
            "full_name": "octocat/Hello-World",
            "clone_url": "https://github.com/octocat/Hello-World.git",
            "default_branch": "main",
        },
        "sender": {"login": "octocat", "id": 1},
    }
