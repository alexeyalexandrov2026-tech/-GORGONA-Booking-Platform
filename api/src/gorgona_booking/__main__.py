"""Server entrypoint (local development and the container): `python -m gorgona_booking`.

Runs uvicorn inside our own event loop so Windows uses a selector loop,
which psycopg's async driver requires. Logs are JSON on stdout. Traces and
metrics go to Application Insights only when APPLICATIONINSIGHTS_CONNECTION_STRING
is set.
"""

import asyncio
import os
import sys

import uvicorn

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.observability import configure_logging, start_telemetry


def main() -> None:
    configure_logging(os.environ.get("GBA_LOG_LEVEL", "INFO"))
    settings = Settings.from_env()
    app = create_app(settings)
    start_telemetry(app, settings)
    config = uvicorn.Config(
        app,
        host=os.environ.get("GBA_HOST", "127.0.0.1"),
        port=int(os.environ.get("GBA_PORT", "8000")),
        proxy_headers=False,
        # Our JSON logging and access log (route templates, no raw paths or client IPs).
        log_config=None,
        access_log=False,
        # Finish in-flight requests on SIGTERM within the platform's grace period (30 s).
        timeout_graceful_shutdown=25,
    )
    server = uvicorn.Server(config)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(server.serve(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
