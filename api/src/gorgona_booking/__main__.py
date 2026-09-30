"""Local development server: `uv run python -m gorgona_booking`.

Runs uvicorn inside our own event loop so Windows uses a selector loop,
which psycopg's async driver requires.
"""

import asyncio
import os
import sys

import uvicorn

from gorgona_booking.api.app import create_app


def main() -> None:
    config = uvicorn.Config(
        create_app(),
        host=os.environ.get("GBA_HOST", "127.0.0.1"),
        port=int(os.environ.get("GBA_PORT", "8000")),
        proxy_headers=False,
        # Finish in-flight requests on SIGTERM within the platform's grace period (30 s).
        timeout_graceful_shutdown=25,
    )
    server = uvicorn.Server(config)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(server.serve(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
