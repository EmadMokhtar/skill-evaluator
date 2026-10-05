"""The evals.json converter: the shared format in, raw case mappings out."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_lens.cases.errors import CaseParseError
from skill_lens.cases.evals_json import evals_json_to_raw_cases
from skill_lens.models import Skill

PATH = Path("evals/evals.json")


def _skill(tmp_path, name="csv-analyzer") -> Skill:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    return Skill(name=name, description="", instructions="", path=skill_dir)


def _eval(**overrides):
    base = {"id": 1, "prompt": "do the thing", "assertions": ["it did the thing"]}
    base.update(overrides)
    return base


def _doc(*evals, **top):
    return {"skill_name": "csv-analyzer", "evals": list(evals), **top}


def _convert(data, skill=None):
    return evals_json_to_raw_cases(PATH, data, skill)


# --- the mapping ---------------------------------------------------------------


def test_an_eval_maps_to_a_case_with_a_judge_block():
    data = _doc(_eval(expected_output="A bar chart.", assertions=["axes labelled", "3 bars"]))
    assert _convert(data) == [
        {
            "name": "eval-1",
            "task": "do the thing",
            "judge": {"expected": "A bar chart.", "rubric": ["axes labelled", "3 bars"]},
        }
    ]


def test_expectations_is_accepted_as_the_same_list():
    entry = {"id": 1, "prompt": "p", "expectations": ["a", "b"]}
    [case] = _convert(_doc(entry))
    assert case["judge"]["rubric"] == ["a", "b"]


def test_both_spellings_in_one_eval_are_refused():
    entry = _eval(expectations=["b"])
    with pytest.raises(CaseParseError, match="both 'assertions' and 'expectations'"):
        _convert(_doc(entry))


def test_expected_output_alone_becomes_the_single_check():
    entry = {"id": 2, "prompt": "p", "expected_output": "  A cleaned CSV.  "}
    [case] = _convert(_doc(entry))
    assert case["judge"] == {
        "expected": "  A cleaned CSV.  ",
        "rubric": ["The output satisfies: A cleaned CSV."],
    }


def test_an_empty_statement_list_falls_back_to_expected_output():
    entry = _eval(assertions=[], expected_output="Done.")
    [case] = _convert(_doc(entry))
    assert case["judge"]["rubric"] == ["The output satisfies: Done."]


@pytest.mark.parametrize("expected", [None, "", "   "])
def test_an_eval_with_nothing_to_grade_is_refused(expected):
    entry = {"id": 1, "prompt": "p"}
    if expected is not None:
        entry["expected_output"] = expected
    with pytest.raises(CaseParseError, match="nothing to grade"):
        _convert(_doc(entry))


# --- strict keys ---------------------------------------------------------------


def test_an_unknown_top_level_key_is_refused_and_named():
    with pytest.raises(CaseParseError, match="the top level has unknown key.*'trigger'"):
        _convert(_doc(_eval(), trigger={"positive": []}))


def test_an_unknown_per_case_key_is_refused_and_named():
    with pytest.raises(CaseParseError, match=r"eval #1 has unknown key.*'kind'"):
        _convert(_doc(_eval(kind="dialogue")))


def test_errors_name_the_file():
    with pytest.raises(CaseParseError) as caught:
        _convert(_doc(_eval(kind="dialogue")))
    assert str(PATH) in str(caught.value)


# --- ids -----------------------------------------------------------------------


@pytest.mark.parametrize(("ident", "name"), [(1, "eval-1"), (0, "eval-0"), ("a-b", "eval-a-b")])
def test_an_id_becomes_the_case_name(ident, name):
    [case] = _convert(_doc(_eval(id=ident)))
    assert case["name"] == name


@pytest.mark.parametrize("bad", [True, False, 1.5, "", "  ", None, [], {}])
def test_an_id_must_be_an_integer_or_a_non_empty_string(bad):
    with pytest.raises(CaseParseError, match="'id' must be an integer or a non-empty string"):
        _convert(_doc(_eval(id=bad)))


def test_a_missing_id_is_refused():
    entry = {"prompt": "p", "assertions": ["a"]}
    with pytest.raises(CaseParseError, match="has no 'id'"):
        _convert(_doc(entry))


def test_a_repeated_id_is_refused_even_across_int_and_string():
    with pytest.raises(CaseParseError, match="repeats id"):
        _convert(_doc(_eval(id=1), _eval(id="1")))


# --- prompt, statements, expected_output ----------------------------------------


@pytest.mark.parametrize("bad", [None, "", "   ", 3, ["x"]])
def test_a_prompt_must_be_a_non_empty_string(bad):
    entry = _eval()
    entry["prompt"] = bad
    with pytest.raises(CaseParseError, match="'prompt' must be a non-empty string"):
        _convert(_doc(entry))


def test_a_missing_prompt_is_refused():
    with pytest.raises(CaseParseError, match="'prompt' must be a non-empty string"):
        _convert(_doc({"id": 1, "assertions": ["a"]}))


@pytest.mark.parametrize("bad", ["a string", [""], ["ok", "  "], [1], {"a": "b"}])
def test_statements_must_be_a_list_of_non_empty_strings(bad):
    with pytest.raises(CaseParseError, match="must be a list of non-empty strings"):
        _convert(_doc(_eval(assertions=bad)))


def test_expected_output_must_be_a_string():
    with pytest.raises(CaseParseError, match="'expected_output' must be a string"):
        _convert(_doc(_eval(expected_output=3)))


# --- the document's own shape ---------------------------------------------------


def test_a_bare_list_is_refused_with_the_expected_shape():
    with pytest.raises(CaseParseError, match="expected a JSON object with an 'evals' list"):
        _convert([_eval()])


@pytest.mark.parametrize("bad", [None, "x", {"id": 1}])
def test_evals_must_be_a_list(bad):
    with pytest.raises(CaseParseError, match="'evals' must be a list"):
        _convert({"skill_name": "csv-analyzer", "evals": bad})


def test_a_document_without_evals_is_refused():
    with pytest.raises(CaseParseError, match="'evals' must be a list"):
        _convert({"skill_name": "csv-analyzer"})


def test_an_empty_evals_list_is_refused():
    with pytest.raises(CaseParseError, match="'evals' is empty"):
        _convert(_doc())


def test_an_eval_must_be_an_object():
    with pytest.raises(CaseParseError, match=r"eval #2 must be a JSON object"):
        _convert(_doc(_eval(), "not an object"))


# --- skill_name -----------------------------------------------------------------


def test_skill_name_must_match_the_skill(tmp_path):
    skill = _skill(tmp_path, "csv-analyzer")
    assert len(_convert(_doc(_eval()), skill)) == 1
    with pytest.raises(CaseParseError, match="belongs to another skill"):
        _convert(_doc(_eval(), skill_name="pdf"), skill)


def test_skill_name_is_not_compared_without_a_skill():
    assert len(_convert(_doc(_eval(), skill_name="anything"))) == 1


def test_skill_name_is_optional(tmp_path):
    data = {"evals": [_eval()]}
    assert len(_convert(data, _skill(tmp_path))) == 1


def test_skill_name_must_be_a_string():
    with pytest.raises(CaseParseError, match="'skill_name' must be a string"):
        _convert(_doc(_eval(), skill_name=3))


# --- input files (filled in by the next task) ------------------------------------


def test_no_workspace_block_without_files():
    [case] = _convert(_doc(_eval()))
    assert "workspace" not in case
    [case] = _convert(_doc(_eval(files=[])))
    assert "workspace" not in case
