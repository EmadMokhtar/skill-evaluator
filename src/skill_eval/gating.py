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

    if report.total and report.pass_rate < min_pass_rate:
        reasons.append(
            f"pass rate {report.pass_rate:.0%} is below the required {min_pass_rate:.0%}"
        )

    if fail_on_error and report.errored:
        reasons.append(f"{report.errored} case(s) errored")

    for skill_name, minimum in (per_skill_min or {}).items():
        actual = report.pass_rate_by_skill().get(skill_name)
        if actual is not None and actual < minimum:
            reasons.append(
                f"skill {skill_name!r} pass rate {actual:.0%} is below its required {minimum:.0%}"
            )

    passed = not reasons
    return GateResult(passed=passed, exit_code=EXIT_OK if passed else EXIT_FAILED, reasons=reasons)
