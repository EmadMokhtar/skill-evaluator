"""Turn a RunReport into a pass/fail decision and a process exit code."""

from __future__ import annotations

from pydantic import BaseModel, Field

from skill_eval.comparison import Delta
from skill_eval.models import RunReport

EXIT_OK = 0
EXIT_FAILED = 1


class GateResult(BaseModel):
    """Whether a run met the configured bar, and why not if it did not."""

    passed: bool
    exit_code: int
    reasons: list[str] = Field(default_factory=list)


def evaluate_gate(
    report: RunReport,
    min_pass_rate: float = 1.0,
    fail_on_error: bool = True,
    per_skill_min: dict[str, float] | None = None,
    min_delta: float | None = None,
    delta: Delta | None = None,
) -> GateResult:
    """Apply thresholds to a report. Errored cases fail the gate by default.

    Every pre-existing rule reads the candidate arm (see `RunReport`). `min_delta`
    adds the comparative rules: the improvement must clear the bar, and it must
    have been measured against something -- a gate that verified nothing must
    never report a pass.
    """
    reasons: list[str] = []

    if report.total == 0:
        if report.tag_filtered_skills:
            names = ", ".join(report.tag_filtered_skills)
            reasons.append(
                f"no eval cases ran: the --tag filter excluded every case for skill(s): {names}"
            )
        elif report.skipped_skills:
            names = ", ".join(report.skipped_skills)
            reasons.append(
                "no eval cases ran: all discovered skill(s) were skipped for "
                f"having no eval cases: {names}"
            )
        else:
            reasons.append("no eval cases ran: no skills were found")
    elif report.pass_rate < min_pass_rate:
        reasons.append(
            f"pass rate {report.pass_rate:.0%} is below the required {min_pass_rate:.0%}"
        )

    if fail_on_error and report.errored:
        case_word = "case" if report.errored == 1 else "cases"
        reasons.append(f"{report.errored} {case_word} errored")

    pass_rates = report.pass_rate_by_skill()
    for skill_name, minimum in (per_skill_min or {}).items():
        actual = pass_rates.get(skill_name)
        if actual is None:
            if skill_name in report.skipped_skills:
                msg = (
                    f"skill {skill_name!r} requires a pass rate of {minimum:.0%} "
                    f"but was skipped (no eval cases)"
                )
            else:
                msg = (
                    f"skill {skill_name!r} requires a pass rate of {minimum:.0%} "
                    f"but produced no results"
                )
            reasons.append(msg)
        elif actual < minimum:
            reasons.append(
                f"skill {skill_name!r} pass rate {actual:.0%} is below its required {minimum:.0%}"
            )

    if min_delta is not None:
        if delta is None:
            reasons.append(
                "a minimum delta was required but no baseline arm ran, so no "
                "improvement could be measured"
            )
        else:
            comparable = [case for case in delta.cases if case.comparable]
            if not comparable:
                reasons.append(
                    "a minimum delta was required but no case had a comparable "
                    "baseline, so no improvement could be measured"
                )
            elif delta.pass_rate_delta < min_delta:
                reasons.append(
                    f"pass-rate delta {delta.pass_rate_delta:+.0%} is below the "
                    f"required {min_delta:+.0%}"
                )
        for note in report.baseline_notes:
            # A deliberately skipped baseline is not a failure -- nothing went
            # wrong. An unresolvable one is: treating it as "no change" would
            # let a repo pass this gate forever by deleting its git history.
            if note.kind == "unavailable":
                reasons.append(
                    f"skill {note.skill_name!r} has no resolvable baseline: {note.reason}"
                )

    passed = not reasons
    return GateResult(passed=passed, exit_code=EXIT_OK if passed else EXIT_FAILED, reasons=reasons)
