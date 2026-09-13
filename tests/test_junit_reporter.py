"""JUnit XML reporter tests.

Every assertion parses the rendered document rather than matching substrings:
the whole point of this reporter is that a CI system can read it, so a test
that only greps the text would pass on output no parser accepts.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from skill_lens.gating import evaluate_gate
from skill_lens.models import (
    CaseOutcome,
    CheckResult,
    EvalScore,
    RunReport,
    RunResult,
    ScriptStatus,
    ToolCall,
)
from skill_lens.reporters.junit import render_junit


def _outcome(
    name="extracts", status="passed", arm="candidate", repeat_index=0, runner="fake", **kwargs
):
    scores = kwargs.pop("scores", [EvalScore(evaluator="assertion", passed=True, score=1.0)])
    result = kwargs.pop("result", RunResult(output="yes", latency_ms=800))
    return CaseOutcome(
        skill_name=kwargs.pop("skill_name", "pdf"),
        case_name=name,
        runner=runner,
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
    # The body now carries the output excerpt after the head line, so only
    # the head -- what the control-character stripping is about -- is
    # checked here.
    assert root.find("testsuite/testcase/error").text.split("\n")[0] == "boom"


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


def test_two_runners_for_one_case_get_distinct_identities():
    """Consumers key on classname+name and silently collapse duplicates, so a
    skill x case x runner matrix must not emit the same identity twice."""
    report = RunReport(
        outcomes=[_outcome(name="extracts"), _outcome(name="extracts", runner="pydantic-ai")]
    )
    names = [c.get("name") for c in _parse(report).findall("testsuite/testcase")]
    assert names == ["extracts (fake)", "extracts (pydantic-ai)"]
    assert len(set(names)) == 2


def test_a_single_runner_keeps_clean_names():
    report = RunReport(outcomes=[_outcome(name="extracts")])
    assert _parse(report).find("testsuite/testcase").get("name") == "extracts"


def _failed(output, tool_calls=()):
    return _outcome(
        name="rejects",
        status="failed",
        scores=[EvalScore(evaluator="assertion", passed=False, detail="nope")],
        result=RunResult(output=output, tool_calls=list(tool_calls)),
    )


def test_a_failure_body_carries_the_output_after_the_detail():
    root = _parse(RunReport(outcomes=[_failed("I cannot refund order 1234.")]))
    failure = root.find("testsuite/testcase/failure")
    assert failure.text.split("\n")[0] == "assertion: nope"
    assert "output:\nI cannot refund order 1234." in failure.text
    # The attribute stays the one-line summary.
    assert failure.get("message") == "assertion: nope"


def test_an_error_body_carries_the_output_too():
    outcome = _outcome(
        name="boom",
        status="errored",
        scores=[],
        result=RunResult(output="partial answer", error="provider returned 500"),
    )
    root = _parse(RunReport(outcomes=[outcome]))
    error = root.find("testsuite/testcase/error")
    assert error.text.startswith("provider returned 500")
    assert "output:\npartial answer" in error.text


def test_tool_calls_and_the_cut_note_reach_the_body():
    calls = [ToolCall(name="lookup_order", arguments={"order_id": "1234"})]
    root = _parse(RunReport(outcomes=[_failed("x" * 2342, calls)]))
    text = root.find("testsuite/testcase/failure").text
    assert "… (1,842 more characters; --full-output prints them)" in text
    assert 'tool calls:\nlookup_order(order_id="1234")' in text


def test_no_output_limit_puts_the_whole_output_in_the_body():
    root = _parse(RunReport(outcomes=[_failed("x" * 2342)]), output_limit=None)
    assert "x" * 2342 in root.find("testsuite/testcase/failure").text


def test_control_characters_in_the_output_are_stripped_so_the_document_parses():
    root = _parse(RunReport(outcomes=[_failed("bad\x00byte\x01here")]))
    assert "badbytehere" in root.find("testsuite/testcase/failure").text


def test_an_empty_output_is_stated_in_the_body():
    root = _parse(RunReport(outcomes=[_failed("")]))
    assert "output:\n(empty)" in root.find("testsuite/testcase/failure").text


def test_a_trailing_newline_does_not_leave_a_blank_line_in_the_body():
    root = _parse(RunReport(outcomes=[_failed("done.\n")]))
    assert "output:\ndone." in root.find("testsuite/testcase/failure").text
    assert "done.\n\n" not in root.find("testsuite/testcase/failure").text


def test_case_filtered_skills_become_skipped_suites():
    root = _parse(RunReport(outcomes=[_outcome()], case_filtered_skills=["xlsx"]))
    names = [s.get("name") for s in root.findall("testsuite")]
    assert "xlsx" in names
    skipped = root.find("testsuite[@name='xlsx']/testcase/skipped")
    assert skipped is not None
    assert "--case" in skipped.get("message")


def test_the_sandbox_is_a_suite_property_when_scripts_ran():
    report = RunReport(
        outcomes=[_outcome()],
        scripts=ScriptStatus(sandbox="sandbox-exec", detail="sandbox-exec probe succeeded"),
    )
    root = _parse(report)
    suite = root.find("testsuite")
    prop = suite.find("properties/property")
    assert prop is not None
    assert prop.get("name") == "skill-lens.scripts.sandbox"
    assert prop.get("value") == "sandbox-exec"


def test_no_properties_element_when_scripts_are_off():
    root = _parse(RunReport(outcomes=[_outcome()]))
    assert root.find("testsuite/properties") is None


_SCRIPTS = ScriptStatus(sandbox="bwrap", detail="bwrap probe succeeded")


def _property_names(suite):
    return [prop.get("name") for prop in suite.findall("properties/property")]


def test_every_kind_of_suite_carries_the_script_properties_first():
    # docs/gating.md promises "every skill's <testsuite>": a skipped, tag-
    # filtered or case-filtered skill is a suite too, and <properties> must
    # be its first child -- the JUnit schema puts it before any <testcase>.
    report = RunReport(
        outcomes=[_outcome()],
        skipped_skills=["docx"],
        tag_filtered_skills=["xlsx"],
        case_filtered_skills=["pptx"],
        scripts=_SCRIPTS,
    )
    root = _parse(report)
    suites = root.findall("testsuite")
    assert [s.get("name") for s in suites] == ["pdf", "docx", "xlsx", "pptx"]
    for suite in suites:
        assert suite[0].tag == "properties", suite.get("name")
        assert _property_names(suite) == [
            "skill-lens.scripts.sandbox",
            "skill-lens.scripts.detail",
        ]


def test_the_zero_case_error_suite_carries_the_script_properties_first():
    # Preflight ran (scripts is set) and then nothing was executed: the one
    # synthetic suite is still a suite of this run and says so.
    root = _parse(RunReport(outcomes=[], scripts=_SCRIPTS), gate=evaluate_gate(RunReport()))
    (suite,) = root.findall("testsuite")
    assert suite[0].tag == "properties"
    assert _property_names(suite) == ["skill-lens.scripts.sandbox", "skill-lens.scripts.detail"]
    assert suite.find("testcase/error") is not None


def test_no_suite_of_any_kind_carries_properties_when_scripts_are_off():
    report = RunReport(
        outcomes=[_outcome()],
        skipped_skills=["docx"],
        tag_filtered_skills=["xlsx"],
        case_filtered_skills=["pptx"],
    )
    assert _parse(report).findall("testsuite/properties") == []
    assert _parse(RunReport(outcomes=[])).findall("testsuite/properties") == []
