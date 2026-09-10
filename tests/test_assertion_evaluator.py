import json

import pytest

from skill_lens.cases.loader import _ASSERTION_FIELDS
from skill_lens.evaluators.assertion import (
    ASSERTION_KINDS,
    AssertionEvaluator,
    InvalidAssertionValue,
    UnknownAssertionKind,
)
from skill_lens.evaluators.base import Evaluator
from skill_lens.models import AssertionSpec, EvalCase, RunResult, WorkspaceSpec


def test_assertion_evaluator_satisfies_the_evaluator_protocol():
    """Item 8: Evaluator is @runtime_checkable but nothing exercised isinstance
    against it, so protocol drift would not be caught when M2's adapters
    land. Lock in that AssertionEvaluator actually satisfies the protocol.
    """
    assert isinstance(AssertionEvaluator(), Evaluator)


def test_contains_passes_when_substring_present():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="contains", value="pdfplumber")),
        RunResult(output="I used pdfplumber to extract."),
    )
    assert score.passed is True
    assert score.score == 1.0
    assert score.evaluator == "assertion"


def test_contains_fails_and_explains_when_missing():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="contains", value="pdfplumber")),
        RunResult(output="I used something else."),
    )
    assert score.passed is False
    assert "pdfplumber" in score.detail


def test_not_contains_passes_when_absent():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="not_contains", value="traceback")),
        RunResult(output="all good"),
    )
    assert score.passed is True


def test_not_contains_fails_when_present():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="not_contains", value="traceback")),
        RunResult(output="traceback: boom"),
    )
    assert score.passed is False


def test_regex_matches():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="regex", value=r"\d+ pages")),
        RunResult(output="found 12 pages"),
    )
    assert score.passed is True


def test_equals_is_exact_after_strip():
    evaluator = AssertionEvaluator()
    assert (
        evaluator.evaluate(
            _case(AssertionSpec(kind="equals", value="done")), RunResult(output="  done  ")
        ).passed
        is True
    )
    assert (
        evaluator.evaluate(
            _case(AssertionSpec(kind="equals", value="done")), RunResult(output="done!")
        ).passed
        is False
    )


def test_all_assertions_must_pass():
    score = AssertionEvaluator().evaluate(
        _case(
            AssertionSpec(kind="contains", value="a"),
            AssertionSpec(kind="contains", value="zzz"),
        ),
        RunResult(output="a b c"),
    )
    assert score.passed is False
    assert score.score == 0.5


def test_case_with_no_assertions_passes_vacuously():
    assert AssertionEvaluator().evaluate(_case(), RunResult(output="x")).passed is True


def test_unknown_kind_raises():
    with pytest.raises(UnknownAssertionKind, match="nonsense"):
        AssertionEvaluator().evaluate(
            _case(AssertionSpec(kind="nonsense", value="x")), RunResult(output="y")
        )


def test_invalid_regex_raises_invalid_assertion_value():
    from skill_lens.evaluators.assertion import InvalidAssertionValue

    with pytest.raises(InvalidAssertionValue, match=r"\[unclosed"):
        AssertionEvaluator().evaluate(
            _case(AssertionSpec(kind="regex", value="[unclosed")), RunResult(output="x")
        )


def test_assertion_kinds_lists_every_supported_kind():
    """ASSERTION_KINDS is the single source of truth for supported kinds.

    The docs test in tests/test_docs.py enumerates this tuple, so a kind that
    dispatches but is missing here would ship undocumented.
    """
    assert ASSERTION_KINDS == (
        "contains",
        "not_contains",
        "regex",
        "equals",
        "file-produced",
        "json-schema",
    )


def test_every_listed_kind_actually_dispatches(tmp_path):
    """No entry in ASSERTION_KINDS may raise UnknownAssertionKind.

    `file-produced` and `json-schema` need their own fixtures because, unlike
    the text kinds, they do not accept a bare `value=` against a workspaceless
    result -- the loader's `_ASSERTION_FIELDS` table (Task 5) requires a
    `file` for one and a `json_schema` for the other.
    """
    (tmp_path / "report.md").write_text("x", encoding="utf-8")
    result = RunResult(output="x", workspace=tmp_path.resolve())
    for kind in ASSERTION_KINDS:
        if kind == "file-produced":
            spec = AssertionSpec(kind=kind, file="report.md")
        elif kind == "json-schema":
            spec = AssertionSpec(kind=kind, json_schema={"type": "string"})
        else:
            spec = AssertionSpec(kind=kind, value="x")
        case = EvalCase(name="c", task="t", assertions=[spec])
        # Must not raise; pass/fail is irrelevant here.
        AssertionEvaluator().evaluate(case, result)


