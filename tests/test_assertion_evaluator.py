import pytest

from skill_eval.evaluators.assertion import AssertionEvaluator, UnknownAssertionKind
from skill_eval.evaluators.base import Evaluator
from skill_eval.models import AssertionSpec, EvalCase, RunResult


def _case(*specs):
    return EvalCase(name="c", task="t", assertions=list(specs))


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
    from skill_eval.evaluators.assertion import InvalidAssertionValue

    with pytest.raises(InvalidAssertionValue, match=r"\[unclosed"):
        AssertionEvaluator().evaluate(
            _case(AssertionSpec(kind="regex", value="[unclosed")), RunResult(output="x")
        )
