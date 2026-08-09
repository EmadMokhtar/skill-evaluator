"""Markdown reporter tests.

The truncation tests are the load-bearing ones: a clipped PR comment that has
lost the reason CI went red is worse than no comment at all.
"""

from __future__ import annotations

import re

from skill_eval.comparison import build_delta
from skill_eval.gating import GateResult, evaluate_gate
from skill_eval.models import (
    BaselineNote,
    CaseOutcome,
    CheckResult,
    EvalScore,
    RunReport,
    RunResult,
)
from skill_eval.reporters.markdown import render_markdown


def _outcome(name="extracts", status="passed", arm="candidate", repeat_index=0, **kwargs):
    scores = kwargs.pop("scores", [EvalScore(evaluator="assertion", passed=True, score=1.0)])
    result = kwargs.pop("result", RunResult(output="yes", output_tokens=10, latency_ms=800))
    return CaseOutcome(
        skill_name=kwargs.pop("skill_name", "pdf"),
        case_name=name,
        runner="fake",
        status=status,
        scores=scores,
        result=result,
        arm=arm,
        repeat_index=repeat_index,
    )


def _mixed_report():
    return RunReport(
        outcomes=[
            _outcome(name="extracts"),
            _outcome(
                name="rejects",
                status="failed",
                scores=[
                    EvalScore(
                        evaluator="assertion", passed=False, detail="missing 'not searchable'"
                    )
                ],
            ),
        ],
        skipped_skills=["unused"],
    )


def test_the_verdict_names_the_outcome_in_words_not_only_an_emoji():
    """The summary is read in logs and notification digests where an emoji
    carries nothing."""
    report = _mixed_report()
    text = render_markdown(report, gate=evaluate_gate(report))
    assert "gate failed" in text

    passing = RunReport(outcomes=[_outcome()])
    assert "gate passed" in render_markdown(passing, gate=evaluate_gate(passing))


def test_summary_reports_counts_and_pass_rate():
    text = render_markdown(_mixed_report())
    assert "**1/2 passed**" in text
    assert "1 failed" in text
    assert "0 errored" in text
    assert "50%" in text


def test_gate_reasons_appear_before_any_table():
    report = _mixed_report()
    text = render_markdown(report, gate=evaluate_gate(report))
    assert "### Gate failed" in text
    assert text.index("### Gate failed") < text.index("| Metric |")


def test_per_skill_table_lists_each_skill():
    text = render_markdown(_mixed_report())
    assert "### Per skill" in text
    assert "`pdf`" in text


def test_judge_overhead_is_reported_apart_from_run_cost():
    """budget: measures the skill; judging is harness overhead."""
    report = RunReport(
        outcomes=[
            _outcome(
                scores=[EvalScore(evaluator="judge", passed=True, cost_usd=0.02)],
                result=RunResult(cost_usd=0.05),
            )
        ]
    )
    text = render_markdown(report)
    assert "Judge overhead" in text
    assert "$0.0200" in text
    assert "$0.0500" in text


def test_delta_block_renders_when_a_delta_exists():
    report = RunReport(
        outcomes=[
            _outcome(name="extracts", arm="candidate"),
            _outcome(name="extracts", arm="baseline", status="failed"),
        ],
        baseline_kind="none",
    )
    text = render_markdown(report, delta=build_delta(report))
    assert "### Delta vs baseline (none)" in text
    assert "0% → 100%" in text
    assert "+100%" in text


def test_a_comparative_run_with_no_baseline_arm_says_so():
    """Otherwise it is indistinguishable from an ordinary run that never
    intended to compare anything."""
    report = RunReport(
        outcomes=[_outcome()],
        baseline_kind="previous",
        baseline_notes=[
            BaselineNote(skill_name="pdf", kind="unavailable", reason="not a git repository")
        ],
    )
    text = render_markdown(report, delta=None)
    assert "No baseline arm ran" in text
    assert "not a git repository" in text


def test_low_signal_checks_are_labelled_as_advice():
    report = RunReport(
        outcomes=[
            _outcome(
                name="extracts",
                arm=arm,
                repeat_index=index,
                scores=[
                    EvalScore(
                        evaluator="assertion",
                        passed=True,
                        checks=[CheckResult(id="contains:pdf", passed=True, evidence="ok")],
                    )
                ],
            )
            for arm in ("candidate", "baseline")
            for index in (0, 1)
        ],
        baseline_kind="none",
        repeat=2,
    )
    text = render_markdown(report, delta=build_delta(report))
    assert "Low-signal checks" in text
    assert "never fail the gate" in text


