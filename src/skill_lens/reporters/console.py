"""Human-readable run summary."""

from __future__ import annotations

from skill_lens.comparison import ArmStats, CaseStats, Delta, format_baseline_notes
from skill_lens.gating import GateResult
from skill_lens.models import CaseOutcome, RunReport
from skill_lens.reporters.failure_context import (
    OUTPUT_LIMIT,
    cut_note,
    failure_context,
    more_calls_note,
)

_MARKS = {"passed": "PASS", "failed": "FAIL", "errored": "ERROR"}
_INDENT = "        "  # 8 spaces: the evaluator-detail column
_DEEPER = "            "  # 12 spaces: the check-evidence column


def _fraction(stats: ArmStats) -> str:
    scored = stats.runs - stats.errored
    return f"{stats.passed}/{scored}" if scored else "0/0 (all errored)"


def _mark_for(stats: ArmStats) -> str:
    if stats.runs and stats.errored == stats.runs:
        return _MARKS["errored"]
    return _MARKS["passed" if stats.pass_rate == 1.0 else "failed"]


def _case_line(case: CaseStats) -> str:
    head = (
        f"[{_mark_for(case.candidate)}] {case.skill_name} :: {case.case_name} "
        f"({case.runner})  candidate {_fraction(case.candidate)}"
    )
    if case.baseline is None:
        return f"{head}  baseline not run"
    if not case.comparable:
        return f"{head}  baseline {_fraction(case.baseline)}  (excluded: {case.exclusion_reason})"
    difference = case.candidate.pass_rate - case.baseline.pass_rate
    return f"{head}  baseline {_fraction(case.baseline)}  {difference:+.0%}"


def _delta_block(delta: Delta) -> list[str]:
    lines = ["", f"Delta vs baseline ({delta.baseline_kind})"]
    lines.append(
        f"  pass rate  {delta.pass_rate_baseline:.0%} -> {delta.pass_rate_candidate:.0%}  "
        f"{delta.pass_rate_delta:+.0%}   (higher is better)"
    )
    lines.append(f"  tokens     {delta.tokens_delta:+.0f}   (negative is better)")
    lines.append(f"  cost       ${delta.cost_usd_delta:+.4f}   (negative is better)")
    lines.append(f"  latency    {delta.latency_ms_delta:+.0f}ms   (negative is better)")
    if delta.low_signal:
        lines.append("")
        lines.append(
            "Low-signal checks (passed with and without the skill — they measure nothing):"
        )
        lines.extend(f"  - {c.skill_name} :: {c.case_name}: {c.check_id}" for c in delta.low_signal)
    if delta.high_variance:
        lines.append("")
        lines.append("High-variance cases (repetitions disagreed — often ambiguous instructions):")
        lines.extend(
            f"  - {r.skill_name} :: {r.case_name} ({r.arm}): "
            f"{r.pass_rate:.0%}, stddev {r.stddev:.2f}"
            for r in delta.high_variance
        )
    if delta.notes:
        lines.append("")
        lines.append("Baseline notes:")
        lines.extend(f"  - {note}" for note in delta.notes)
    lines.append("")
    lines.append("Flags above are advice about the eval suite; they never fail the gate.")
    return lines


def _no_baseline_block(report: RunReport) -> list[str]:
    """Say so when a comparative run produced no baseline arm at all.

    Without this, `--baseline previous` in a shallow clone prints output
    indistinguishable from a non-comparative run and exits 0 -- the user is
    never told the comparison silently stopped happening.
    """
    lines = ["", "No baseline arm ran, so no delta was computed."]
    notes = format_baseline_notes(report.baseline_notes)
    if notes:
        lines.append("Baseline notes:")
        lines.extend(f"  - {note}" for note in notes)
    return lines


def _kept_workspaces(report: RunReport) -> list[str]:
    """Every directory still on disk, and which run left it there.

    Printed on every run that kept one, whether the flag or the config file
    turned keeping on. That is what makes `keep_workspace` safe to commit.
    """
    kept = [
        (outcome, outcome.result.workspace)
        for outcome in report.outcomes
        if outcome.result is not None and outcome.result.workspace is not None
    ]
    if not kept:
        return []
    lines = ["", "Kept workspaces"]
    for outcome, path in kept:
        lines.append(
            f"  {outcome.skill_name} :: {outcome.case_name} "
            f"({outcome.arm}, repeat {outcome.repeat_index})  {path}"
        )
    return lines


def _failure_lines(outcome: CaseOutcome, output_limit: int | None) -> list[str]:
    """Why a non-passing outcome did not pass, then what the agent actually did.

    The first part is the M3 renderer: each failing evaluator's detail and each
    failing check's evidence -- the evidence is the point of a judge verdict,
    since a summary line cannot tell an author whether the judge read the
    response or invented a reason. The second part is what M7 added: the
    output and the tool calls, so `did not hold` comes with the text it was
    checked against.
    """
    lines: list[str] = []
    for score in outcome.scores:
        if not score.passed:
            lines.append(f"{_INDENT}{score.evaluator}: {score.detail}")
            for check in score.checks:
                if not check.passed:
                    lines.append(f"{_DEEPER}{check.id}: {check.evidence or 'no evidence given'}")
    if outcome.result is not None and outcome.result.error:
        lines.append(f"{_INDENT}error: {outcome.result.error}")
    lines.extend(_context_lines(outcome, output_limit))
    return lines


