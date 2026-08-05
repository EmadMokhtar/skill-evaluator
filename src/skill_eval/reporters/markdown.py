"""GitHub-flavored Markdown — the human-facing CI surface.

Rendering only. The workflow decides where this goes (a step summary, a PR
comment); putting a GitHub API client in a reporter would move network failure
and token scopes inside a pure function.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from skill_eval.comparison import Delta, format_baseline_notes
from skill_eval.gating import GateResult
from skill_eval.models import CaseOutcome, RunReport

_TRUNCATION_NOTE = "_Truncated — see the JSON report artifact._"
_ADVISORY = "These flags are advice about the eval suite; they never fail the gate."


class _Block(NamedTuple):
    """One section, and whether it may be dropped to fit a size budget.

    Essential means a reader who sees only this still learns the verdict and
    why. Everything else is detail, and detail is what gets sacrificed.
    """

    text: str
    essential: bool


def _cell(text: str) -> str:
    """A table cell: pipes escaped, wrapped in inline code.

    Content containing a backtick needs the double-backtick delimiter form,
    which is the only way to put one inside inline code.
    """
    escaped = text.replace("|", r"\|")
    return f"`` {escaped} ``" if "`" in escaped else f"`{escaped}`"


def _fenced(text: str) -> str:
    """A fenced block whose fence outlives any run of backticks inside it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _verdict(gate: GateResult | None) -> str:
    if gate is None:
        return "## skill-eval"
    return "## skill-eval — ✅ gate passed" if gate.passed else "## skill-eval — ❌ gate failed"


def _summary(report: RunReport) -> str:
    return (
        f"**{report.passed}/{report.total} passed** · {report.failed} failed · "
        f"{report.errored} errored · pass rate {report.pass_rate:.0%}"
    )


def _gate_block(gate: GateResult) -> str:
    return "### Gate failed\n" + "\n".join(f"- {reason}" for reason in gate.reasons)


def _totals(report: RunReport) -> str:
    # Both arms: money spent is money spent. `RunReport.total_tokens` /
    # `total_cost_usd` / `total_latency_ms` / `pricing_degraded` live on the
    # model so the console and JSON reporters compute the same numbers once.
    tokens = report.total_tokens
    cost = report.total_cost_usd
    latency = report.total_latency_ms
    # 0.0 means both "free" and "pricing failed everywhere"; the note is the
    # only thing that tells them apart.
    degraded = report.pricing_degraded

    rows = [["Tokens", f"{tokens:,}"]]
    if not cost and degraded:
        rows.append(["Cost", "not priced — see the JSON report"])
    else:
        rows.append(["Cost", f"${cost:.4f}"])
        if degraded:
            rows.append(["Cost note", "some costs not priced — see the JSON report"])
    if report.judge_cost_usd:
        # Judging is harness overhead and is never charged to the skill.
        rows.append(["Judge overhead", f"${report.judge_cost_usd:.4f}"])
    rows.append(["Latency", f"{latency / 1000:.2f}s" if latency >= 1000 else f"{latency}ms"])
    if report.baseline_errored:
        rows.append(["Baseline errored", str(report.baseline_errored)])
    return _table(["Metric", "Value"], rows)


def _per_skill(report: RunReport) -> str:
    rates = report.pass_rate_by_skill()
    if not rates:
        return ""
    counts: dict[str, dict[str, int]] = {}
    for outcome in report.candidate_outcomes:
        bucket = counts.setdefault(outcome.skill_name, {"passed": 0, "failed": 0, "errored": 0})
        bucket[outcome.status] += 1
    rows = [
        [
            _cell(name),
            f"{rate:.0%}",
            str(counts[name]["passed"]),
            str(counts[name]["failed"]),
            str(counts[name]["errored"]),
        ]
        for name, rate in rates.items()
    ]
    return "### Per skill\n" + _table(["Skill", "Pass rate", "Passed", "Failed", "Errored"], rows)


def _delta_block(delta: Delta) -> str:
    head = (
        f"### Delta vs baseline ({delta.baseline_kind})\n"
        f"Pass rate **{delta.pass_rate_baseline:.0%} → {delta.pass_rate_candidate:.0%}** "
        f"(**{delta.pass_rate_delta:+.0%}**, higher is better)\n"
    )
    table = _table(
        ["Metric (mean, per case)", "Delta", "Better when"],
        [
            ["Tokens", f"{delta.tokens_delta:+.0f}", "negative"],
            ["Cost", f"${delta.cost_usd_delta:+.4f}", "negative"],
            ["Latency", f"{delta.latency_ms_delta:+.0f}ms", "negative"],
        ],
    )
    return head + "\n" + table


def _no_baseline_block(report: RunReport) -> str:
    """A comparative run whose baseline never materialised must not read like
    an ordinary one."""
    lines = ["### No baseline arm ran", "", "No delta was computed."]
    notes = format_baseline_notes(report.baseline_notes)
    if notes:
        lines.append("")
        lines.extend(f"- {note}" for note in notes)
    return "\n".join(lines)