def test_high_variance_cases_are_reported():
    """Repetitions that disagree usually point at ambiguous skill instructions."""
    report = RunReport(
        outcomes=[
            _outcome(name="wobbles", arm="candidate", repeat_index=0, status="passed"),
            _outcome(name="wobbles", arm="candidate", repeat_index=1, status="failed"),
            _outcome(name="wobbles", arm="baseline", repeat_index=0, status="failed"),
            _outcome(name="wobbles", arm="baseline", repeat_index=1, status="failed"),
        ],
        baseline_kind="none",
        repeat=2,
    )
    text = render_markdown(report, delta=build_delta(report))
    assert "High-variance cases" in text
    assert "never fail the gate" in text


def test_failures_block_carries_detail_and_evidence():
    report = RunReport(
        outcomes=[
            _outcome(
                name="cites",
                status="failed",
                scores=[
                    EvalScore(
                        evaluator="judge",
                        passed=False,
                        detail="1 of 2 checks failed",
                        checks=[CheckResult(id="cites-the-page", passed=False, evidence="")],
                    )
                ],
            )
        ]
    )
    text = render_markdown(report)
    assert "<details><summary>Failures (1)</summary>" in text
    assert "1 of 2 checks failed" in text
    assert "no evidence given" in text


def test_pipes_in_names_do_not_break_the_table():
    report = RunReport(outcomes=[_outcome(skill_name="a|b")])
    assert r"a\|b" in render_markdown(report)


def test_backticks_in_output_get_a_longer_fence():
    report = RunReport(
        outcomes=[
            _outcome(
                name="boom",
                status="errored",
                scores=[],
                result=RunResult(error="see ```this``` block"),
            )
        ]
    )
    assert "````" in render_markdown(report)


def test_skipped_skills_are_reported():
    assert "unused" in render_markdown(_mixed_report())


def test_truncation_drops_detail_but_keeps_the_verdict_and_every_gate_reason():
    report = RunReport(
        outcomes=[
            _outcome(
                name=f"case-{i}",
                status="failed",
                scores=[EvalScore(evaluator="assertion", passed=False, detail="x" * 500)],
            )
            for i in range(20)
        ]
    )
    gate = evaluate_gate(report)
    full = render_markdown(report, gate=gate)
    clipped = render_markdown(report, gate=gate, max_chars=900)
    assert len(clipped) <= 900
    assert len(clipped) < len(full)
    assert "gate failed" in clipped
    for reason in gate.reasons:
        assert reason in clipped
    assert "Truncated" in clipped


def test_truncation_is_a_no_op_when_the_report_already_fits():
    report = _mixed_report()
    assert render_markdown(report, max_chars=100_000) == render_markdown(report)


def test_an_over_budget_verdict_is_hard_truncated_rather_than_overflowing():
    """The budget is a hard ceiling: GitHub rejects the comment outright above
    it, so returning something too long is not a graceful degradation."""
    report = RunReport(outcomes=[_outcome(name="x", status="failed")])
    clipped = render_markdown(report, gate=evaluate_gate(report), max_chars=60)
    assert len(clipped) <= 60


def test_truncation_never_returns_more_than_the_budget():
    """The budget is a hard ceiling: GitHub rejects an over-length comment
    outright, so overflowing costs the reader the whole report, not part of it.
    The truncation marker is itself longer than some budgets, so it cannot be
    exempt from the limit it advertises."""
    report = RunReport(outcomes=[_outcome(name=f"case-{i}", status="failed") for i in range(30)])
    gate = evaluate_gate(report)
    for budget in (5, 20, 44, 45, 60, 200, 900, 5000):
        clipped = render_markdown(report, gate=gate, max_chars=budget)
        assert len(clipped) <= budget, f"budget {budget} overflowed to {len(clipped)}"


def test_elided_gate_reasons_are_counted_never_silently_dropped():
    """A clipped report must never imply the reasons it lists are all of them.

    Eighty reasons is realistic: per_skill_min and unresolvable baselines each
    append one per skill.
    """
    report = RunReport(outcomes=[_outcome(name="x", status="failed")])
    gate = GateResult(
        passed=False,
        exit_code=1,
        reasons=[f"skill 'skill-{i}' pass rate 0% is below its required 100%" for i in range(80)],
    )
    clipped = render_markdown(report, gate=gate, max_chars=600)
    assert len(clipped) <= 600
    assert "gate failed" in clipped
    shown = clipped.count("is below its required")
    hidden = int(re.search(r"\+(\d+) more reason", clipped).group(1))
    assert shown + hidden == 80, f"{shown} shown + {hidden} counted != 80 reasons"