def test_each_assertion_gets_its_own_check():
    case = EvalCase(
        name="c",
        task="t",
        assertions=[
            AssertionSpec(kind="contains", value="yes"),
            AssertionSpec(kind="contains", value="never"),
        ],
    )
    score = AssertionEvaluator().evaluate(case, RunResult(output="yes indeed"))

    assert [(c.id, c.passed) for c in score.checks] == [
        ("contains[0]", True),
        ("contains[1]", False),
    ]


def test_check_ids_are_positional_so_they_pair_across_arms():
    # Two assertions of the same kind must not collide, or a low-signal report
    # cannot say which one is dead weight.
    case = EvalCase(
        name="c",
        task="t",
        assertions=[
            AssertionSpec(kind="contains", value="a"),
            AssertionSpec(kind="contains", value="b"),
        ],
    )
    score = AssertionEvaluator().evaluate(case, RunResult(output="a b"))
    assert [c.id for c in score.checks] == ["contains[0]", "contains[1]"]


def test_every_check_carries_evidence():
    case = EvalCase(name="c", task="t", assertions=[AssertionSpec(kind="contains", value="never")])
    score = AssertionEvaluator().evaluate(case, RunResult(output="nope"))
    assert score.checks[0].evidence
    assert "never" in score.checks[0].evidence


def test_a_case_with_no_assertions_has_no_checks():
    score = AssertionEvaluator().evaluate(EvalCase(name="c", task="t"), RunResult(output="x"))
    assert score.checks == []
    assert score.passed is True


def _case(*assertions: AssertionSpec) -> EvalCase:
    return EvalCase(name="n", task="t", workspace=WorkspaceSpec(), assertions=list(assertions))


def _result(tmp_path, **files: str) -> RunResult:
    for name, content in files.items():
        (tmp_path / name.replace("__", ".")).write_text(content, encoding="utf-8")
    return RunResult(output="chat output", workspace=tmp_path.resolve())


def test_the_requirements_table_covers_exactly_the_known_kinds():
    # Two different facts about the same kinds live in two modules: the
    # evaluator maps kind -> predicate, the loader maps kind -> required
    # fields. This is what keeps them from drifting apart.
    assert set(_ASSERTION_FIELDS) == set(ASSERTION_KINDS)


def test_file_produced_passes_when_the_file_is_there(tmp_path):
    result = _result(tmp_path, report__md="body")
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="file-produced", file="report.md")), result
    )
    assert score.passed


def test_file_produced_fails_and_lists_what_was_actually_there(tmp_path):
    # The common authoring mistake is a filename off by a character, and this
    # listing is the whole diagnostic story for it.
    #
    # Deliberately NOT a case-only difference. macOS's default filesystem is
    # case-insensitive, so `report.MD` and `report.md` are the same file
    # there: a case-only test would pass in Linux CI while failing on every
    # developer's Mac, which trains people to ignore a red suite.
    result = _result(tmp_path, reports__md="body", notes__txt="x")
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="file-produced", file="report.md")), result
    )
    assert not score.passed
    assert "reports.md" in score.detail
    assert "notes.txt" in score.detail


def test_the_listing_elides_behind_a_truthful_count(tmp_path):
    for index in range(30):
        (tmp_path / f"f{index:02d}.txt").write_text("x", encoding="utf-8")
    result = RunResult(output="", workspace=tmp_path.resolve())
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="file-produced", file="missing.md")), result
    )
    assert "+10 more" in score.detail


def test_contains_reads_the_file_when_given_one(tmp_path):
    result = _result(tmp_path, report__md="the north region")
    case = _case(AssertionSpec(kind="contains", value="north", file="report.md"))
    assert AssertionEvaluator().evaluate(case, result).passed


def test_contains_still_reads_the_output_when_given_no_file(tmp_path):
    result = _result(tmp_path, report__md="nothing useful")
    case = _case(AssertionSpec(kind="contains", value="chat"))
    assert AssertionEvaluator().evaluate(case, result).passed


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("contains", "north", True),
        ("not_contains", "south", True),
        ("regex", r"nor\w+", True),
        ("equals", "the north region", True),
        ("contains", "south", False),
    ],
)
def test_every_text_kind_works_against_a_file(tmp_path, kind, value, expected):
    result = _result(tmp_path, report__md="the north region")
    case = _case(AssertionSpec(kind=kind, value=value, file="report.md"))
    assert AssertionEvaluator().evaluate(case, result).passed is expected


def test_a_missing_file_fails_a_text_assertion_rather_than_erroring(tmp_path):
    result = RunResult(output="", workspace=tmp_path.resolve())
    case = _case(AssertionSpec(kind="contains", value="x", file="gone.md"))
    score = AssertionEvaluator().evaluate(case, result)
    assert not score.passed
    assert not score.errored


