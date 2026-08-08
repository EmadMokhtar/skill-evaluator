# skill-eval M5 Part 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the CI-facing half of M5 — JUnit XML and Markdown reporters, bounded orchestrator concurrency, and a composite GitHub Action with example workflows — so a skill-eval run lands natively in CI test panes and pull requests.

**Architecture:** Two new pure-function reporters beside the existing console/JSON pair, no IO and no service calls. The orchestrator splits into a sequential planning phase (discovery, git baseline resolution) and an execution phase that fans work items out across a `concurrent.futures.Executor`, reading futures in submission order so results never reorder. A composite `action.yml` wraps the CLI and is kept in step with it by a drift test plus an end-to-end CI smoke job.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, `xml.etree.ElementTree`, `concurrent.futures`, pytest, ruff, uv, GitHub Actions, MkDocs Material.

**Spec:** `docs/superpowers/specs/2026-08-05-skill-eval-m5-design.md`. Section references below (§N) point at it.

## Global Constraints

- **Reporters are pure functions.** `render_*(report, gate=None, delta=None) -> str`. No file IO, no network, no GitHub API. The CLI writes files; the workflow posts comments. (§2.1)
- **`errored` ≠ `failed`.** `failed` = ran and scored below bar. `errored` = infra blew up. Every new surface must preserve the distinction.
- **Exit codes are the CI contract:** gate pass `0`, gate fail `1`, user/authoring error `2`. A report-write failure escalates to `2` **only when the gate itself passed**.
- **Authoring errors abort the run and never score as failures.** They propagate out of `run_evals` for `cli.py` to catch via `_AUTHORING_ERRORS`.
- **Every `RunReport` aggregate reads the candidate arm.** Baseline outcomes never reach a gate aggregate, and never become JUnit test cases.
- **All file IO pins `encoding="utf-8"`.**
- **`skill_eval` (underscore) never appears in user-facing output.** The user-facing name is `skill-eval` everywhere.
- **`extra="forbid"`** stays on `Config`.
- **YAML is read through `skill_eval.yaml_loading.safe_load`**, never `yaml.safe_load`.
- **The pipeline test tier is offline, deterministic and free.** Every test in this plan passes with no network and no API key.
- **Conventional Commits are enforced** by a `commit-msg` hook (`cz check`). Every commit message below is already conventional — use it verbatim.
- **Line length is 100** (`ruff`, `line-length = 100`). Lint select is `E, F, I, UP, B`.
- **Docs ship with the change.** CI has `docs` and `docs-freshness` jobs and `tests/test_docs.py`.

---

## File Structure

**Create:**

| Path | Responsibility |
| --- | --- |
| `src/skill_eval/reporters/junit.py` | Render a `RunReport` as JUnit XML. |
| `src/skill_eval/reporters/markdown.py` | Render a `RunReport` as GitHub-flavored Markdown, with structural truncation. |
| `tests/test_junit_reporter.py` | JUnit reporter tests. |
| `tests/test_markdown_reporter.py` | Markdown reporter tests. |
| `tests/test_action.py` | Drift guard between `action.yml` inputs and `run` flags. |
| `tests/fixtures/ci-smoke/SKILL.md` | Fixture skill for the action smoke job. |
| `tests/fixtures/ci-smoke/evals/ci-smoke.eval.yaml` | Its eval case, passing under `FakeRunner`. |
| `action.yml` | The composite action. |
| `examples/ci/skill-eval.yml` | Example workflow using the action + guarded PR comment. |
| `examples/ci/skill-eval-cli.yml` | Example workflow using the raw CLI. |
| `docs/ci.md` | CI integration page. |

**Modify:**

| Path | Change |
| --- | --- |
| `src/skill_eval/orchestrator.py` | Split into planning + execution; add `concurrency` and `executor_factory`. |
| `src/skill_eval/config.py` | Add `concurrency`. |
| `src/skill_eval/cli.py` | Add four flags; factor the report-writing loop. |
| `.github/workflows/ci.yml` | Add the `action-smoke` job. |
| `mkdocs.yml` | Add `docs/ci.md` to `nav`. |
| `docs/cli.md`, `docs/configuration.md`, `docs/gating.md`, `docs/roadmap.md` | Document the new surface. |
| `ARCHITECTURE.md`, `CLAUDE.md` | Module map, invariants, milestone status. |

---

## Task 1: JUnit XML reporter

**Files:**
- Create: `src/skill_eval/reporters/junit.py`
- Test: `tests/test_junit_reporter.py`

**Interfaces:**
- Consumes: `RunReport`, `CaseOutcome`, `GateResult`, `Delta` (all existing).
- Produces: `render_junit(report: RunReport, gate: GateResult | None = None, delta: Delta | None = None) -> str` — a complete XML document beginning with an XML declaration.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_junit_reporter.py`:

```python
"""JUnit XML reporter tests.

Every assertion parses the rendered document rather than matching substrings:
the whole point of this reporter is that a CI system can read it, so a test
that only greps the text would pass on output no parser accepts.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from skill_eval.gating import evaluate_gate
from skill_eval.models import CaseOutcome, CheckResult, EvalScore, RunReport, RunResult
from skill_eval.reporters.junit import render_junit


def _outcome(name="extracts", status="passed", arm="candidate", repeat_index=0, **kwargs):
    scores = kwargs.pop("scores", [EvalScore(evaluator="assertion", passed=True, score=1.0)])
    result = kwargs.pop("result", RunResult(output="yes", latency_ms=800))
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


def _parse(report, **kwargs):
    return ET.fromstring(render_junit(report, **kwargs))


def test_renders_one_suite_per_skill_and_one_case_per_outcome():
    report = RunReport(
        outcomes=[
            _outcome(name="extracts"),
            _outcome(name="rejects"),
            _outcome(name="sums", skill_name="xlsx"),
        ]
    )
    root = _parse(report)
    suites = root.findall("testsuite")
    assert [s.get("name") for s in suites] == ["pdf", "xlsx"]
    assert [c.get("name") for c in suites[0].findall("testcase")] == ["extracts", "rejects"]
    assert root.get("tests") == "3"


def test_a_failed_case_is_a_failure_and_an_errored_case_is_an_error():
    """The project's central distinction, rendered natively by CI.

    A runner that blew up must not appear as a skill that got worse.
    """
    report = RunReport(
        outcomes=[
            _outcome(
                name="rejects",
                status="failed",
                scores=[
                    EvalScore(
                        evaluator="assertion",
                        passed=False,
                        detail="expected output to contain 'not searchable'",
                    )
                ],
            ),
            _outcome(
                name="explodes",
                status="errored",
                scores=[],
                result=RunResult(error="APIConnectionError: boom"),
            ),
        ]
    )
    root = _parse(report)
    cases = root.findall("testsuite/testcase")
    assert cases[0].find("failure") is not None
    assert cases[0].find("error") is None
    assert "not searchable" in cases[0].find("failure").text
    assert cases[1].find("error") is not None
    assert cases[1].find("failure") is None
    assert "APIConnectionError: boom" in cases[1].find("error").text
    assert root.get("failures") == "1"
    assert root.get("errors") == "1"


def test_a_failing_check_without_evidence_says_so():
    """An unsupported pass is the judge's characteristic failure mode, so the
    report must never render a check as if it justified itself."""
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
    text = _parse(report).find("testsuite/testcase/failure").text
    assert "judge/cites-the-page: no evidence given" in text


def test_only_the_candidate_arm_becomes_test_cases():
    """A baseline failure is evidence the skill helped, not a red build."""
    report = RunReport(
        outcomes=[
            _outcome(name="extracts", arm="candidate"),
            _outcome(name="extracts", arm="baseline", status="failed"),
        ],
        baseline_kind="none",
    )
    root = _parse(report)
    assert root.get("tests") == "1"
    assert root.get("failures") == "0"


def test_repetitions_get_unique_names_only_when_repeating():
    """JUnit consumers key on classname+name and silently collapse duplicates."""
    repeated = RunReport(
        outcomes=[
            _outcome(name="extracts", repeat_index=0),
            _outcome(name="extracts", repeat_index=1),
        ],
        repeat=2,
    )
    names = [c.get("name") for c in _parse(repeated).findall("testsuite/testcase")]
    assert names == ["extracts [run 1/2]", "extracts [run 2/2]"]

    single = RunReport(outcomes=[_outcome(name="extracts")], repeat=1)
    assert _parse(single).find("testsuite/testcase").get("name") == "extracts"


def test_skills_with_no_cases_become_skipped_suites():
    report = RunReport(
        outcomes=[_outcome()],
        skipped_skills=["unused"],
        tag_filtered_skills=["filtered"],
    )
    root = _parse(report)
    skipped = root.findall("testsuite/testcase/skipped")
    assert len(skipped) == 2
    assert root.get("skipped") == "2"
    assert root.get("failures") == "0"
    assert root.get("errors") == "0"


def test_a_run_with_no_cases_is_an_error_not_an_empty_green_suite():
    """tests="0" renders green in most CI UIs, contradicting exit code 1."""
    report = RunReport(outcomes=[])
    gate = evaluate_gate(report)
    root = _parse(report, gate=gate)
    assert root.get("tests") == "1"
    assert root.get("errors") == "1"
    error = root.find("testsuite/testcase/error")
    assert "no eval cases ran" in error.text


def test_illegal_xml_characters_are_stripped_so_the_document_parses():
    """ElementTree escapes &, < and > but emits control characters raw, which
    produces a file every real parser rejects."""
    report = RunReport(
        outcomes=[
            _outcome(
                name="weird\x00name",
                status="errored",
                scores=[],
                result=RunResult(error="boom\x08\x1f"),
            )
        ]
    )
    root = _parse(report)  # would raise ParseError before the strip
    assert root.find("testsuite/testcase").get("name") == "weirdname"
    assert root.find("testsuite/testcase/error").text == "boom"


def test_markup_in_names_survives_a_round_trip():
    report = RunReport(outcomes=[_outcome(name='a <b> & "c"', skill_name="x&y")])
    case = _parse(report).find("testsuite/testcase")
    assert case.get("name") == 'a <b> & "c"'
    assert case.get("classname") == "x&y"


def test_time_is_reported_in_seconds():
    report = RunReport(outcomes=[_outcome(result=RunResult(latency_ms=1500))])
    root = _parse(report)
    assert root.find("testsuite/testcase").get("time") == "1.500"
    assert root.get("time") == "1.500"


def test_the_document_starts_with_an_xml_declaration():
    assert render_junit(RunReport(outcomes=[_outcome()])).startswith('<?xml version="1.0"')
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_junit_reporter.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'skill_eval.reporters.junit'`.

- [ ] **Step 3: Write the reporter**

Create `src/skill_eval/reporters/junit.py`:

```python
"""JUnit XML — the report format every CI system already knows how to render.

