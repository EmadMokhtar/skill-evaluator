"""The generated suite must be real YAML, refuse to run, and run once filled in."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_lens.cases.loader import UNFILLED_SENTINEL, CaseParseError, parse_cases_file
from skill_lens.models import Skill
from skill_lens.scaffold import render_scaffold
from skill_lens.yaml_loading import safe_load

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


def test_the_scaffold_is_valid_yaml_with_five_cases():
    data = safe_load(render_scaffold(SKILL))
    assert len(data["cases"]) == 5


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
    assert len(cases) == 5
    assert [case.mode for case in cases] == ["loaded", "loaded", "offered", "offered", "loaded"]


def test_no_scaffold_case_is_assertion_free_unless_it_checks_triggering():
    # A case with no assertions passes vacuously; the generated loaded cases
    # must never model that.
    data = safe_load(render_scaffold(SKILL))
    for case in data["cases"]:
        if case.get("mode") == "offered":
            continue
        assert case["assertions"], case["name"]


def test_scaffold_header_mentions_indentation():
    text = render_scaffold(SKILL)
    assert "indentation" in text.lower()
    assert "continuation" in text.lower() or ">-" in text


def test_the_fifth_case_exercises_the_workspace_and_the_file_assertions():
    data = safe_load(render_scaffold(SKILL))
    case = data["cases"][4]
    assert "input.txt" in case["workspace"]["files"]
    assert [a["kind"] for a in case["assertions"]] == ["file-produced", "contains"]
    assert all("file" in a for a in case["assertions"])


def test_the_fifth_case_says_when_to_delete_it():
    assert "Delete this case if the skill produces no files" in render_scaffold(SKILL)


def test_no_placeholder_sits_in_a_mapping_key():
    # Task 8 made the loader refuse keys too; the template must still be
    # refused for its *values* and never rely on a key to carry the marker.
    data = safe_load(render_scaffold(SKILL))

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    assert not any(UNFILLED_SENTINEL in str(k) for k in keys(data))
