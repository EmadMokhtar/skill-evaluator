"""The case limit both keyed adapters hand their framework, and its stop message.

`budget:` is scored by `BudgetEvaluator` after a run ends, which is too late
for a case that runs away: it would spend until its timeout first. So the
adapters also hand `max_tokens` -- the one limit both frameworks can enforce
while a run spends -- to the framework itself, which stops the run as soon as
the total crosses it. Kept here, outside either adapter and importing neither
framework, so the two stop at the same number and say so in the same words.

`max_cost_usd` is not handed over: PydanticAI has no cost limit at the
version floor this project supports, and LangChain has none at all. The
run-wide `max_spend_usd` ceiling is what bounds money.
"""

from __future__ import annotations

from skill_lens.models import EvalCase


def token_limit(case: EvalCase) -> int | None:
    """The case's `budget.max_tokens`, or None when it declares none."""
    return case.budget.max_tokens if case.budget is not None else None


class TokenBudgetExceeded(Exception):
    """A run crossed its case's `max_tokens` and its framework stopped it.

    Not transient by either adapter's rule -- no `status_code`, and a name
    that ends in neither `ConnectionError` nor `TimeoutError` -- so a stopped
    run is never retried into spending the limit twice.
    """

    def __init__(self, runner: str, limit: int, tokens: int) -> None:
        super().__init__(
            f"{runner} stopped the run at {tokens} tokens, over the case's budget "
            f"max_tokens of {limit}"
        )


def stop_message(runner: str, limit: int, tokens: int) -> str:
    """`RunResult.error` for a run its framework stopped at `max_tokens`."""
    exc = TokenBudgetExceeded(runner, limit, tokens)
    return f"{type(exc).__name__}: {exc}"