The mapping that matters is `failed` -> <failure> and `errored` -> <error>:
this project's central distinction finally gets a native rendering, so an
exploded runner stops looking like a skill that got worse in the CI UI.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element, SubElement, tostring

from skill_eval.comparison import Delta
from skill_eval.gating import GateResult
from skill_eval.models import CaseOutcome, RunReport

# XML 1.0 forbids most control characters outright -- a document containing one
# is not "badly escaped", it is not XML. ElementTree escapes &, < and > but
# passes these through raw, so a model emitting \x00 would produce a file every
# parser rejects. Stripped before anything reaches the tree.
# The legal set is tab, newline, carriage return, and the printable ranges,
# written with explicit escapes so the pattern survives copy-paste.
_ILLEGAL_XML = re.compile(
    "[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)

# Some CI UIs render `message` in a fixed-width column; the full text is in the
# element body either way.
_MESSAGE_LIMIT = 1000


def _xml_safe(text: str) -> str:
    return _ILLEGAL_XML.sub("", text)


def _message(body: str) -> str:
    """The attribute form of a body: its first line, capped."""
    first = body.splitlines()[0] if body else ""
    return _xml_safe(first[:_MESSAGE_LIMIT])


def _failure_body(outcome: CaseOutcome) -> str:
    """Why this case failed: each failing evaluator, then each failing check.

    `no evidence given` matches the console reporter deliberately -- a check
    that passed on nothing is the judge's characteristic failure mode, and a
    report that hid it would be worse than one that never showed checks at all.
    """
    lines: list[str] = []
    for score in outcome.scores:
        if score.passed:
            continue
        lines.append(f"{score.evaluator}: {score.detail}")
        for check in score.checks:
            if not check.passed:
                lines.append(
                    f"{score.evaluator}/{check.id}: {check.evidence or 'no evidence given'}"
                )
    return "\n".join(lines) or "no failing evaluator reported a detail"


def _case_name(outcome: CaseOutcome, repeat: int) -> str:
    """Repetitions need distinct names: consumers key on classname+name and
    silently collapse duplicates. No suffix at repeat == 1 keeps the ordinary
    case clean."""
    if repeat <= 1:
        return outcome.case_name
    return f"{outcome.case_name} [run {outcome.repeat_index + 1}/{repeat}]"


def _by_skill(outcomes: list[CaseOutcome]) -> dict[str, list[CaseOutcome]]:
    groups: dict[str, list[CaseOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault(outcome.skill_name, []).append(outcome)
    return groups


def _skipped_suite(root: Element, skill_name: str, case_name: str, reason: str) -> None:
    suite = SubElement(
        root,
        "testsuite",
        name=_xml_safe(skill_name),
        tests="1",
        failures="0",
        errors="0",
        skipped="1",
        time="0.000",
    )
    case = SubElement(
        suite,
        "testcase",
        classname=_xml_safe(skill_name),
        name=case_name,
        time="0.000",
    )
    SubElement(case, "skipped", message=reason)


def render_junit(
    report: RunReport, gate: GateResult | None = None, delta: Delta | None = None
) -> str:
    """Render a report as JUnit XML.

    Only the **candidate** arm becomes test cases: a baseline failure is the
    evidence that the skill helped, and emitting it as <failure> would paint CI
    red for the skill working. `delta` is accepted for signature symmetry with
    the other reporters and is unused -- JUnit has no vocabulary for "this case
    improved"; that lives in the JSON and Markdown reports.
    """
    root = Element("testsuites", name="skill-eval")
    tests = failures = errors = skipped = 0
    total_time = 0.0

    for skill_name, outcomes in _by_skill(report.candidate_outcomes).items():
        suite = SubElement(root, "testsuite", name=_xml_safe(skill_name))
        suite_failures = suite_errors = 0
        suite_time = 0.0
        for outcome in outcomes:
            seconds = outcome.result.latency_ms / 1000 if outcome.result else 0.0
            suite_time += seconds
            case = SubElement(
                suite,
                "testcase",
                classname=_xml_safe(skill_name),
                name=_xml_safe(_case_name(outcome, report.repeat)),
                time=f"{seconds:.3f}",
            )
            if outcome.status == "failed":
                suite_failures += 1
                body = _failure_body(outcome)
                SubElement(case, "failure", message=_message(body)).text = _xml_safe(body)
            elif outcome.status == "errored":
                suite_errors += 1
                body = (
                    outcome.result.error
                    if outcome.result is not None and outcome.result.error
                    else "the runner reported no detail"
                )
                SubElement(case, "error", message=_message(body)).text = _xml_safe(body)
        suite.set("tests", str(len(outcomes)))
        suite.set("failures", str(suite_failures))
        suite.set("errors", str(suite_errors))
        suite.set("skipped", "0")
        suite.set("time", f"{suite_time:.3f}")
        tests += len(outcomes)
        failures += suite_failures
        errors += suite_errors
        total_time += suite_time

    # A skill with no coverage is exactly what JUnit's <skipped> is for, and it
    # surfaces "nobody is testing this" in every CI UI.
    for skill_name in report.skipped_skills:
        _skipped_suite(root, skill_name, "(no eval cases)", "no eval cases")
        tests += 1
        skipped += 1
    for skill_name in report.tag_filtered_skills:
        _skipped_suite(
            root, skill_name, "(no cases matched --tag)", "no cases matched the --tag filter"
        )
        tests += 1
        skipped += 1

    if report.total == 0:
        # tests="0" renders green in most CI UIs, which would directly
        # contradict exit code 1. "Nothing ran is a broken run" has to hold on
        # every surface that claims to report the run.
        reasons = (
            "\n".join(gate.reasons)
            if gate is not None and gate.reasons
            else "no eval cases ran"
        )
        suite = SubElement(
            root,
            "testsuite",
            name="skill-eval",
            tests="1",
            failures="0",
            errors="1",
            skipped="0",
            time="0.000",
        )
        case = SubElement(
            suite, "testcase", classname="skill-eval", name="no eval cases ran", time="0.000"
        )
        SubElement(case, "error", message=_message(reasons)).text = _xml_safe(reasons)
        tests += 1
        errors += 1

    root.set("tests", str(tests))
    root.set("failures", str(failures))
    root.set("errors", str(errors))
    root.set("skipped", str(skipped))
    root.set("time", f"{total_time:.3f}")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(root, encoding="unicode")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_junit_reporter.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check . && uv run ruff format .
```

Expected: `All checks passed!` then a reformat count (0 or more files).

- [ ] **Step 6: Commit**

```bash
git add src/skill_eval/reporters/junit.py tests/test_junit_reporter.py && git commit -m "feat: render run reports as JUnit XML"
```

---

## Task 2: Markdown reporter

**Files:**
- Create: `src/skill_eval/reporters/markdown.py`
- Test: `tests/test_markdown_reporter.py`

**Interfaces:**
- Consumes: `RunReport`, `GateResult`, `Delta`, `format_baseline_notes` (from `skill_eval.comparison`).
- Produces: `render_markdown(report: RunReport, gate: GateResult | None = None, delta: Delta | None = None, max_chars: int | None = None) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_markdown_reporter.py`:

```python
"""Markdown reporter tests.

The truncation tests are the load-bearing ones: a clipped PR comment that has
lost the reason CI went red is worse than no comment at all.
"""

from __future__ import annotations

from skill_eval.comparison import build_delta
from skill_eval.gating import evaluate_gate
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
                    EvalScore(evaluator="assertion", passed=False, detail="missing 'not searchable'")
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_markdown_reporter.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'skill_eval.reporters.markdown'`.

- [ ] **Step 3: Write the reporter**

Create `src/skill_eval/reporters/markdown.py`:

```python
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
    tokens = sum(o.result.tokens for o in report.outcomes if o.result)
    cost = sum(o.result.cost_usd for o in report.outcomes if o.result)
    latency = sum(o.result.latency_ms for o in report.outcomes if o.result)
    # 0.0 means both "free" and "pricing failed everywhere"; the note is the
    # only thing that tells them apart.
    degraded = any(o.result.cost_note for o in report.outcomes if o.result)

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
    rows.append(
        ["Latency", f"{latency / 1000:.2f}s" if latency >= 1000 else f"{latency}ms"]
    )
    if report.baseline_errored:
        rows.append(["Baseline errored", str(report.baseline_errored)])
    return _table(["Metric", "Value"], rows)


def _per_skill(report: RunReport) -> str:
    rates = report.pass_rate_by_skill()
    if not rates:
        return ""
    counts: dict[str, dict[str, int]] = {}
    for outcome in report.candidate_outcomes:
        bucket = counts.setdefault(
            outcome.skill_name, {"passed": 0, "failed": 0, "errored": 0}
        )
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
    return "### Per skill\n" + _table(
        ["Skill", "Pass rate", "Passed", "Failed", "Errored"], rows
    )


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
                lines.append(
                    f"    - `{check.id}`: {check.evidence or 'no evidence given'}"
                )
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
        f"- {_cell(c.skill_name)} :: {_cell(c.case_name)}: `{c.check_id}`"
        for c in delta.low_signal
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
        bits.append(
            f"Skipped (no cases matched --tag): {', '.join(report.tag_filtered_skills)}"
        )
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_markdown_reporter.py -v
```

Expected: 16 passed.

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check . && uv run ruff format .
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/skill_eval/reporters/markdown.py tests/test_markdown_reporter.py && git commit -m "feat: render run reports as GitHub-flavored Markdown"
```

---

## Task 3: Bounded orchestrator concurrency

**Files:**
- Modify: `src/skill_eval/orchestrator.py:129-220` (replace `run_evals`'s body; add helpers above it)
- Test: `tests/test_orchestrator.py` (append)

**Interfaces:**
- Consumes: `_run_one`, `_baseline_skill`, `_arms` (existing, unchanged).
- Produces: `run_evals(..., concurrency: int = 1, executor_factory: Callable[[int], Executor] | None = None) -> RunReport`. Every existing keyword argument keeps its name, position and default.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
def _concurrency_skill(tmp_path, count=6):
    """A skill with `count` cases, each trivially passing under FakeRunner."""
    skill_dir = tmp_path / "concurrent"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: concurrent\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    cases = "cases:\n" + "".join(
        f"  - name: case-{i}\n    task: task-{i}\n    assertions:\n"
        f"      - kind: contains\n        value: '[fake]'\n"
        for i in range(count)
    )
    evals = skill_dir / "evals"
    evals.mkdir(exist_ok=True)
    (evals / "concurrent.eval.yaml").write_text(cases, encoding="utf-8")
    return load_skills(skill_dir)


def test_concurrency_produces_the_same_outcomes_in_the_same_order(tmp_path):
    """Order is submission order, never completion order: render_console
    iterates report.outcomes and build_delta groups by insertion order, so
    completion-order results would make output churn between identical runs.
    """
    skills = _concurrency_skill(tmp_path)
    sequential = run_evals(skills, [FakeRunner()])
    parallel = run_evals(skills, [FakeRunner()], concurrency=4)

    assert [(o.skill_name, o.case_name, o.arm, o.repeat_index) for o in parallel.outcomes] == [
        (o.skill_name, o.case_name, o.arm, o.repeat_index) for o in sequential.outcomes
    ]
    assert [o.status for o in parallel.outcomes] == [o.status for o in sequential.outcomes]
    assert parallel.pass_rate == sequential.pass_rate


def test_concurrency_one_never_constructs_an_executor(tmp_path):
    """Not an optimisation: no executor is what keeps the default path
    byte-identical and the order-sensitive cassette tier deterministic."""
    skills = _concurrency_skill(tmp_path, count=2)

    def explode(_workers):
        raise AssertionError("an executor must not be built at concurrency == 1")

    report = run_evals(skills, [FakeRunner()], executor_factory=explode)
    assert report.total == 2


def test_a_custom_executor_factory_is_used_above_one(tmp_path):
    skills = _concurrency_skill(tmp_path, count=2)
    seen: list[int] = []

    def factory(workers):
        seen.append(workers)
        return ThreadPoolExecutor(max_workers=workers)

    report = run_evals(skills, [FakeRunner()], concurrency=3, executor_factory=factory)
    assert seen == [3]
    assert report.total == 2


def test_an_authoring_error_still_aborts_the_run_under_concurrency(tmp_path):
    """A malformed assertion is a mistake in the user's files, not a signal
    about the skill. It must abort, never score as a failed case."""
    skill_dir = tmp_path / "bad"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bad\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    evals = skill_dir / "evals"
    evals.mkdir()
    (evals / "bad.eval.yaml").write_text(
        "cases:\n"
        + "".join(
            f"  - name: case-{i}\n    task: t{i}\n    assertions:\n"
            f"      - kind: no-such-kind\n        value: x\n"
            for i in range(4)
        ),
        encoding="utf-8",
    )
    skills = load_skills(skill_dir)
    with pytest.raises(UnknownAssertionKind):
        run_evals(skills, [FakeRunner()], concurrency=4)


def test_the_surfaced_authoring_error_is_deterministic(tmp_path):
    """Reading futures in submission order means the same error surfaces every
    time, so the message a user sees does not depend on thread scheduling."""
    skill_dir = tmp_path / "mixed"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mixed\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    evals = skill_dir / "evals"
    evals.mkdir()
    (evals / "mixed.eval.yaml").write_text(
        "cases:\n"
        "  - name: first\n    task: t1\n    assertions:\n"
        "      - kind: first-bad-kind\n        value: x\n"
        "  - name: second\n    task: t2\n    assertions:\n"
        "      - kind: second-bad-kind\n        value: x\n",
        encoding="utf-8",
    )
    skills = load_skills(skill_dir)
    messages = set()
    for _ in range(5):
        with pytest.raises(UnknownAssertionKind) as caught:
            run_evals(skills, [FakeRunner()], concurrency=4)
        messages.add(str(caught.value))
    assert len(messages) == 1
    assert "first-bad-kind" in messages.pop()


def test_concurrency_below_one_is_rejected(tmp_path):
    skills = _concurrency_skill(tmp_path, count=1)
    with pytest.raises(ValueError, match="concurrency must be at least 1"):
        run_evals(skills, [FakeRunner()], concurrency=0)
```

Add these imports at the top of `tests/test_orchestrator.py` if not already present:

```python
from concurrent.futures import ThreadPoolExecutor

import pytest

from skill_eval.evaluators.assertion import UnknownAssertionKind
from skill_eval.runners.fake import FakeRunner
from skill_eval.skills.loader import load_skills
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_orchestrator.py -k "concurrency or authoring or executor" -v
```

Expected: FAIL — `TypeError: run_evals() got an unexpected keyword argument 'concurrency'`.

- [ ] **Step 3: Rewrite the orchestrator's planning and execution**

In `src/skill_eval/orchestrator.py`, replace the import block at the top with:

```python
"""Build and run the skill x case x runner matrix."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.evaluators.assertion import AssertionEvaluator
from skill_eval.evaluators.base import Evaluator
from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.evaluators.judge import JudgeEvaluator
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.judges.base import Judge
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import (
    Arm,
    BaselineKind,
    BaselineNote,
    CaseOutcome,
    EvalCase,
    RunReport,
    Skill,
)
from skill_eval.runners.base import Runner
from skill_eval.skills.baseline import BaselineUnavailable, resolve_previous
```

Then insert these definitions immediately **above** `def run_evals(`, after the existing `_arms` function:

```python
@dataclass(frozen=True)
class _WorkItem:
    """One (skill-arm, case, runner, repetition) to run and score.

    `skill` is the arm's skill -- a baseline resolved from git keeps its own
    name -- while `report_skill_name` is the candidate's name, which is the
    heading both arms group under in the report.
    """

    skill: Skill
    case: EvalCase
    runner: Runner
    arm: Arm
    repeat_index: int
    report_skill_name: str


@dataclass
class _Plan:
    """Everything discovery produced, before anything has been run."""

    items: list[_WorkItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    tag_filtered: list[str] = field(default_factory=list)
    notes: list[BaselineNote] = field(default_factory=list)


def _plan_work(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None,
    tag: str | None,
    baseline: BaselineKind | None,
    repeat: int,
) -> _Plan:
    """Discovery, filtering and baseline resolution -- always sequential.

    Baseline resolution shells out to git once per skill; parallelising it
    would multiply subprocess spawns to save nothing. The nesting order here is
    what defines report order, so it must not change.
    """
    plan = _Plan()
    for skill in skills:
        cases = load_cases_for_skill(skill, evals_path=evals_path)
        if not cases:
            plan.skipped.append(skill.name)
            continue
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
            if not cases:
                plan.tag_filtered.append(skill.name)
                continue
        baseline_skill = (
            None if baseline is None else _baseline_skill(skill, baseline, plan.notes)
        )
        for case in cases:
            for arm, arm_skill in _arms(case, skill, baseline_skill, baseline, plan.notes):
                for runner in runners:
                    for index in range(repeat):
                        plan.items.append(
                            _WorkItem(
                                skill=arm_skill,
                                case=case,
                                runner=runner,
                                arm=arm,
                                repeat_index=index,
                                report_skill_name=skill.name,
                            )
                        )
    return plan


def _run_item(item: _WorkItem, evaluators: list[Evaluator]) -> CaseOutcome:
    return _run_one(
        item.skill,
        item.case,
        item.runner,
        evaluators,
        arm=item.arm,
        repeat_index=item.repeat_index,
        report_skill_name=item.report_skill_name,
    )


def _default_executor(concurrency: int) -> Executor:
    return ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="skill-eval")


def _execute(
    items: list[_WorkItem],
    evaluators: list[Evaluator],
    concurrency: int,
    executor_factory: Callable[[int], Executor] | None,
) -> list[CaseOutcome]:
    """Run every work item, in submission order.

    At `concurrency == 1` no executor is constructed at all. That is not an
    optimisation: it is what keeps the default path byte-identical to the
    single-threaded run -- same ordering, same exception propagation, and a
    cassette tier that vcrpy (order-sensitive, not thread-safe) can still match.

    Above 1, futures are collected and read in submission order so results
    never reorder by completion time, and an exception aborts without waiting
    on every in-flight provider call.
    """
    if concurrency == 1:
        return [_run_item(item, evaluators) for item in items]

    executor = (executor_factory or _default_executor)(concurrency)
    futures = [executor.submit(_run_item, item, evaluators) for item in items]
    outcomes: list[CaseOutcome] = []
    try:
        for future in futures:
            outcomes.append(future.result())
    except BaseException:
        # An authoring error must abort the whole run. `with executor:` would
        # shut down with wait=True and block on every call still in flight.
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    executor.shutdown(wait=True)
    return outcomes
```

Now replace the body of `run_evals` from `if evaluators is not None and judge is not None:` to the end of the function. The new signature and body:

```python
def run_evals(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None = None,
    evaluators: list[Evaluator] | None = None,
    tag: str | None = None,
    judge: Judge | None = None,
    baseline: BaselineKind | None = None,
    repeat: int = 1,
    concurrency: int = 1,
    executor_factory: Callable[[int], Executor] | None = None,
) -> RunReport:
```

(keep the existing docstring, and append this paragraph to it:)

```
    `concurrency` bounds how many work items run at once. It defaults to 1,
    which constructs no executor and behaves exactly like the single-threaded
    run -- upgrading must never change ordering or spend on its own. The work
    is network-bound, so threads (not processes) are the right unit; the
    parameter is typed against `concurrent.futures.Executor` via
    `executor_factory` so a different pool can be swapped in without touching
    call sites. Runners, judges and evaluators must therefore be safe to share
    across threads: no mutable instance state touched by run/evaluate/judge.
```

Body:

```python
    if evaluators is not None and judge is not None:
        raise ValueError(
            "run_evals() received both `evaluators` and `judge`; pass an explicit "
            "JudgeEvaluator inside `evaluators` instead of also passing `judge`."
        )
    if repeat < 1:
        raise ValueError(f"repeat must be at least 1, got {repeat}")
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, got {concurrency}")
    evaluators = (
        evaluators
        if evaluators is not None
        else [
            AssertionEvaluator(),
            TrajectoryEvaluator(),
            BudgetEvaluator(),
            # The offline judge by default: M3 must never start spending money
            # on its own. Unscripted it errors rather than passing, so a rubric
            # with no real judge configured is never a vacuous green.
            JudgeEvaluator(judge if judge is not None else FakeJudge()),
        ]
    )
    plan = _plan_work(skills, runners, evals_path, tag, baseline, repeat)
    outcomes = _execute(plan.items, evaluators, concurrency, executor_factory)
    return RunReport(
        outcomes=outcomes,
        skipped_skills=plan.skipped,
        tag_filtered_skills=plan.tag_filtered,
        baseline_kind=baseline,
        repeat=repeat,
        baseline_notes=plan.notes,
    )
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
uv run pytest tests/test_orchestrator.py -v
```

Expected: all pass, including the six new tests.

- [ ] **Step 5: Run the whole suite to confirm nothing regressed**

```bash
uv run pytest
```

Expected: all pass. This is the check that the planning refactor preserved report ordering — `tests/test_reporters.py`, `tests/test_comparison.py` and `tests/test_arms.py` all depend on it.

- [ ] **Step 6: Lint, format, commit**

```bash
uv run ruff check . && uv run ruff format . && git add src/skill_eval/orchestrator.py tests/test_orchestrator.py && git commit -m "feat: run the eval matrix with bounded concurrency"
```

---

## Task 4: CLI and config wiring

**Files:**
- Modify: `src/skill_eval/config.py:52-65` (add `concurrency`)
- Modify: `src/skill_eval/cli.py` (imports, `run` signature, resolution, the write loop)
- Modify: `docs/cli.md`, `docs/configuration.md`, `docs/gating.md`
- Test: `tests/test_cli.py`, `tests/test_config.py` (append)

**Interfaces:**
- Consumes: `render_junit`, `render_markdown` (Tasks 1–2); `run_evals(..., concurrency=)` (Task 3).
- Produces: CLI flags `--junit-output`, `--markdown-output`, `--markdown-max-chars`, `--concurrency`; `Config.concurrency: int = 1`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_concurrency_defaults_to_one_and_can_be_set(tmp_path):
    """Default 1 for the same reason judge defaults to "fake": upgrading must
    never change spend or behavior on its own."""
    assert Config().concurrency == 1
    path = tmp_path / "skill-eval.toml"
    path.write_text("concurrency = 8\n", encoding="utf-8")
    assert load_config(path=path).concurrency == 8
```

Append to `tests/test_cli.py`:

```python
def test_junit_output_writes_parseable_xml(tmp_path):
    _make_skill(tmp_path / "skills")
    out = tmp_path / "reports" / "junit.xml"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--junit-output", str(out)])
    assert result.exit_code == 0
    root = ET.fromstring(out.read_text(encoding="utf-8"))
    assert root.tag == "testsuites"
    assert root.get("tests") == "1"


def test_markdown_output_writes_a_summary(tmp_path):
    _make_skill(tmp_path / "skills")
    out = tmp_path / "summary.md"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--markdown-output", str(out)])
    assert result.exit_code == 0
    assert "gate passed" in out.read_text(encoding="utf-8")


def test_markdown_max_chars_truncates(tmp_path):
    _make_skill(tmp_path / "skills")
    out = tmp_path / "summary.md"
    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path / "skills"),
            "--markdown-output",
            str(out),
            "--markdown-max-chars",
            "80",
        ],
    )
    assert result.exit_code == 0
    assert len(out.read_text(encoding="utf-8")) <= 80


def test_a_junit_write_failure_does_not_mask_a_failing_gate(tmp_path):
    """Exit codes are the CI contract: a red gate must stay visible rather
    than being escalated to 2 by an unrelated write problem."""
    _make_skill(tmp_path / "skills", cases=FAILING_CASES_YAML)
    blocking = tmp_path / "blocking"
    blocking.write_text("I am a file")
    out = blocking / "junit.xml"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--junit-output", str(out)])
    assert result.exit_code == 1
    assert "Failed to write JUnit report" in result.stdout


def test_a_markdown_write_failure_with_a_passing_gate_exits_two(tmp_path):
    _make_skill(tmp_path / "skills")
    blocking = tmp_path / "blocking"
    blocking.write_text("I am a file")
    out = blocking / "summary.md"
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--markdown-output", str(out)])
    assert result.exit_code == 2
    assert "Failed to write Markdown report" in result.stdout


def test_concurrency_below_one_is_a_user_error(tmp_path):
    _make_skill(tmp_path / "skills")
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--concurrency", "0"])
    assert result.exit_code == 2
    assert "--concurrency must be at least 1" in plain(result.stdout)


def test_concurrency_above_one_runs_the_suite(tmp_path):
    _make_skill(tmp_path / "skills")
    result = runner.invoke(app, ["run", str(tmp_path / "skills"), "--concurrency", "4"])
    assert result.exit_code == 0
```

Add to the imports at the top of `tests/test_cli.py`:

```python
import xml.etree.ElementTree as ET
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_cli.py -k "junit or markdown or concurrency" tests/test_config.py -k concurrency -v
```

Expected: FAIL — `No such option: --junit-output`, and `Config().concurrency` raising `AttributeError`.

- [ ] **Step 3: Add the config field**

In `src/skill_eval/config.py`, add after `repeat: int = 1`:

```python
    concurrency: int = 1
```

And append this paragraph to the `Config` docstring, after the `baseline` paragraph:

```
    `concurrency` defaults to 1 -- no executor is constructed and behavior is
    identical to a single-threaded run. The work is network-bound (one provider
    round trip per item against sub-millisecond of local CPU), so raising it
    overlaps waits rather than using more cores; the practical ceiling is the
    provider's rate limit. Validation lives in the CLI, not here, so a config
    value and a flag are checked the same way `repeat` already is.
```

- [ ] **Step 4: Wire the CLI**

In `src/skill_eval/cli.py`, add to the imports:

```python
from skill_eval.reporters.junit import render_junit
from skill_eval.reporters.markdown import render_markdown
```

Add this helper immediately above `@app.command()` for `run`:

```python
def _write_report(path: Path, text: str, label: str) -> str | None:
    """Write one report file. Returns an error message, or None on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"Failed to write {label} report to {path}: {exc}"
    return None
```

Add these four parameters to `run`, after `min_delta`:

```python
    junit_output: Annotated[
        Path | None, typer.Option(help="Write a JUnit XML report here.")
    ] = None,
    markdown_output: Annotated[
        Path | None, typer.Option(help="Write a Markdown summary here.")
    ] = None,
    markdown_max_chars: Annotated[
        int | None,
        typer.Option(help="Truncate the Markdown summary to this many characters."),
    ] = None,
    concurrency: Annotated[
        int | None, typer.Option(help="Run this many cases at once.")
    ] = None,
```

Inside the `try:` block, add the resolution right after the `resolved_repeat` check:

```python
        resolved_concurrency = concurrency if concurrency is not None else settings.concurrency
        if resolved_concurrency < 1:
            raise typer.BadParameter("--concurrency must be at least 1")
```

Pass it to `run_evals`:

```python
        report = run_evals(
            skills,
            [active_runner],
            evals_path=evals,
            tag=tag,
            judge=active_judge,
            baseline=baseline_kind or None,
            repeat=resolved_repeat,
            concurrency=resolved_concurrency,
        )
```

Replace the whole `if json_output is not None:` block (currently `cli.py:205-217`) with:

```python
    # One loop over every requested report. A write failure escalates to exit 2
    # only when the gate itself passed -- exit codes are the CI contract, and an
    # already-red gate must stay visible rather than being masked by an
    # unrelated write problem.
    writes = (
        (json_output, "JSON", lambda: render_json(report, gate=gate, delta=delta)),
        (junit_output, "JUnit", lambda: render_junit(report, gate=gate, delta=delta)),
        (
            markdown_output,
            "Markdown",
            lambda: render_markdown(
                report, gate=gate, delta=delta, max_chars=markdown_max_chars
            ),
        ),
    )
    write_failed = False
    for target, label, render in writes:
        if target is None:
            continue
        error = _write_report(target, render(), label)
        if error is not None:
            typer.echo(error)
            write_failed = True
    if write_failed and gate.exit_code == EXIT_OK:
        raise typer.Exit(code=2)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_cli.py tests/test_config.py -v
```

Expected: all pass, including the existing `test_json_write_failure_does_not_mask_a_failing_gate` — the message text is unchanged.

- [ ] **Step 6: Update the docs**

In `docs/cli.md`, add to the `run` options table after the `--min-delta` row:

```markdown
| `--junit-output <path>` | none | Write a JUnit XML report here, for CI test panes |
| `--markdown-output <path>` | none | Write a Markdown summary here, for a job summary or PR comment |
| `--markdown-max-chars <int>` | unset | Truncate the Markdown summary. Detail blocks are dropped first; the verdict and every gate reason always survive |
| `--concurrency <int>` | `1` | Run this many cases at once. The work is network-bound, so the practical ceiling is your provider's rate limit |
```

And update the usage synopsis at `docs/cli.md:6` to include the new flags:

```
                      [--json-output <path>] [--junit-output <path>]
                      [--markdown-output <path>] [--markdown-max-chars <int>]
                      [--concurrency <int>] [--config <file>] [--baseline <kind>]
```

In `docs/configuration.md`, add to the key table after the `min_delta` row:

```markdown
| `concurrency` | `1` | `--concurrency` |
```

And add this paragraph after the `baseline`/`repeat`/`min_delta` paragraph:

```markdown
`concurrency` bounds how many cases run at once. It defaults to `1`, which runs everything
sequentially and behaves exactly as it did before the option existed. The work is
network-bound — one provider round trip per case against sub-millisecond of local work — so
raising it overlaps waiting, not computation; the practical ceiling is your provider's rate
limit, not your CPU. Runners and evaluators are shared across threads, so a custom one must
have no mutable state its `run`/`evaluate` touches.
```

In `docs/gating.md`, add a section documenting the JUnit mapping:

```markdown
## JUnit XML

`--junit-output` writes a JUnit report, the format GitHub, GitLab, Jenkins, CircleCI and
Buildkite all ingest natively.

| skill-eval | JUnit |
| --- | --- |
| `passed` | `<testcase>` with no child |
| `failed` | `<testcase>` with `<failure>` |
| `errored` | `<testcase>` with `<error>` |
| a skill with no cases | `<testcase>` with `<skipped>` |

The `failed`/`errored` split is the same one the exit code and the JSON report use: a
`<failure>` means the case ran and scored below bar, an `<error>` means the runner or an
evaluator blew up.

Only the **candidate** arm becomes test cases. Under `--baseline`, a failing baseline is the
evidence that the skill helped, so rendering it as a `<failure>` would turn CI red for the
skill working.

A run with no eval cases emits a single `<testcase>` carrying an `<error>` that repeats the
gate's reasons. An empty `tests="0"` file renders green in most CI UIs, which would contradict
the exit code of 1.
```

- [ ] **Step 7: Verify the docs tests pass**

```bash
uv run pytest tests/test_docs.py -v
```

Expected: all pass — `test_every_cli_option_is_documented` and `test_every_config_field_is_documented` cover the new surface.

- [ ] **Step 8: Lint, format, commit**

```bash
uv run ruff check . && uv run ruff format . && git add -A && git commit -m "feat: add --junit-output, --markdown-output and --concurrency to run"
```

---

## Task 5: Composite action, example workflows and their guards

**Files:**
- Create: `action.yml`, `examples/ci/skill-eval.yml`, `examples/ci/skill-eval-cli.yml`
- Create: `tests/fixtures/ci-smoke/SKILL.md`, `tests/fixtures/ci-smoke/evals/ci-smoke.eval.yaml`
- Create: `tests/test_action.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: every `run` flag from Task 4.
- Produces: an action whose inputs are the kebab-cased `run` flags plus `install-spec`, `working-directory` and `step-summary`; outputs `exit-code`, `passed`, `pass-rate`, `json-report`, `junit-report`, `markdown-report`.

- [ ] **Step 1: Write the failing drift test**

Create `tests/test_action.py`:

```python
"""Keep action.yml and the CLI from drifting apart.

Two documented surfaces are only safe if something fails the build when they
disagree. The second test is the one that matters: it fires when a future flag
is added to `run` and the action is forgotten.
"""

from __future__ import annotations

from pathlib import Path

from typer.main import get_command

from skill_eval.cli import app
from skill_eval.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION = REPO_ROOT / "action.yml"

# Inputs about the environment the action runs in, not about the run itself.
ENVIRONMENT_INPUTS = {"install-spec", "working-directory", "step-summary"}

# `path` is a positional argument, not an option.
ARGUMENT_INPUTS = {"path"}

IGNORED_FLAGS = {"--help", "--install-completion", "--show-completion"}


def _action() -> dict:
    return safe_load(ACTION.read_text(encoding="utf-8"))


def _action_inputs() -> set[str]:
    return set(_action()["inputs"])


def _run_flags() -> set[str]:
    command = get_command(app).commands["run"]
    return {
        opt
        for param in command.params
        if param.param_type_name == "option"
        for opt in param.opts
        if opt.startswith("--") and opt not in IGNORED_FLAGS
    }


def test_every_action_input_maps_to_a_real_cli_flag():
    flags = _run_flags()
    for name in _action_inputs() - ENVIRONMENT_INPUTS - ARGUMENT_INPUTS:
        assert f"--{name}" in flags, f"action input {name!r} has no matching CLI flag"


def test_every_cli_flag_is_exposed_as_an_action_input():
    inputs = _action_inputs()
    for flag in _run_flags():
        name = flag.removeprefix("--")
        assert name in inputs, f"CLI flag {flag} is not exposed as an action.yml input"


def test_every_action_input_is_described():
    for name, spec in _action()["inputs"].items():
        assert spec.get("description"), f"action input {name!r} has no description"


def test_the_action_declares_its_report_outputs():
    outputs = set(_action()["outputs"])
    assert {"exit-code", "passed", "pass-rate"} <= outputs
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_action.py -v
```

Expected: FAIL — `FileNotFoundError: .../action.yml`.

- [ ] **Step 3: Write the composite action**

Create `action.yml`:

```yaml
name: skill-eval
description: Run evaluations on Agent Skills (SKILL.md) and gate the build on the result.
branding:
  icon: check-circle
  color: purple

inputs:
  path:
    description: A skill directory, or a directory of skills.
    required: true
  evals:
    description: Explicit eval file or directory, overriding discovery.
  runner:
    description: Runner to use — fake or pydantic-ai.
  model:
    description: Model id, e.g. openai:gpt-4o-mini.
  judge-model:
    description: Model id for the LLM judge. Falls back to model.
  tag:
    description: Only run cases carrying this tag.
  config:
    description: Path to skill-eval.toml.
  baseline:
    description: Run a second, baseline arm — none or previous.
  repeat:
    description: Sample each arm this many times.
  min-pass-rate:
    description: Required overall pass rate, 0.0–1.0.
  min-delta:
    description: Required improvement over the baseline. Needs baseline.
  concurrency:
    description: Run this many cases at once.
  json-output:
    description: Where to write the JSON report. Also read back for this action's outputs.
    default: skill-eval-report.json
  junit-output:
    description: Where to write the JUnit XML report.
    default: skill-eval-junit.xml
  markdown-output:
    description: Where to write the Markdown summary.
    default: skill-eval-summary.md
  markdown-max-chars:
    description: >-
      Truncate the Markdown summary to this many characters. Leave unset for the step
      summary, which allows 1 MiB; set roughly 60000 when the summary will be posted as a
      PR comment, which GitHub caps at 65536.
  install-spec:
    description: >-
      Passed verbatim to `uv tool install`. A PyPI name, a pinned version, a git ref
      (git+https://github.com/EmadMokhtar/skill-evaluator@main) or a local path.
    default: skill-eval[pydantic-ai]
  working-directory:
    description: Directory to run in.
    default: .
  step-summary:
    description: Append the Markdown summary to $GITHUB_STEP_SUMMARY.
    default: "true"

outputs:
  exit-code:
    description: The CLI's exit code — 0 gate passed, 1 gate failed, 2 user error.
    value: ${{ steps.run.outputs.exit-code }}
  passed:
    description: Whether the gate passed, as "true" or "false".
    value: ${{ steps.report.outputs.passed }}
  pass-rate:
    description: Overall candidate pass rate, 0.0–1.0. Empty if no report was written.
    value: ${{ steps.report.outputs.pass-rate }}
  json-report:
    description: Path to the JSON report.
    value: ${{ inputs.json-output }}
  junit-report:
    description: Path to the JUnit XML report.
    value: ${{ inputs.junit-output }}
  markdown-report:
    description: Path to the Markdown summary.
    value: ${{ inputs.markdown-output }}

runs:
  using: composite
  steps:
    - name: Install uv
      uses: astral-sh/setup-uv@v5
      with:
        enable-cache: true

    - name: Install skill-eval
      shell: bash
      env:
        INSTALL_SPEC: ${{ inputs.install-spec }}
      run: |
        set -euo pipefail
        uv tool install --force "$INSTALL_SPEC"
        echo "$HOME/.local/bin" >> "$GITHUB_PATH"

    - name: Run skill-eval
      id: run
      shell: bash
      working-directory: ${{ inputs.working-directory }}
      # Every input arrives as an environment variable rather than being
      # interpolated into the script body, so nothing a caller passes can be
      # read as shell.
      env:
        SE_PATH: ${{ inputs.path }}
        SE_EVALS: ${{ inputs.evals }}
        SE_RUNNER: ${{ inputs.runner }}
        SE_MODEL: ${{ inputs.model }}
        SE_JUDGE_MODEL: ${{ inputs.judge-model }}
        SE_TAG: ${{ inputs.tag }}
        SE_CONFIG: ${{ inputs.config }}
        SE_BASELINE: ${{ inputs.baseline }}
        SE_REPEAT: ${{ inputs.repeat }}
        SE_MIN_PASS_RATE: ${{ inputs.min-pass-rate }}
        SE_MIN_DELTA: ${{ inputs.min-delta }}
        SE_CONCURRENCY: ${{ inputs.concurrency }}
        SE_JSON: ${{ inputs.json-output }}
        SE_JUNIT: ${{ inputs.junit-output }}
        SE_MARKDOWN: ${{ inputs.markdown-output }}
        SE_MARKDOWN_MAX: ${{ inputs.markdown-max-chars }}
      run: |
        set -uo pipefail
        args=("$SE_PATH")
        add() { if [ -n "${2:-}" ]; then args+=("$1" "$2"); fi; }
        add --evals "$SE_EVALS"
        add --runner "$SE_RUNNER"
        add --model "$SE_MODEL"
        add --judge-model "$SE_JUDGE_MODEL"
        add --tag "$SE_TAG"
        add --config "$SE_CONFIG"
        add --baseline "$SE_BASELINE"
        add --repeat "$SE_REPEAT"
        add --min-pass-rate "$SE_MIN_PASS_RATE"
        add --min-delta "$SE_MIN_DELTA"
        add --concurrency "$SE_CONCURRENCY"
        add --json-output "$SE_JSON"
        add --junit-output "$SE_JUNIT"
        add --markdown-output "$SE_MARKDOWN"
        add --markdown-max-chars "$SE_MARKDOWN_MAX"

        # The exit code is the CI contract, but it must not stop this step
        # before the summary is written -- so it is captured here and re-raised
        # by the last step instead.
        skill-eval run "${args[@]}"
        code=$?
        echo "exit-code=$code" >> "$GITHUB_OUTPUT"

    - name: Publish the step summary
      if: inputs.step-summary == 'true'
      shell: bash
      working-directory: ${{ inputs.working-directory }}
      env:
        SE_MARKDOWN: ${{ inputs.markdown-output }}
      run: |
        set -uo pipefail
        if [ -f "$SE_MARKDOWN" ]; then
          cat "$SE_MARKDOWN" >> "$GITHUB_STEP_SUMMARY"
        fi

    - name: Read the report
      id: report
      shell: bash
      working-directory: ${{ inputs.working-directory }}
      env:
        SE_JSON: ${{ inputs.json-output }}
      run: |
        set -uo pipefail
        # Exit code 2 means an authoring error aborted before any report was
        # written, so a missing file is expected and must not kill the step.
        python3 - >> "$GITHUB_OUTPUT" <<'PY'
        import json
        import os
        import pathlib

        path = pathlib.Path(os.environ["SE_JSON"])
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            print(f"pass-rate={data['summary']['pass_rate']}")
            print(f"passed={str(data.get('gate', {}).get('passed', False)).lower()}")
        else:
            print("pass-rate=")
            print("passed=false")
        PY

    - name: Re-raise the gate result
      shell: bash
      env:
        CODE: ${{ steps.run.outputs.exit-code }}
      run: exit "$CODE"
```

- [ ] **Step 4: Run the drift test to verify it passes**

```bash
uv run pytest tests/test_action.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Add the smoke fixture**

Create `tests/fixtures/ci-smoke/SKILL.md`:

```markdown
---
name: ci-smoke
description: A fixture skill used only to smoke-test the composite action.
---

Answer briefly and plainly.
```

Create `tests/fixtures/ci-smoke/evals/ci-smoke.eval.yaml`:

```yaml
# Asserts on FakeRunner's default response ("[fake] {skill} handled: {task}"),
# so the whole CLI path runs offline, deterministically and for free. This is a
# smoke test of the action's plumbing, not of any model's behaviour.
cases:
  - name: the fake runner answers
    task: say hello
    assertions:
      - kind: contains
        value: "[fake]"
```

- [ ] **Step 6: Verify the fixture passes locally**

```bash
out=$(mktemp -d) && uv run skill-eval run tests/fixtures/ci-smoke --junit-output "$out/junit.xml" --markdown-output "$out/summary.md" && rm -rf "$out"
```

Expected: `1 passed, 0 failed, 0 errored — pass rate 100%`, exit code 0.

- [ ] **Step 7: Add the CI smoke job**

Append to `.github/workflows/ci.yml`:

```yaml
  action-smoke:
    # Exercises action.yml end to end. tests/test_action.py can only check that
    # the inputs match the CLI's flags; nothing but a real run catches a missing
    # `shell: bash`, a bad expression, or a step that swallows the exit code.
    # `install-spec: .` installs this checkout, so the job needs no release.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run the action against a fixture skill
        uses: ./
        with:
          path: tests/fixtures/ci-smoke
          runner: fake
          install-spec: .
          json-output: smoke/report.json
          junit-output: smoke/junit.xml
          markdown-output: smoke/summary.md

      - name: Assert the reports were written and are well-formed
        run: |
          set -euo pipefail
          test -f smoke/report.json
          test -f smoke/junit.xml
          test -f smoke/summary.md
          python3 -c "import xml.etree.ElementTree as ET; ET.parse('smoke/junit.xml')"
          grep -q "gate passed" smoke/summary.md
```

- [ ] **Step 8: Write the example workflows**

Create `examples/ci/skill-eval.yml`:

```yaml
# Gate pull requests on a skill-eval run, using the composite action.
#
# Copy this into .github/workflows/ in your own repository. Files under
# examples/ are inert -- only .github/workflows/ is executed by GitHub.
name: skill-eval

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          # --baseline previous resolves the skill's prior version from git
          # history, so a shallow clone would leave it with nothing to compare.
          fetch-depth: 0

      - name: Evaluate the skills
        id: eval
        uses: EmadMokhtar/skill-evaluator@v1
        continue-on-error: true
        with:
          path: ./skills
          runner: pydantic-ai
          model: openai:gpt-4o-mini
          baseline: previous
          repeat: 3
          concurrency: 4
          # GitHub caps a comment at 65536 characters. Detail blocks are
          # dropped first; the verdict and the gate reasons always survive.
          markdown-max-chars: "60000"
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}

      - name: Upload the reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: skill-eval-reports
          path: |
            skill-eval-report.json
            skill-eval-junit.xml
            skill-eval-summary.md

      - name: Comment on the pull request
        # GitHub withholds write tokens from fork-triggered `pull_request` runs,
        # so an unguarded comment step fails on exactly the PRs an open-source
        # project gets most. Fork PRs still get the step summary, the JUnit
        # rendering and the correct exit code. See docs/ci.md for the
        # `workflow_run` pattern if you need comments on forks too --
        # `pull_request_target` is not the answer.
        if: github.event.pull_request.head.repo.full_name == github.repository
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const marker = '<!-- skill-eval -->';
            const body = marker + '\n' + fs.readFileSync('skill-eval-summary.md', 'utf8');
            const { data: comments } = await github.rest.issues.listComments({
              ...context.repo,
              issue_number: context.issue.number,
            });
            const existing = comments.find(
              (c) => c.user.type === 'Bot' && c.body.includes(marker)
            );
            if (existing) {
              await github.rest.issues.updateComment({
                ...context.repo, comment_id: existing.id, body,
              });
            } else {
              await github.rest.issues.createComment({
                ...context.repo, issue_number: context.issue.number, body,
              });
            }

      - name: Fail the build if the gate failed
        # `continue-on-error` above let the comment and artifact steps run; the
        # gate's verdict is re-raised here so the exit code still decides the
        # build.
        if: steps.eval.outputs.exit-code != '0'
        run: exit ${{ steps.eval.outputs.exit-code }}
```

Create `examples/ci/skill-eval-cli.yml`:

```yaml
# The same gate without the composite action, for repositories that would
# rather call the CLI directly.
#
# Copy this into .github/workflows/ in your own repository.
name: skill-eval (CLI)

on:
  pull_request:

permissions:
  contents: read

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install skill-eval
        run: uv tool install "skill-eval[pydantic-ai]"

      - name: Evaluate the skills
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          skill-eval run ./skills \
            --runner pydantic-ai \
            --model openai:gpt-4o-mini \
            --concurrency 4 \
            --json-output skill-eval-report.json \
            --junit-output skill-eval-junit.xml \
            --markdown-output skill-eval-summary.md

      - name: Publish the summary
        # `always()` so a failing gate still reports why.
        if: always()
        run: cat skill-eval-summary.md >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 9: Verify both example workflows are valid YAML**

```bash
uv run python -c "
from pathlib import Path
from skill_eval.yaml_loading import safe_load
for p in sorted(Path('examples/ci').glob('*.yml')):
    safe_load(p.read_text(encoding='utf-8'))
    print('ok', p)
"
```

Expected: `ok examples/ci/skill-eval-cli.yml` and `ok examples/ci/skill-eval.yml`.

- [ ] **Step 10: Confirm the fixture does not disturb existing discovery tests**

```bash
uv run pytest
```

Expected: all pass. `testpaths = ["tests"]` collects only `test_*.py`, so the fixture's `SKILL.md` and YAML are inert to pytest.

- [ ] **Step 11: Lint, format, commit**

```bash
uv run ruff check . && uv run ruff format . && git add -A && git commit -m "feat: add a composite GitHub Action and example CI workflows"
```

---

## Task 6: CI documentation page and cross-cutting docs

**Files:**
- Create: `docs/ci.md`
- Modify: `mkdocs.yml` (nav), `docs/roadmap.md`, `ARCHITECTURE.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: no code.

- [ ] **Step 1: Write the CI page**

Create `docs/ci.md`:

````markdown
# Running skill-eval in CI

skill-eval is built to be a CI gate: the exit code is the contract — `0` gate passed, `1` gate
failed, `2` a user or authoring error. Everything else on this page is about making that
verdict legible.

## Reports

| Flag | Format | Read by |
| --- | --- | --- |
| `--json-output` | JSON | Tooling, dashboards, artifact storage |
| `--junit-output` | JUnit XML | GitHub, GitLab, Jenkins, CircleCI, Buildkite test panes |
| `--markdown-output` | Markdown | `$GITHUB_STEP_SUMMARY`, PR comments |

The JUnit mapping — including why only the candidate arm becomes test cases — is documented in
[Gating and exit codes](gating.md#junit-xml).

skill-eval never talks to the GitHub API. It renders a Markdown file; your workflow decides
where that goes.

## The composite action

```yaml
- uses: EmadMokhtar/skill-evaluator@v1
  with:
    path: ./skills
    runner: pydantic-ai
    model: openai:gpt-4o-mini
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

Every `skill-eval run` flag is available as a kebab-cased input (`--min-pass-rate` becomes
`min-pass-rate`), plus three inputs about the environment rather than the run:

| Input | Default | Purpose |
| --- | --- | --- |
| `install-spec` | `skill-eval[pydantic-ai]` | Passed verbatim to `uv tool install`. Accepts a PyPI name, a pinned version, a git ref, or a local path. |
| `working-directory` | `.` | Directory to run in. |
| `step-summary` | `true` | Append the Markdown summary to `$GITHUB_STEP_SUMMARY`. |

Outputs: `exit-code`, `passed`, `pass-rate`, `json-report`, `junit-report`, `markdown-report`.

`json-output`, `junit-output` and `markdown-output` default to real paths rather than being
unset, because the action reads the JSON back to produce `passed` and `pass-rate`.

## A complete workflow

```yaml
--8<-- "examples/ci/skill-eval.yml"
```

## Without the action

```yaml
--8<-- "examples/ci/skill-eval-cli.yml"
```

## Pull request comments on forks

GitHub deliberately gives `pull_request` runs triggered from a **fork** a read-only token, so a
comment step fails there no matter what `permissions:` says. The example above guards the
comment step with:

```yaml
if: github.event.pull_request.head.repo.full_name == github.repository
```

Fork PRs still get the step summary, the JUnit rendering and the correct exit code — the
substance of the report. Only the comment is skipped.

If you need comments on fork PRs, use the two-workflow `workflow_run` pattern: the
`pull_request` workflow runs the evaluation and uploads the Markdown as an artifact, never
holding a write token; a second workflow triggered on `workflow_run` downloads it and posts the
comment with `pull-requests: write`.

Do **not** reach for `pull_request_target` instead. It runs the base repository's workflow with
a write token and access to secrets, and checking out the pull request's head under it is one
of the best-known ways to hand a fork's code your repository's credentials.

## Concurrency and cost

`--concurrency N` runs N cases at once. The work is network-bound — one provider round trip per
case against sub-millisecond of local work — so this overlaps waiting rather than using more
cores, and the practical ceiling is your provider's rate limit.

It does not change what a run costs. `--baseline` and `--repeat` do: `--baseline previous
--repeat 3` is six runs per case, not one. `skill-eval run` prints a ceiling estimate before it
starts whenever the runner needs an API key.
````

- [ ] **Step 2: Add the page to the nav**

In `mkdocs.yml`, add to the `Reference:` list after `Comparative evals`:

```yaml
      - CI integration: ci.md
```

- [ ] **Step 3: Verify the docs build and the docs tests pass**

```bash
uv sync --group docs && uv run mkdocs build --strict && uv run pytest tests/test_docs.py -v
```

Expected: the build succeeds (the `--8<--` snippet includes resolve against `base_path: ["."]`,
already configured), and every docs test passes including
`test_every_page_is_reachable_from_the_nav`.

- [ ] **Step 4: Update the roadmap**

In `docs/roadmap.md`, change the M5 row (line 10) to:

```markdown
| M5 | CI/CD polish: JUnit XML + Markdown reporters, GitHub Action, bounded concurrency | shipped (part 1) |
```

And add a section after the M4 one:

```markdown
## What M5 part 1 shipped

`--junit-output` and `--markdown-output` render a run for CI test panes and for GitHub's step
summary and PR comments. `--concurrency N` overlaps the network waits that dominate a run.
A composite GitHub Action wraps the CLI, with example workflows in
[CI integration](ci.md).

An HTML reporter was dropped as YAGNI — nothing in the milestone consumes it. Process and
subinterpreter pools were deferred: the work is network-bound, so multi-core buys nothing
today, and the orchestrator is typed against `concurrent.futures.Executor` so a different pool
is a one-line change if M6's real tool execution introduces CPU-bound work.

Part 2 — the automated release pipeline (`cz bump` on merge to main, Trusted Publishing to
PyPI) and the manual cassette-refresh workflow — is specified and waiting on the PyPI project
and repository secrets it needs.
```

- [ ] **Step 5: Update ARCHITECTURE.md**

In the module map, update the `reporters/` line to name all four renderers, and the
`orchestrator.py` line to mention concurrency:

```
  reporters/       # console, json, junit, markdown — pure renderers, no IO
  orchestrator.py  # plan (sequential discovery) → execute (bounded concurrency)
```

Add to the "Invariants, and why" section:

```markdown
### CI surfaces (M5)

- **JUnit reports the candidate arm only.** Under `--baseline`, a failing baseline is the
  evidence that the skill helped. Rendering it as `<failure>` would paint CI red for the skill
  working — the same reason every `RunReport` aggregate reads `candidate_outcomes`.
- **`<failure>` is `failed`; `<error>` is `errored`.** The project's central distinction, given
  a native rendering: an exploded runner must not look like a skill that got worse.
- **JUnit output is always well-formed XML.** `ElementTree` escapes `&`, `<` and `>` but emits
  control characters raw, so a model returning `\x00` would produce a file every parser
  rejects. Illegal characters are stripped before they reach the tree.
- **A zero-case run produces a JUnit `<error>`, not an empty green suite.** `tests="0"` renders
  green in most CI UIs, which would contradict the exit code of 1.
- **Markdown truncation never drops the verdict or a gate reason.** A clipped comment that has
  lost the reason CI went red is worse than no comment. Truncation drops whole `<details>`
  blocks, so it lives in the renderer — a caller slicing the string would cut one open.
- **Reporters never do IO to a service.** They return a string. The CLI writes files; the
  workflow posts comments. A GitHub client inside a reporter would put token scopes and network
  failure inside a pure function.
- **`--concurrency 1` constructs no executor** and is byte-identical to a single-threaded run —
  same ordering, same exception propagation, and a cassette tier vcrpy can still match in order.
- **Outcome order is submission order, never completion order.** `render_console` iterates
  `report.outcomes` and `build_delta` groups by insertion order, so completion-order results
  would make output churn between identical runs.
- **Concurrency never turns an authoring error into a case failure**, and the surfaced error is
  deterministic — futures are read in submission order, and an abort shuts down with
  `cancel_futures=True` rather than waiting on every in-flight call.
- **Runners, judges and evaluators must be safe to share across threads.** No mutable instance
  state touched by `run`/`evaluate`/`judge`. This holds today for free: the fakes read
  immutable dicts and return `model_copy(deep=True)`, the deterministic evaluators have no
  instance state, and `PydanticAIRunner` builds a fresh agent per run. It is a constraint on
  what comes next.
- **The action re-raises the CLI's exit code**, and writes the step summary before it does.
  Writing the summary only on success would hide the report exactly when it is needed.
```

- [ ] **Step 6: Update CLAUDE.md**

Replace "Currently at **M4**" in the "What this is" section with:

```markdown
Currently at **M5 (part 1)**: the pipeline runs real agents through `PydanticAIRunner`
(provider-flexible, via PydanticAI), scores tool use and efficiency as well as
output text, and is tested against recorded provider traffic. `FakeRunner`
remains the default and the backbone of the zero-cost test tier. M3 adds a
rubric-based LLM judge that scores output quality with per-check evidence, and
an `offered` case mode that measures whether the agent chose to trigger the
skill at all, negative controls included. M4 makes every measurement
comparative: each case can run in a candidate arm and a baseline arm
(`--baseline none` or `--baseline previous`, resolved from git), optionally
sampled `--repeat N` times, with the report gaining a delta and `--min-delta`
gating on it. M5 part 1 makes a run legible to CI: `--junit-output` and
`--markdown-output` reporters, `--concurrency N` over the work matrix, and a
composite GitHub Action with example workflows. Milestones are defined in
`docs/superpowers/specs/2026-07-30-skill-eval-design.md` §9; the M2 design is
in `docs/superpowers/specs/2026-08-01-skill-eval-m2-design.md`, the M3 design
is in `docs/superpowers/specs/2026-08-03-skill-eval-m3-design.md`, the M4
design is in `docs/superpowers/specs/2026-08-03-skill-eval-m4-design.md`, and
the M5 design is in
`docs/superpowers/specs/2026-08-05-skill-eval-m5-design.md`.
```

Add to the "Invariants that are easy to break" list, at the end:

```markdown
- **JUnit reports the candidate arm only, and `<failure>`/`<error>` mirror `failed`/`errored`.**
  A failing baseline is evidence the skill helped, not a red build.
- **JUnit output is always well-formed XML.** `ElementTree` emits control characters raw, so
  illegal characters are stripped before they reach the tree. A zero-case run emits an
  `<error>`, never an empty `tests="0"` suite that would render green against exit code 1.
- **Markdown truncation drops whole detail blocks, never the verdict or a gate reason** — and
  it lives in the renderer, because only the renderer knows where a `<details>` block ends.
- **Reporters never do IO to a service.** The tool renders; the workflow posts.
- **`--concurrency 1` constructs no executor**, outcome order is submission order rather than
  completion order, and an authoring error still aborts the run deterministically. Runners,
  judges and evaluators must have no mutable state touched by `run`/`evaluate`/`judge`.
- **The action re-raises the CLI's exit code after writing the step summary.**
```

Add to the Documentation table:

```markdown
| CI integration, the action, example workflows | `docs/ci.md` |
```

- [ ] **Step 7: Run the full suite and the docs build**

```bash
uv run pytest && uv run mkdocs build --strict && uv run ruff check . && uv run ruff format --check .
```

Expected: all tests pass, the site builds, lint and format are clean.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "docs: document CI integration, the action and the M5 invariants"
```

---

## Final verification

- [ ] **Verify per-skill thresholds, the one M5 item that needs no code**

The roadmap lists per-skill thresholds under M5, but they shipped in an earlier milestone.
Confirm that rather than re-implementing it:

```bash
uv run pytest tests/test_gating.py -k per_skill -v && uv run pytest tests/test_config.py -k per_skill -v && grep -n "per_skill_min" docs/configuration.md
```

Expected: the gating tests covering below-bar, named-but-no-results, and named-but-skipped all
pass; the config tests pass; and `per_skill_min` appears in the configuration docs. If any of
that is missing, it is a real gap and needs its own task — do not silently skip it.

- [ ] **Run everything CI runs**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -v && uv run skill-eval list ./examples && uv run skill-eval list ./skills
```

Expected: lint clean, format clean, all tests pass, both `list` commands print their skills.

- [ ] **Confirm the docs gate is satisfied**

```bash
uv sync --group docs && uv run mkdocs build --strict && uv run pytest tests/test_docs.py -v
```

Expected: the site builds with no warnings-as-errors and every docs test passes.

- [ ] **Open the pull request**

The PR title becomes the commit on `main` under squash-merge, so it must be conventional:

```bash
gh pr create --title "feat: report runs as JUnit and Markdown, run them concurrently, and ship a GitHub Action" --body "$(cat <<'EOF'
## Summary

M5 part 1 from `docs/superpowers/specs/2026-08-05-skill-eval-m5-design.md`: the CI-facing half
of the milestone.

- **JUnit XML reporter** — `--junit-output`. `failed`/`errored` map to `<failure>`/`<error>`,
  so the distinction this project is built around finally renders natively in CI. Candidate
  arm only: a failing baseline is evidence the skill helped, not a red build.
- **Markdown reporter** — `--markdown-output`, with `--markdown-max-chars` for GitHub's
  65,536-character comment cap. Truncation drops whole detail blocks and never the verdict or
  a gate reason.
- **Bounded concurrency** — `--concurrency N`. The orchestrator splits into sequential
  planning and executor-backed execution; `1` constructs no executor and is byte-identical to
  before.
- **Composite GitHub Action** plus example workflows, kept in step with the CLI by
  `tests/test_action.py` and an end-to-end smoke job.

Per-skill thresholds, also listed under M5 in the roadmap, were already shipped in an earlier
milestone; this PR verifies and documents that rather than re-implementing it.

Part 2 — the release pipeline and cassette-refresh workflow — is specified in §10 of the design
and waits on the PyPI project and repository secrets it needs.

## Test plan

- `uv run pytest` — all green, offline, no API key.
- `uv run mkdocs build --strict` and `uv run pytest tests/test_docs.py`.
- The `action-smoke` CI job runs `action.yml` end to end against a fixture skill.
EOF
)"
```

---

## Notes for the implementer

- **`_run_one`, `_baseline_skill` and `_arms` are not modified** by Task 3. Only the code that
  calls them moves. If a diff shows changes inside those three functions, something went wrong.
- **The JSON write-failure message text is load-bearing.** `tests/test_cli.py` asserts
  `"Failed to write JSON report"`, so `_write_report`'s f-string must keep that exact wording
  with `label="JSON"`.
- **Do not add `concurrency` validation to `Config`.** The CLI validates the resolved value so a
  config file and a flag are checked identically — the same shape `repeat` already uses.
- **`examples/` is inert.** GitHub only executes `.github/workflows/`. The example workflows are
  there to be copied and to be snippet-included by the docs.
