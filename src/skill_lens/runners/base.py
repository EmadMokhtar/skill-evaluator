"""The Runner protocol — the seam every agent framework plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_lens.models import EvalCase, RunResult, Skill
from skill_lens.scripts import ScriptRuntime
from skill_lens.workspace import Workspace


@runtime_checkable
class Runner(Protocol):
    """Runs a case against a skill and reports what happened.

    A runner may also define an optional `preflight(skills, cases_by_skill)
    -> ProductStatus | None`. The orchestrator calls it once per run, after
    discovery and before any case runs, with the candidate-arm skills and the
    cases planned for this runner. It raises an authoring error to abort the
    run before anything is spent, and may return a `ProductStatus` for the
    report. The framework runners use it to refuse a `trajectory:` naming a
    tool they cannot offer the case (`check_trajectory_names`) and return
    None; `ProductRunner` returns its status.

    A runner may also define an optional `unpriced() -> str`: why its spend
    can never be priced in US dollars, or "" when it can. The orchestrator
    asks it only when the run sets a spend ceiling, and refuses the run
    before any case when the answer is not empty.
    """

    name: str

    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: ScriptRuntime | None = None,
    ) -> RunResult:
        """Execute `case` with `skill` loaded, returning a RunResult.

        Takes the whole case, not just its task string, because a runner also
        builds the environment the case declares (its mock tools).

        `workspace` is the contained directory the case asked for, already
        created and seeded by the orchestrator, or None when the case declared
        no `workspace:` block. It arrives as a `Workspace` rather than a
        `Path` so the repository's configured limits travel with it -- an
        adapter rebuilding one from a bare path would silently reinstate the
        defaults.

        The orchestrator, not the runner, owns the directory's lifetime: it is
        deleted only after every evaluator has read it.

        Implementations must not raise for provider failures; they set
        RunResult.error instead so the orchestrator can mark the case errored.

        `scripts` is the run's script policy plus the sandbox decision, or
        None when execution is off. An adapter offers `run_script` only when
        it is set AND the skill's `bundle_root` has something under
        `scripts/`; the two read tools need only `bundle_root`. Additive with
        a default, so a runner written against Part 1 keeps working.

        `case.task` is always set: the orchestrator runs a `sessions:` case
        one session at a time and hands the runner a copy per session whose
        `task` is that session's text. A runner handed the whole case
        directly reports it through `refuse_sessions` instead of sending
        None as the prompt.
        """
        ...


SESSIONS_REFUSED = (
    "this case declares sessions:, and a runner runs one session at a time; run the "
    "case through run_evals, which calls the runner once per session"
)


def refuse_sessions(case: EvalCase) -> RunResult | None:
    """An errored result for a case still carrying `sessions:`, else None.

    Only a caller that bypasses the orchestrator can reach a runner with one.
    It is a mistake in the calling code, not a provider failure, but a runner
    still never raises for it: it returns this, and spends nothing.
    """
    if case.sessions is None:
        return None
    return RunResult(error=SESSIONS_REFUSED)


class RunnerDependencyError(Exception):
    """Raised when the optional extra providing a runner or judge is not installed.

    A setup error, not a provider failure: `cli.py` turns it into a clean exit
    2 with the install hint, so an adapter must let it propagate rather than
    swallow it into `RunResult.error`.
    """
