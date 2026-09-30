"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gorgona_booking.api import health, holds
from gorgona_booking.api.errors import install_error_handlers
from gorgona_booking.api.health import ReadinessProbe
from gorgona_booking.api.request_id import RequestIdMiddleware
from gorgona_booking.booking.service import BookingService
from gorgona_booking.config import Settings, assert_environment_allowed
from gorgona_booking.db.pool import (
    RuntimePool,
    assert_safe_runtime_role,
    create_runtime_pool,
    pool_readiness_probe,
)


def create_app(
    settings: Settings | None = None,
    *,
    pool: RuntimePool | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    """Build the app. An injected `pool` is borrowed; otherwise one is opened from
    `settings.database_url` for the app's lifetime and closed on shutdown."""
    settings = settings or Settings.from_env()
    assert_environment_allowed(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned: RuntimePool | None = None
        if app.state.pool is None and settings.database_url is not None:
            owned = create_runtime_pool(
                settings.database_url.get_secret_value(),
                min_size=settings.db_pool_min_size,
                max_size=settings.db_pool_max_size,
            )
            await owned.open(wait=True, timeout=10.0)
            try:
                # Fail fast: never serve with a credential that can bypass RLS.
                async with owned.connection() as conn:
                    await assert_safe_runtime_role(conn)
            except BaseException:
                await owned.close()
                raise
            app.state.pool = owned
            app.state.booking_service = BookingService(
                owned, hold_ttl_seconds=settings.hold_ttl_seconds
            )
            if app.state.readiness_probe is None:
                app.state.readiness_probe = pool_readiness_probe(owned)
        try:
            yield
        finally:
            if owned is not None:
                await owned.close()

    app = FastAPI(title="GORGONA Booking AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = pool
    app.state.booking_service = (
        BookingService(pool, hold_ttl_seconds=settings.hold_ttl_seconds)
        if pool is not None
        else None
    )
    app.state.readiness_probe = readiness_probe or (
        pool_readiness_probe(pool) if pool is not None else None
    )

    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(holds.router)
    return app
