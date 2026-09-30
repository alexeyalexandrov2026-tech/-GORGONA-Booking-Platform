"""FastAPI application factory."""

from fastapi import FastAPI

from gorgona_booking.api import health
from gorgona_booking.api.errors import install_error_handlers
from gorgona_booking.api.health import ReadinessProbe
from gorgona_booking.api.request_id import RequestIdMiddleware
from gorgona_booking.config import Settings, assert_environment_allowed


def create_app(
    settings: Settings | None = None,
    *,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    assert_environment_allowed(settings)

    app = FastAPI(title="GORGONA Booking AI", version="0.1.0")
    app.state.settings = settings
    app.state.readiness_probe = readiness_probe

    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    return app