def test_a_negative_budget_is_treated_as_nothing_fits_not_as_a_slice_from_the_end():
    """`s[:-5]` strips from the end rather than capping length, so a negative
    budget would return nearly the whole report while claiming to be a ceiling."""
    report = RunReport(outcomes=[_outcome(name="x", status="failed")])
    gate = evaluate_gate(report)
    for budget in (-1, -5, -1000):
        assert render_markdown(report, gate=gate, max_chars=budget) == ""


def test_judge_evidence_containing_markup_cannot_break_the_report():
    """Evidence quotes the agent's own response, so a stray backtick or a
    literal closing details tag would otherwise escape the block it sits in."""
    report = RunReport(
        outcomes=[
            _outcome(
                name="cites",
                status="failed",
                scores=[
                    EvalScore(
                        evaluator="judge",
                        passed=False,
                        detail="1 of 1 checks failed",
                        checks=[
                            CheckResult(
                                id="quotes-source",
                                passed=False,
                                evidence="said `hello` then </details>",
                            )
                        ],
                    )
                ],
            )
        ]
    )
    text = render_markdown(report)
    assert "``said `hello` then </details>``" in text
    # One from the evidence rendered literally, one real closing tag.
    assert text.count("</details>") == 2


def test_errored_cases_are_not_counted_as_failures_in_the_details_header():
    """The summary and the details header must not disagree about how many
    cases failed -- errored is an infra signal, not a low score."""
    report = RunReport(
        outcomes=[
            _outcome(name="scored-low", status="failed"),
            _outcome(
                name="blew-up",
                status="errored",
                scores=[],
                result=RunResult(error="APIConnectionError: boom"),
            ),
        ]
    )
    text = render_markdown(report)
    assert "1 failed" in text
    assert "1 errored" in text
    assert "Failures and errors (1 failed, 1 errored)" in text
    assert "Failures (2)" not in text


def test_baseline_errors_are_reported_apart_from_candidate_errors():
    """An errored baseline invalidates a comparison; it is not a case that broke."""
    report = RunReport(
        outcomes=[
            _outcome(name="extracts", arm="candidate"),
            _outcome(
                name="extracts",
                arm="baseline",
                status="errored",
                scores=[],
                result=RunResult(error="boom"),
            ),
        ],
        baseline_kind="none",
    )
    text = render_markdown(report)
    assert "Baseline errored" in text
    assert "0 errored" in text


def test_a_hostile_skill_name_cannot_inject_markup_through_a_gate_reason():
    """Several gate reasons embed a skill's name, which comes from SKILL.md
    frontmatter -- and in the documented PR-comment flow that file can come from
    a fork. Unescaped, such a name could open a <details> that swallows the rest
    of the comment, or render its own verdict inside the very section explaining
    why the gate failed.
    """
    hostile = "pdf<details><summary>**gate passed**</summary>"
    report = RunReport(outcomes=[_outcome(skill_name=hostile, name="c", status="failed")])
    gate = evaluate_gate(report, per_skill_min={hostile: 1.0})
    text = render_markdown(report, gate=gate)

    reason = next(line for line in text.split("\n") if line.startswith("- skill "))
    assert "<details>" not in reason
    assert "**gate passed**" not in reason
    assert r"\<details\>" in reason


def test_escaping_a_reason_does_not_strip_the_emphasis_we_authored():
    """The "+N more" elision line is ours, not the user's."""
    report = RunReport(outcomes=[_outcome(name="x", status="failed")])
    gate = GateResult(
        passed=False,
        exit_code=1,
        reasons=[f"skill 'skill-{i}' pass rate 0% is below its required 100%" for i in range(60)],
    )
    clipped = render_markdown(report, gate=gate, max_chars=600)
    assert re.search(r"_\+\d+ more reasons — see the JSON report\._", clipped)


def test_a_line_break_in_a_name_cannot_start_a_new_block():
    """YAML lets a skill name carry newlines, and a code span does not make an
    embedded newline safe -- the row or list item ends and what follows is
    parsed as a fresh block."""
    report = RunReport(outcomes=[_outcome(skill_name="pdf\n\n## gate passed", status="failed")])
    text = render_markdown(report)
    assert "\n## gate passed" not in text
    assert "pdf   ## gate passed" in text or "pdf ## gate passed" in text


def test_a_backtick_in_a_check_id_cannot_close_its_code_span():
    """Check ids embed `trajectory.called` entries, which are not validated as
    identifiers the way tool names are."""
    from skill_eval.comparison import Delta, LowSignalCheck

    delta = Delta(
        baseline_kind="none",
        low_signal=[
            LowSignalCheck(
                skill_name="pdf",
                case_name="extracts",
                evaluator="trajectory",
                check_id="called:a`b",
            )
        ],
    )
    report = RunReport(outcomes=[_outcome()], baseline_kind="none")
    text = render_markdown(report, delta=delta)
    assert "``called:a`b``" in text