def test_a_non_utf8_file_fails_rather_than_erroring(tmp_path):
    # The skill produced a file the eval cannot read. That is a fact about the
    # skill, not about the harness.
    (tmp_path / "blob.dat").write_bytes(b"\xff\xfe\x00")
    result = RunResult(output="", workspace=tmp_path.resolve())
    case = _case(AssertionSpec(kind="contains", value="x", file="blob.dat"))
    score = AssertionEvaluator().evaluate(case, result)
    assert not score.passed
    assert not score.errored
    assert "UTF-8" in score.detail


def test_json_schema_passes_on_a_conforming_document(tmp_path):
    result = _result(tmp_path, totals__json=json.dumps({"units": 200}))
    case = _case(
        AssertionSpec(
            kind="json-schema",
            file="totals.json",
            json_schema={
                "type": "object",
                "required": ["units"],
                "properties": {"units": {"type": "integer"}},
            },
        )
    )
    assert AssertionEvaluator().evaluate(case, result).passed


def test_json_schema_fails_and_says_where(tmp_path):
    result = _result(tmp_path, totals__json=json.dumps({"units": "lots"}))
    case = _case(
        AssertionSpec(
            kind="json-schema",
            file="totals.json",
            json_schema={
                "type": "object",
                "properties": {"units": {"type": "integer"}},
            },
        )
    )
    score = AssertionEvaluator().evaluate(case, result)
    assert not score.passed
    assert "units" in score.detail


def test_json_schema_fails_on_text_that_is_not_json(tmp_path):
    result = _result(tmp_path, totals__json="not json at all")
    case = _case(
        AssertionSpec(kind="json-schema", file="totals.json", json_schema={"type": "object"})
    )
    score = AssertionEvaluator().evaluate(case, result)
    assert not score.passed
    assert not score.errored


def test_json_schema_with_no_file_validates_the_output_text():
    result = RunResult(output='{"units": 3}')
    case = EvalCase(
        name="n",
        task="t",
        assertions=[AssertionSpec(kind="json-schema", json_schema={"type": "object"})],
    )
    assert AssertionEvaluator().evaluate(case, result).passed


def test_a_file_assertion_with_no_workspace_is_an_authoring_error():
    # Only reachable from a programmatically built EvalCase: the loader
    # rejects this first.
    case = EvalCase(
        name="n",
        task="t",
        assertions=[AssertionSpec(kind="contains", value="x", file="r.md")],
    )
    with pytest.raises(InvalidAssertionValue):
        AssertionEvaluator().evaluate(case, RunResult(output="x"))


@pytest.mark.parametrize("escaping", ["../escape.md", "/etc/passwd"])
@pytest.mark.parametrize("kind", ["contains", "file-produced"])
def test_a_refused_path_is_an_authoring_error_not_a_failure(tmp_path, kind, escaping):
    # The other half of the fail-vs-raise rule: a MISSING file fails (a fact
    # about the skill), but a path that escapes the workspace is the author's
    # mistake and must abort the run. Without this, someone could later
    # "simplify" _subject_text into swallowing PathRefused as a plain failure
    # and nothing would notice.
    result = RunResult(output="", workspace=tmp_path.resolve())
    spec = (
        AssertionSpec(kind=kind, file=escaping)
        if kind == "file-produced"
        else AssertionSpec(kind=kind, value="x", file=escaping)
    )
    with pytest.raises(InvalidAssertionValue):
        AssertionEvaluator().evaluate(_case(spec), result)


def test_json_schema_without_a_schema_is_an_authoring_error(tmp_path):
    # Reachable only by building an EvalCase directly; the loader requires the
    # field. Draft202012Validator(None) would otherwise raise a bare
    # AttributeError, which is neither a failure nor an errored case.
    result = RunResult(output="{}", workspace=tmp_path.resolve())
    with pytest.raises(InvalidAssertionValue):
        AssertionEvaluator().evaluate(_case(AssertionSpec(kind="json-schema")), result)


def test_check_ids_stay_positional_and_zero_based(tmp_path):
    # M4 pairs assertions across arms by id. A new scheme would silently break
    # that pairing and the low-signal-assertion report with it.
    result = _result(tmp_path, report__md="north")
    case = _case(
        AssertionSpec(kind="contains", value="chat"),
        AssertionSpec(kind="file-produced", file="report.md"),
    )
    score = AssertionEvaluator().evaluate(case, result)
    assert [check.id for check in score.checks] == ["contains[0]", "file-produced[1]"]
