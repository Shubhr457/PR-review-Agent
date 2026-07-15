"""
app/routers/health.py

GET /health — liveness / readiness probe with live dependency checks.

Dependency checks:
  - github_api : live GET /rate_limit ping (5-second timeout, no quota cost)
  - openai_api : API key format validation (no API call — avoids cost/quota burn)
  - sqs        : queue URL presence check

Always returns HTTP 200. Inspect the ``status`` field ("ok" | "degraded") and
the per-dependency ``ok`` booleans for fine-grained health data.
"""

import time
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])

# Record module import time as approximate cold-start reference.
_START_TIME = time.time()

# Never let a single dependency ping hang the health check.
_PING_TIMEOUT = 5.0


# ── Dependency check helpers ─────────────────────────────────────────────────


async def _check_github(settings: Settings) -> Dict[str, Any]:
    """Live GET /rate_limit ping — confirms auth token is valid."""
    if not settings.github_token:
        return {"ok": False, "detail": "token not configured"}
    try:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT) as client:
            response = await client.get(
                f"{settings.github_api_base_url}/rate_limit",
                headers={
                    "Authorization": f"Bearer {settings.github_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if response.is_success:
            remaining = response.json().get("rate", {}).get("remaining", "?")
            return {
                "ok": True,
                "detail": f"reachable (rate limit remaining: {remaining})",
            }
        return {"ok": False, "detail": f"HTTP {response.status_code}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:120]}


def _check_openai(settings: Settings) -> Dict[str, Any]:
    """Validate OpenAI key format — no API call to avoid cost / quota burn."""
    if not settings.openai_api_key:
        return {"ok": False, "detail": "key not configured"}
    if not settings.openai_api_key.startswith("sk-"):
        return {"ok": False, "detail": "key format invalid (expected sk-...)"}
    return {"ok": True, "detail": "key configured and format valid"}


def _check_sqs(settings: Settings) -> Dict[str, Any]:
    """Check SQS queue URL is configured."""
    if not settings.sqs_queue_url:
        return {"ok": False, "detail": "queue URL not configured"}
    return {"ok": True, "detail": "configured"}


def _check_deduplication(settings: Settings) -> Dict[str, Any]:
    if not settings.webhook_deduplication_table:
        return {"ok": False, "detail": "deduplication table not configured"}
    return {"ok": True, "detail": "configured"}


# ── Route ────────────────────────────────────────────────────────────────────


@router.get("/health/live")
async def liveness_check() -> Dict[str, Any]:
    """Indicate that the process can serve requests without external calls."""
    return {"status": "ok", "uptime_seconds": round(time.time() - _START_TIME, 2)}


@router.get("/health/ready")
@router.get("/health")
async def readiness_check(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """
    Return service status, version, uptime, and per-dependency health.

    HTTP 200 always — check the ``status`` field for "ok" vs "degraded".
    """
    github = await _check_github(settings)
    openai_dep = _check_openai(settings)
    sqs = _check_sqs(settings)
    deduplication = _check_deduplication(settings)

    all_ok = github["ok"] and openai_dep["ok"] and sqs["ok"] and deduplication["ok"]

    payload = {
        "status": "ok" if all_ok else "degraded",
        "version": settings.app_version,
        "uptime_seconds": round(time.time() - _START_TIME, 2),
        "dependencies": {
            "github_api": github,
            "openai_api": openai_dep,
            "sqs": sqs,
            "deduplication": deduplication,
        },
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )
