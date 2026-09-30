"""Liveness and readiness. Readiness reports the real dependency state, never a canned "ok"."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("gorgona_booking.health")

# A readiness probe raises if its dependency is not usable.
type ReadinessProbe = Callable[[], Awaitable[None]]

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    probe: ReadinessProbe | None = getattr(request.app.state, "readiness_probe", None)
    if probe is None:
        return JSONResponse(
            {"status": "unavailable", "checks": {"database": "not_configured"}}, status_code=503
        )
    try:
        await probe()
    except Exception as exc:
        # Details go to logs only; the response never carries driver or server messages.
        logger.warning("readiness probe failed", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            {"status": "unavailable", "checks": {"database": "failed"}}, status_code=503
        )
    return JSONResponse({"status": "ready", "checks": {"database": "ok"}})
