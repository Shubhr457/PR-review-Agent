"""
main.py

FastAPI application entry-point.

Local development:
    uvicorn main:app --reload --port 8000

AWS Lambda (Milestone 5):
    The Mangum handler wraps `app` for use with API Gateway + Lambda.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings, validate_required_settings
from app.core.secrets import load_secrets_into_env
from app.routers import health, reviews, webhook

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup / shutdown lifecycle.

    Startup:
      - Load secrets from AWS Secrets Manager (no-op in local dev).
      - Log key configuration for observability.
    """
    # Load secrets first so Settings picks them up from env vars.
    load_secrets_into_env()
    get_settings.cache_clear()

    settings = get_settings()
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        validate_required_settings(settings)
    logger.info(
        "PR Review Agent v%s starting — docs=%s",
        settings.app_version,
        settings.enable_docs,
    )
    logger.info(
        "Config: max_files=%s | max_diff_chars=%s | large_pr_threshold=%s",
        settings.max_files_per_pr,
        settings.max_diff_chars,
        settings.large_pr_threshold,
    )

    yield  # ── application is live ──

    logger.info("PR Review Agent shutting down.")


# ── Application factory ───────────────────────────────────────────────────────


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="GitHub PR Review Agent",
        description=(
            "AI-powered automated code review via FastAPI + AWS Lambda. "
            "Receives GitHub pull_request webhooks, analyses diffs with "
            "OpenAI GPT-4o, and posts structured review comments."
        ),
        version=settings.app_version,
        # Disable interactive docs in production (FR-10 / Section 10).
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        lifespan=lifespan,
    )

    # CORS is intentionally restrictive — only GitHub's servers should POST.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://github.com"],
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )

    # ── Routers ──────────────────────────────────────────────────────────────
    app.include_router(health.router)
    app.include_router(webhook.router)
    app.include_router(reviews.router)

    return app


app = create_app()

# ── AWS Lambda handler (Milestone 5) ─────────────────────────────────────────
# Mangum wraps the ASGI app for use with AWS Lambda + API Gateway.
# lifespan="on" ensures startup events (secrets loading) run on cold start.
from mangum import Mangum

handler = Mangum(app, lifespan="on")
