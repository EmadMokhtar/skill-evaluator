"""Turn a RunReport into a pass/fail decision and a process exit code."""

from __future__ import annotations

from pydantic import BaseModel, Field

from skill_lens.comparison import Delta
from skill_lens.models import RunReport

EXIT_OK = 0
EXIT_FAILED = 1


def usd(amount: float) -> str:
    """A dollar amount as a person set it: `$0.50`, never `$0.5` or a cent rounded away."""
    if amount == round(amount, 2):
        return f"${amount:.2f}"
    return f"${amount:.6f}".rstrip("0")


def spend_ceiling_reasons(report: RunReport) -> list[str]:
    """Why the spend ceiling fails this run, or nothing when it does not.

    Two causes, each its own reason. A ceiling that met unpriced spend could
    not be enforced, whether or not anything was cut: an unpriced `0.0` is
    not free. And a run that left any work item unstarted -- of either arm --
    measured less than it was asked to, whatever the pass rate of what ran.
    A ceiling the last item crossed cut nothing, and is no reason at all.
    Shared with the JUnit reporter, which states the same reasons on the case
    it adds so that a cut-short run never renders green.
    """
    reasons: list[str] = []
    ceiling = report.spend_ceiling
    if ceiling is not None and ceiling.unpriced:
        reasons.append(
            f"the spend ceiling of {usd(ceiling.max_spend_usd)} could not be enforced: "
            f"spend was not priced for {ceiling.unpriced}"
        )
    if report.not_run:
        count = len(report.not_run)
        cases = "case" if count == 1 else "cases"
        # The summary line counts the candidate arm only, so a count that
        # includes baseline runs says how it splits.
        baseline = sum(1 for item in report.not_run if item.arm == "baseline")
        arms = f" ({count - baseline} candidate, {baseline} baseline)" if baseline else ""
        if ceiling is not None and not ceiling.unpriced:
            cause = (
                f"the spend ceiling of {usd(ceiling.max_spend_usd)} was reached "
                f"(${report.spent_usd:.4f} recorded)"
            )
        else:
            cause = "the spend ceiling stopped the run"
        reasons.append(f"{count} {cases} did not run{arms}: {cause}")
    return reasons


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
        # A filter that matched nothing is named first: it is the likeliest
        # cause of an empty run and the one a typo produces.
        if report.tag_filtered_skills:
            names = ", ".join(report.tag_filtered_skills)
            reasons.append(
                f"no eval cases ran: the --tag filter excluded every case for skill(s): {names}"
            )
        if report.case_filtered_skills:
            names = ", ".join(report.case_filtered_skills)
            reasons.append(
                f"no eval cases ran: the --case filter matched no case for skill(s): {names}"
            )
        if not report.tag_filtered_skills and not report.case_filtered_skills:
            if report.skipped_skills:
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

    # Whatever the pass rate and fail_on_error say: a run the ceiling cut
    # short is never a pass, and a ceiling that could not see spend was not
    # enforced.
    reasons.extend(spend_ceiling_reasons(report))

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
            elif delta.per_runner:
                # Every runner must meet the bar: an aggregate across runners
                # lets a strong one hide a regression in another (issue #93),
                # and a runner with nothing comparable measured nothing.
                for runner in delta.runners:
                    if runner.comparable_cases == 0:
                        reasons.append(
                            "a minimum delta was required but no case under runner "
                            f"{runner.runner!r} had a comparable baseline, so no "
                            "improvement could be measured for it"
                        )
                    elif runner.pass_rate_delta < min_delta:
                        reasons.append(
                            f"pass-rate delta under runner {runner.runner!r} "
                            f"{runner.pass_rate_delta:+.0%} is below the required "
                            f"{min_delta:+.0%}"
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
