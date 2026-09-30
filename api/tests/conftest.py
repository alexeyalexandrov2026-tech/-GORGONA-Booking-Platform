import asyncio
import sys

import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> tuple[str, dict[str, object]]:
    # psycopg's async driver cannot run on Windows' default Proactor loop.
    options: dict[str, object] = {}
    if sys.platform == "win32":
        options["loop_factory"] = asyncio.SelectorEventLoop
    return ("asyncio", options)
