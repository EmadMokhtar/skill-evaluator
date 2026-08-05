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


def test_an_errored_evaluator_reports_its_own_diagnostic():
    """`errored` covers an evaluator that blew up as well as a runner that did.

    Only the runner sets `RunResult.error`, so reading only that would discard
    a judge failure's message and blame the runner for it.
    """
    report = RunReport(
        outcomes=[
            _outcome(
                name="judged",
                status="errored",
                scores=[
                    EvalScore(
                        evaluator="judge",
                        passed=False,
                        errored=True,
                        detail="judge failed: connection reset",
                    )
                ],
                result=RunResult(output="ok", latency_ms=500),
            )
        ]
    )
    error = _parse(report).find("testsuite/testcase/error")
    assert "judge: judge failed: connection reset" in error.text
    assert "runner" not in error.text
