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
_ILLEGAL_XML = re.compile("[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")

# Some CI UIs render `message` in a fixed-width column; the full text is in the
# element body either way.
_MESSAGE_LIMIT = 1000


def _xml_safe(text: str) -> str:
    return _ILLEGAL_XML.sub("", text)


def _message(body: str) -> str:
    """The attribute form of a body: its first line, capped.

    `str.splitlines()` also breaks on exotic separators (`\x0b`, `\x0c`,
    `\x1c`-`\x1e`, `\x85`, U+2028, U+2029) that are not line breaks as far as
    the element body is concerned -- splitting on those would cut the
    attribute short of what the body itself shows.
    """
    first = body.split("\n")[0] if body else ""
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


def _error_body(outcome: CaseOutcome) -> str:
    """Why this case errored -- from the runner, or from an evaluator.

    `errored` covers both: a runner that blew up, and an evaluator that did
    (a judge endpoint returning 500). Only the runner sets `RunResult.error`,
    so reading only that would discard an evaluator's diagnostic and pin the
    blame on the wrong component.
    """
    if outcome.result is not None and outcome.result.error:
        return outcome.result.error
    details = [f"{score.evaluator}: {score.detail}" for score in outcome.scores if score.errored]
    return "\n".join(details) if details else "no detail was reported"


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
        name=_xml_safe(case_name),
        time="0.000",
    )
    SubElement(case, "skipped", message=_xml_safe(reason))


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
                body = _error_body(outcome)
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
            "\n".join(gate.reasons) if gate is not None and gate.reasons else "no eval cases ran"
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
