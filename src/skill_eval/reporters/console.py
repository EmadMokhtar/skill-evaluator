"""Human-readable run summary."""

from __future__ import annotations

from skill_eval.gating import GateResult
from skill_eval.models import RunReport

_MARKS = {"passed": "PASS", "failed": "FAIL", "errored": "ERROR"}


def render_console(report: RunReport, gate: GateResult | None = None) -> str:
    """Render a report as plain text suitable for a terminal or CI log."""
    lines: list[str] = []
    for outcome in report.outcomes:
        mark = _MARKS[outcome.status]
        lines.append(f"[{mark}] {outcome.skill_name} :: {outcome.case_name} ({outcome.runner})")
        for score in outcome.scores:
            if not score.passed:
                lines.append(f"        {score.evaluator}: {score.detail}")
        if outcome.result is not None and outcome.result.error:
            lines.append(f"        error: {outcome.result.error}")

    if report.skipped_skills:
        lines.append("")
        lines.append(f"Skipped (no eval cases): {', '.join(report.skipped_skills)}")

    lines.append("")
    lines.append(
        f"{report.passed} passed, {report.failed} failed, "
        f"{report.errored} errored — pass rate {report.pass_rate:.0%}"
    )

    total_cost = sum(o.result.cost_usd for o in report.outcomes if o.result)
    if total_cost:
        lines.append(f"Total cost: ${total_cost:.4f}")

    if gate is not None and not gate.passed:
        lines.append("")
        lines.append("Gate FAILED:")
        lines.extend(f"  - {reason}" for reason in gate.reasons)

    return "\n".join(lines)
