"""The case limit both keyed adapters hand their framework, and the notes they share.

`budget:` is scored by `BudgetEvaluator` after a run ends, which is too late
for a case that runs away: it would spend until a framework default stopped
it. So the adapters also hand `max_tokens` -- the one limit both frameworks
can enforce while a run spends -- to the framework itself, which stops the
run as soon as the total crosses it. A stopped run is not errored: it is
returned as far as it got, with `stop_note` set, and its `max_tokens` check
fails. Kept here, outside either adapter and importing neither framework, so
the two stop at the same number and report it in the same words.

`max_cost_usd` is not handed over: PydanticAI has no cost limit at the
version floor this project supports, and LangChain has none at all. The
run-wide `max_spend_usd` ceiling is what bounds money.
"""

from __future__ import annotations

from skill_lens.models import EvalCase


def token_limit(case: EvalCase) -> int | None:
    """The case's `budget.max_tokens`, or None when it declares none."""
    return case.budget.max_tokens if case.budget is not None else None


def stop_note(runner: str) -> str:
    """`RunResult.stop_note` for a run its framework stopped at `max_tokens`."""
    return f"{runner} stopped the run before the agent finished, at the case's max_tokens"


class TokenBudgetExceeded(Exception):
    """A run crossed its case's `max_tokens`; raised inside an adapter to stop it.

    Never reaches a report: the adapter catches it and returns the run as
    far as it got. Not transient by either adapter's rule -- no
    `status_code`, and a name that ends in neither `ConnectionError` nor
    `TimeoutError` -- so a stopped run is never retried.
    """


def no_usage_notes(runner: str) -> dict[str, str]:
    """`usage_note` and `cost_note` for a run whose responses carried no usage.

    A provider that reports no token usage leaves the counts at `0`, and `0`
    is not a measurement: `max_tokens` could neither stop the run nor be
    checked, and its cost could not be priced. Both notes say so, so the
    budget check fails as not evaluated and the spend ceiling does not take
    the `0.0` for free.
    """
    note = f"{runner} received no token usage from the provider"
    return {"usage_note": note, "cost_note": f"{note}; cost not calculated"}
