"""The generated suite must be real YAML, refuse to run, and run once filled in."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_eval.cases.loader import UNFILLED_SENTINEL, CaseParseError, parse_cases_file
from skill_eval.models import Skill
from skill_eval.scaffold import render_scaffold
from skill_eval.yaml_loading import safe_load

SKILL = Skill(
    name="order-support",
    description="Handle customer refund requests against the 30-day return policy",
    instructions="Always call lookup_order first.",
    path=Path("order-support"),
)


def test_the_scaffold_names_the_skill_and_quotes_its_description():
    text = render_scaffold(SKILL)
    assert "order-support" in text
    assert "Handle customer refund requests against the 30-day return policy" in text


def test_the_scaffold_is_valid_yaml_with_four_cases():
    data = safe_load(render_scaffold(SKILL))
    assert len(data["cases"]) == 4


def test_the_scaffold_ships_both_halves_of_the_triggering_pair():
    data = safe_load(render_scaffold(SKILL))
    triggered = [
        case["trajectory"]["skill_triggered"]
        for case in data["cases"]
        if case.get("mode") == "offered"
    ]
    assert sorted(triggered) == [False, True]


def test_every_scaffold_case_carries_a_placeholder():
    data = safe_load(render_scaffold(SKILL))
    for case in data["cases"]:
        assert UNFILLED_SENTINEL in str(case), case["name"]


def test_a_fresh_scaffold_refuses_to_load(tmp_path):
    path = tmp_path / "order-support.eval.yaml"
    path.write_text(render_scaffold(SKILL), encoding="utf-8")
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path, SKILL)
    assert UNFILLED_SENTINEL in str(exc.value)


@pytest.mark.parametrize(
    "replacement",
    [
        pytest.param("the customer's order", id="apostrophe"),
        pytest.param('a "priority" ticket', id="double-quote"),
        pytest.param("refund order 1234: approved", id="colon-space"),
    ],
)
def test_a_filled_scaffold_loads_clean(tmp_path, replacement):
    # Substituting any real text for the placeholder must be all it takes: if
    # the generated file were malformed in some other way -- a trajectory
    # naming an undeclared tool, `skill_triggered` on a loaded case -- the
    # cross-reference checks would catch it here. Real authors type
    # apostrophes, quotes, and colons far more often than anything exotic, so
    # those are exactly the characters the template must survive.
    path = tmp_path / "order-support.eval.yaml"
    filled = render_scaffold(SKILL).replace(UNFILLED_SENTINEL, replacement)
    path.write_text(filled, encoding="utf-8")
    cases = parse_cases_file(path, SKILL)
    assert len(cases) == 4
    assert [case.mode for case in cases] == ["loaded", "loaded", "offered", "offered"]


def test_no_scaffold_case_is_assertion_free_unless_it_checks_triggering():
    # A case with no assertions passes vacuously; the generated loaded cases
    # must never model that.
    data = safe_load(render_scaffold(SKILL))
    for case in data["cases"]:
        if case.get("mode") == "offered":
            continue
        assert case["assertions"], case["name"]
