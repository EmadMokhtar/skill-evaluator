"""Machine-readable run report."""

from __future__ import annotations

import json

from skill_eval.gating import GateResult
from skill_eval.models import RunReport


def render_json(report: RunReport, gate: GateResult | None = None) -> str:
    """Render a report as indented JSON for CI artifacts and tooling."""
    payload: dict = {
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "errored": report.errored,
            "pass_rate": report.pass_rate,
            "pass_rate_by_skill": report.pass_rate_by_skill(),
        },
        "skipped_skills": report.skipped_skills,
        "outcomes": [
            {
                "skill_name": o.skill_name,
                "case_name": o.case_name,
                "runner": o.runner,
                "status": o.status,
                "scores": [s.model_dump() for s in o.scores],
                "output": o.result.output if o.result else "",
                "error": o.result.error if o.result else None,
                "tokens": o.result.tokens if o.result else 0,
                "latency_ms": o.result.latency_ms if o.result else 0,
                "cost_usd": o.result.cost_usd if o.result else 0.0,
            }
            for o in report.outcomes
        ],
    }
    if gate is not None:
        payload["gate"] = gate.model_dump()
    return json.dumps(payload, indent=2)
