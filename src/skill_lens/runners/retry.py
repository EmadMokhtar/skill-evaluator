"""Retry a provider call on transient failures -- shared by every adapter.

The policy is one function so two adapters cannot disagree about what "try
again" means. Each adapter supplies its own `is_transient`, because what a
rate limit or an outage looks like is spelled differently by every framework
-- and this module imports none of them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Statuses worth another attempt: request timeouts, conflicts and rate limits.
# A 401 or 404 will never fix itself.
TRANSIENT_STATUSES = frozenset({408, 409, 429})


def transient_status(status_code: int) -> bool:
    """Whether an HTTP status is worth another attempt.

    The three listed statuses, plus anything the provider blames on itself.
    """
    return status_code in TRANSIENT_STATUSES or status_code >= 500


def run_with_retries(
    call: Callable[[], T],
    is_transient: Callable[[Exception], bool],
    retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], None],
) -> T:
    """Call `call()`; on a transient exception, sleep and try again.

    Up to `retries` further attempts are made, sleeping `backoff_seconds`
    before the first and doubling before each one after. A non-transient
    exception, or the last attempt failing, re-raises as-is so the adapter can
    report it through `RunResult.error` / `JudgeVerdict.error`.
    """
    delay = backoff_seconds
    attempt = 0
    while True:
        try:
            return call()
        except Exception as exc:
            if attempt >= retries or not is_transient(exc):
                raise
            sleep(delay)
            delay *= 2
            attempt += 1
