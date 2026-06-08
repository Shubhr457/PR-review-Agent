"""
app/routers/health.py

GET /health — liveness / readiness probe.

Used by:
  - API Gateway health checks
  - CloudWatch Synthetic Canaries (Milestone 6)
  - Local dev smoke-tests
"""

import time
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])

# Record the time this module was first imported (approximate cold-start time).
_START_TIME = time.time()


@router.get("/health")
async def health_check(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    """
    Return service status, version, and basic dependency readiness.

    HTTP 200 → healthy.  Any other status code signals a problem to the
    load-balancer / API Gateway.
    """
    return {
        "status": "ok",
        "version": settings.app_version,
        "uptime_seconds": round(time.time() - _START_TIME, 2),
        "dependencies": {
            # In later milestones these will make live ping calls.
            "github_api": "configured" if settings.github_token else "missing",
            "openai_api": "configured" if settings.openai_api_key else "missing",
            "sqs": "configured" if settings.sqs_queue_url else "missing",
        },
    }