def _details(summary: str, body_lines: list[str]) -> str:
    return "\n".join([f"<details><summary>{summary}</summary>", "", *body_lines, "", "</details>"])


def _failure_lines(outcome: CaseOutcome) -> list[str]:
    lines = [
        f"**{_cell(outcome.skill_name)} :: {_cell(outcome.case_name)}** "
        f"({outcome.runner}) — {outcome.status}"
    ]
    for score in outcome.scores:
        if score.passed:
            continue
        lines.append(f"- `{score.evaluator}`: {score.detail}")
        for check in score.checks:
            if not check.passed:
                lines.append(f"    - `{check.id}`: {check.evidence or 'no evidence given'}")
    if outcome.result is not None and outcome.result.error:
        lines.extend(["", _fenced(outcome.result.error)])
    lines.append("")
    return lines


def _failures(report: RunReport) -> str:
    failing = [o for o in report.candidate_outcomes if o.status != "passed"]
    if not failing:
        return ""
    body: list[str] = []
    for outcome in failing:
        body.extend(_failure_lines(outcome))
    return _details(f"Failures ({len(failing)})", body)


def _low_signal(delta: Delta) -> str:
    if not delta.low_signal:
        return ""
    body = [
        "Passed with *and* without the skill — they inflate the score while measuring nothing.",
        "",
    ]
    body.extend(
        f"- {_cell(c.skill_name)} :: {_cell(c.case_name)}: `{c.check_id}`" for c in delta.low_signal
    )
    body.extend(["", _ADVISORY])
    return _details(f"Low-signal checks ({len(delta.low_signal)})", body)


def _high_variance(delta: Delta) -> str:
    if not delta.high_variance:
        return ""
    body = ["Repetitions disagreed — often a sign of ambiguous skill instructions.", ""]
    body.extend(
        f"- {_cell(r.skill_name)} :: {_cell(r.case_name)} ({r.arm}): "
        f"{r.pass_rate:.0%}, stddev {r.stddev:.2f}"
        for r in delta.high_variance
    )
    body.extend(["", _ADVISORY])
    return _details(f"High-variance cases ({len(delta.high_variance)})", body)


def _skipped(report: RunReport) -> str:
    bits = []
    if report.skipped_skills:
        bits.append(f"Skipped (no eval cases): {', '.join(report.skipped_skills)}")
    if report.tag_filtered_skills:
        bits.append(f"Skipped (no cases matched --tag): {', '.join(report.tag_filtered_skills)}")
    return "<sub>" + "<br>".join(bits) + "</sub>" if bits else ""


def _join(blocks: list[_Block]) -> str:
    return "\n\n".join(block.text for block in blocks if block.text)


def _fit(blocks: list[_Block], max_chars: int | None) -> str:
    """Trim to a size budget by dropping whole optional blocks, last first.

    Structural, not a string slice: only this module knows where a <details>
    block ends, so a caller slicing the result would cut one open. The budget
    is a hard ceiling -- GitHub rejects an over-length comment outright -- so
    the essential blocks are hard-truncated as a last resort rather than
    allowed to overflow.
    """
    text = _join(blocks)
    if max_chars is None or len(text) <= max_chars:
        return text

    kept = list(blocks)
    while any(not block.essential for block in kept):
        for index in range(len(kept) - 1, -1, -1):
            if not kept[index].essential:
                del kept[index]
                break
        text = _join(kept) + "\n\n" + _TRUNCATION_NOTE
        if len(text) <= max_chars:
            return text

    note = "\n\n" + _TRUNCATION_NOTE
    room = max(0, max_chars - len(note))
    return _join(kept)[:room] + note


def render_markdown(
    report: RunReport,
    gate: GateResult | None = None,
    delta: Delta | None = None,
    max_chars: int | None = None,
) -> str:
    """Render a report as Markdown, optionally trimmed to `max_chars`.

    `max_chars` is left to the caller because 65,536 is a fact about GitHub
    comments, not about skill-eval; a step summary allows 1 MiB and should not
    be trimmed at all.
    """
    blocks = [_Block(_verdict(gate), True), _Block(_summary(report), True)]
    if gate is not None and not gate.passed:
        blocks.append(_Block(_gate_block(gate), True))
    blocks.append(_Block(_totals(report), False))
    blocks.append(_Block(_per_skill(report), False))
    if delta is not None:
        blocks.append(_Block(_delta_block(delta), False))
    elif report.baseline_kind is not None:
        blocks.append(_Block(_no_baseline_block(report), False))
    blocks.append(_Block(_failures(report), False))
    if delta is not None:
        blocks.append(_Block(_low_signal(delta), False))
        blocks.append(_Block(_high_variance(delta), False))
    blocks.append(_Block(_skipped(report), False))
    return _fit(blocks, max_chars)
