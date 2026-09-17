"""The Runner protocol — the seam every agent framework plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_lens.models import EvalCase, RunResult, Skill
from skill_lens.scripts import ScriptRuntime
from skill_lens.workspace import Workspace


@runtime_checkable
class Runner(Protocol):
    """Runs a case against a skill and reports what happened."""

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

        A runner may also define an optional `preflight(skills, cases_by_skill)
        -> ProductStatus | None`. The orchestrator calls it once per run, after
        discovery and before any case runs, with the candidate-arm skills and the
        cases planned for this runner. It raises an authoring error to abort the
        run before anything is spent, and may return a `ProductStatus` for the
        report. The framework runners define none; `ProductRunner` does.
        """
        ...


class RunnerDependencyError(Exception):
    """Raised when the optional extra providing a runner or judge is not installed.

    A setup error, not a provider failure: `cli.py` turns it into a clean exit
    2 with the install hint, so an adapter must let it propagate rather than
    swallow it into `RunResult.error`.
    """
