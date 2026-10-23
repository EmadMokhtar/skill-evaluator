"""Run a case's sessions against one workspace, and merge them into one result.

A case with `sessions:` is one work item: one workspace, one outcome, one
score. Each session is a fresh `Runner.run` -- no conversation history, the
tools built again -- so the workspace is the only thing that carries over.
That is the claim a "resume from files" skill makes, and the reason the
sessions run here rather than inside a runner: every runner, a third-party
one included, gets sessions without a change, and never sees `sessions:`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from skill_lens.models import EvalCase, RunResult, Skill
from skill_lens.runners.base import Runner
from skill_lens.workspace import Workspace


def run_sessions(
    runner: Runner,
    skill: Skill,
    case: EvalCase,
    workspace: Workspace | None,
    extra: Mapping[str, Any],
) -> RunResult:
    """One runner call for a `task:` case, one per session for a `sessions:` case.

    A `task:` case makes exactly the call the orchestrator made before
    sessions existed, with the case object itself, so a one-session run is
    unchanged. A `sessions:` case hands the runner a copy whose `task` is that
    session's text. The loop stops at the first errored session: later
    sessions would build on a state the case never meant to test, and every
    one is paid for.
    """
    if case.sessions is None:
        return runner.run(skill, case, workspace=workspace, **extra)
    results: list[RunResult] = []
    for text in case.sessions:
        session = case.model_copy(update={"task": text, "sessions": None})
        result = runner.run(skill, session, workspace=workspace, **extra)
        results.append(result)
        if result.errored:
            break
    return merge_results(results, case.sessions)


def _notes(values: list[str]) -> str:
    """Every distinct non-empty note, in session order.

    One session's note is enough to keep the merged note non-empty, so
    `BudgetEvaluator` refuses a total that is missing that session's share.
    """
    return "; ".join(dict.fromkeys(value for value in values if value))


def merge_results(results: list[RunResult], tasks: list[str]) -> RunResult:
    """The one `RunResult` every evaluator and reporter reads.

    `results` holds the sessions that ran, in order -- fewer than `tasks`
    when one errored. The output is the last session's, the tool calls and
    the transcript are every session's, and the spend is the sum, so the
    budget measures the whole case and the trajectory cannot miss a call
    made in an earlier session. Spend is kept when a session errored: it was
    spent.
    """
    total = len(tasks)
    transcript: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        transcript.append({"session": index + 1, "of": total, "task": tasks[index]})
        transcript.extend(result.transcript)
    flags = [result.skill_triggered for result in results if result.skill_triggered is not None]
    last = results[-1]
    error = None
    if last.error is not None:
        error = f"session {len(results)} of {total}: {last.error}"
    return RunResult(
        output=last.output,
        tool_calls=[call for result in results for call in result.tool_calls],
        transcript=transcript,
        input_tokens=sum(result.input_tokens for result in results),
        output_tokens=sum(result.output_tokens for result in results),
        latency_ms=sum(result.latency_ms for result in results),
        cost_usd=sum(result.cost_usd for result in results),
        cost_note=_notes([result.cost_note for result in results]),
        usage_note=_notes([result.usage_note for result in results]),
        model=next((result.model for result in results if result.model), ""),
        # Any session loading the skill is a trigger: a negative control must
        # fail when the first session reached for the skill, not only the last.
        skill_triggered=any(flags) if flags else None,
        error=error,
    )
