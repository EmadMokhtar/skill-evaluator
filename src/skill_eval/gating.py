"""Turn a RunReport into a pass/fail decision and a process exit code."""

from __future__ import annotations

from pydantic import BaseModel, Field

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
) -> GateResult:
    """Apply thresholds to a report. Errored cases fail the gate by default."""
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

    passed = not reasons
    return GateResult(passed=passed, exit_code=EXIT_OK if passed else EXIT_FAILED, reasons=reasons)
