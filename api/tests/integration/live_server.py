"""Run the real ASGI app on a loopback port for black-box HTTP/browser tests."""

import asyncio
import os
import selectors
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def live_server(app: FastAPI, port: int) -> Iterator[str]:
    """Serve `app` on 127.0.0.1:`port`; yields the base URL and stops it on exit."""
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            proxy_headers=False,
        )
    )

    def serve() -> None:
        if os.name == "nt":
            asyncio.run(
                server.serve(),
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
            )
        else:
            asyncio.run(server.serve())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "live server did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive(), "live server did not stop"