def _context_lines(outcome: CaseOutcome, output_limit: int | None) -> list[str]:
    context = failure_context(outcome, limit=output_limit)
    if context is None:
        return []
    lines: list[str] = []
    text = context.output.rstrip("\r\n")
    if text:
        text_lines = text.split("\n")
        # Strip carriage returns from each line to handle CRLF input.
        clean_lines = [line.rstrip("\r") for line in text_lines]
        lines.append(f"{_INDENT}output: {clean_lines[0]}")
        lines.extend(f"{_INDENT}{line}" for line in clean_lines[1:])
    else:
        # "The agent said nothing" is the most useful fact about a failed
        # assertion; an absent line would look like the feature is missing.
        lines.append(f"{_INDENT}output: (empty)")
    if context.cut:
        lines.append(f"{_INDENT}{cut_note(context)}")
    if context.tool_calls:
        lines.append(f"{_INDENT}tool calls:")
        lines.extend(f"{_DEEPER}{call}" for call in context.tool_calls)
        if context.more_calls:
            lines.append(f"{_DEEPER}{more_calls_note(context)}")
    return lines


def render_console(
    report: RunReport,
    gate: GateResult | None = None,
    delta: Delta | None = None,
    output_limit: int | None = OUTPUT_LIMIT,
) -> str:
    """Render a report as plain text suitable for a terminal or CI log.

    With no `delta` this is exactly the M3 renderer: one line per outcome. A
    comparative run collapses to one line per (case, arm) instead, because
    `--repeat 5 --baseline previous` would otherwise print ten lines per case.

    `output_limit` caps the agent output shown under a non-passing case; None
    prints all of it (`--full-output`).
    """
    lines: list[str] = []
    if delta is None:
        for outcome in report.outcomes:
            mark = _MARKS[outcome.status]
            lines.append(f"[{mark}] {outcome.skill_name} :: {outcome.case_name} ({outcome.runner})")
            lines.extend(_failure_lines(outcome, output_limit))
    else:
        for case in delta.cases:
            lines.append(_case_line(case))
            failing = next(
                (
                    o
                    for o in report.candidate_outcomes
                    if (o.skill_name, o.case_name, o.runner)
                    == (case.skill_name, case.case_name, case.runner)
                    and o.status != "passed"
                ),
                None,
            )
            if failing is not None:
                lines.extend(_failure_lines(failing, output_limit))
            if case.low_signal:
                lines.append(f"        low-signal: {', '.join(case.low_signal)}")

    if report.skipped_skills:
        lines.append("")
        lines.append(f"Skipped (no eval cases): {', '.join(report.skipped_skills)}")

    if report.tag_filtered_skills:
        lines.append("")
        lines.append(
            f"Skipped (no cases matched --tag filter): {', '.join(report.tag_filtered_skills)}"
        )

    lines.append("")
    lines.append(
        f"{report.passed} passed, {report.failed} failed, "
        f"{report.errored} errored — pass rate {report.pass_rate:.0%}"
    )

    total_cost = report.total_cost_usd
    total_latency_ms = report.total_latency_ms
    pricing_degraded = report.pricing_degraded

    # Build totals line with cost and latency. `total_cost` is 0.0 both when a
    # run genuinely cost nothing and when pricing failed for every outcome --
    # `if total_cost:` alone can't tell those apart, so a degraded note is
    # surfaced explicitly rather than leaving the line silent either way.
    totals_parts = []
    if total_cost:
        totals_parts.append(f"Total cost: ${total_cost:.4f}")
        if pricing_degraded:
            totals_parts.append("some costs not priced (see per-case cost_note in the JSON report)")
    elif pricing_degraded:
        totals_parts.append("Total cost: not priced (see per-case cost_note in the JSON report)")

    judge_cost = report.judge_cost_usd
    if judge_cost:
        totals_parts.append(f"Judge overhead: ${judge_cost:.4f}")

    if total_latency_ms:
        if total_latency_ms >= 1000:
            totals_parts.append(f"Total latency: {total_latency_ms / 1000:.2f}s")
        else:
            totals_parts.append(f"Total latency: {total_latency_ms}ms")

    if totals_parts:
        lines.append(" | ".join(totals_parts))

    if delta is not None:
        lines.extend(_delta_block(delta))
    elif report.baseline_kind is not None:
        lines.extend(_no_baseline_block(report))

    lines.extend(_kept_workspaces(report))

    if gate is not None and not gate.passed:
        lines.append("")
        lines.append("Gate FAILED:")
        lines.extend(f"  - {reason}" for reason in gate.reasons)

    return "\n".join(lines)
