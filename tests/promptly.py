"""Run a call that must not block, and fail the test if it does.

A FIFO (a named pipe) blocks `open()` until the other end connects, so a
regression in the "regular files only" rule would hang the suite rather than
fail it. Running the call in a daemon thread with a join timeout turns that
hang into an ordinary assertion failure.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

TIMEOUT_SECONDS = 5.0


def promptly(call: Callable[[], Any], timeout: float = TIMEOUT_SECONDS) -> Any:
    """`call()`'s return value, or its exception re-raised; fails if it blocks."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the test thread
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        pytest.fail(f"the call was still blocked after {timeout:g} s; a FIFO was opened")
    if "error" in box:
        raise box["error"]
    return box["value"]
