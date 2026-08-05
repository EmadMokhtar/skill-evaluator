"""Machine-readable run report."""

from __future__ import annotations

import json

from skill_eval.comparison import Delta
from skill_eval.gating import GateResult
from skill_eval.models import RunReport


def render_json(
    report: RunReport, gate: GateResult | None = None, delta: Delta | None = None
) -> str:
    """Render a report as indented JSON for CI artifacts and tooling.

    Token, cost and latency totals sum **both** arms: money spent is money
    spent. The pass/fail counts in `summary` are the candidate arm's, because
    those are what the gate reads.
    """
    total_tokens = report.total_tokens
    total_cost_usd = report.total_cost_usd
    total_latency_ms = report.total_latency_ms

    payload: dict = {
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "errored": report.errored,
            "baseline_errored": report.baseline_errored,
            "pass_rate": report.pass_rate,
            "pass_rate_by_skill": report.pass_rate_by_skill(),
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost_usd,
            "total_latency_ms": total_latency_ms,
            # Kept apart from total_cost_usd: judging is harness overhead and is
            # never charged to the skill's budget.
            "judge_cost_usd": report.judge_cost_usd,
        },
        "skipped_skills": report.skipped_skills,
        "tag_filtered_skills": report.tag_filtered_skills,
        "outcomes": [
            {
                "skill_name": o.skill_name,
                "case_name": o.case_name,
                "runner": o.runner,
                "status": o.status,
                "arm": o.arm,
                "repeat_index": o.repeat_index,
                "scores": [s.model_dump() for s in o.scores],
                "output": o.result.output if o.result else "",
                "error": o.result.error if o.result else None,
                "tokens": o.result.tokens if o.result else 0,
                "latency_ms": o.result.latency_ms if o.result else 0,
                "cost_usd": o.result.cost_usd if o.result else 0.0,
                "model": o.result.model if o.result else "",
                "cost_note": o.result.cost_note if o.result else "",
            }
            for o in report.outcomes
        ],
    }
    payload["delta"] = delta.model_dump() if delta is not None else None
    payload["baseline_notes"] = [note.model_dump() for note in report.baseline_notes]
    if gate is not None:
        payload["gate"] = gate.model_dump()
    return json.dumps(payload, indent=2)
