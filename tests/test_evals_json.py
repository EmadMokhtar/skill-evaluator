"""The evals.json converter: the shared format in, raw case mappings out."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from promptly import promptly
from skill_lens.cases.errors import CaseParseError
from skill_lens.cases.evals_json import evals_json_to_raw_cases
from skill_lens.models import Skill
from skill_lens.workspace import DEFAULT_LIMITS

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


# --- input files ----------------------------------------------------------------

posix_only = pytest.mark.skipif(os.name == "nt", reason="FIFOs and symlinks are POSIX features")


def _with_file(tmp_path, relative, content="a,b\n1,2\n"):
    skill = _skill(tmp_path)
    target = skill.path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")
    return skill


def test_no_workspace_block_without_files():
    [case] = _convert(_doc(_eval()))
    assert "workspace" not in case
    [case] = _convert(_doc(_eval(files=[])))
    assert "workspace" not in case


def test_a_file_is_read_as_text_and_keyed_by_the_path_as_written(tmp_path):
    skill = _with_file(tmp_path, "evals/files/sales.csv")
    [case] = _convert(_doc(_eval(files=["evals/files/sales.csv"])), skill)
    assert case["workspace"] == {"files": {"evals/files/sales.csv": "a,b\n1,2\n"}}


def test_several_files_are_all_read(tmp_path):
    skill = _with_file(tmp_path, "evals/files/a.txt", "A")
    (skill.path / "evals/files/b.txt").write_text("B", encoding="utf-8")
    [case] = _convert(_doc(_eval(files=["evals/files/a.txt", "evals/files/b.txt"])), skill)
    assert case["workspace"]["files"] == {"evals/files/a.txt": "A", "evals/files/b.txt": "B"}


def test_files_must_be_a_list_of_paths():
    for bad in ("evals/a.csv", [1], [None]):
        with pytest.raises(CaseParseError, match="'files' must be a list of paths"):
            _convert(_doc(_eval(files=bad)))


def test_files_without_a_skill_are_refused():
    with pytest.raises(CaseParseError, match="no skill directory was given"):
        _convert(_doc(_eval(files=["evals/files/a.csv"])))


def test_a_missing_file_is_refused(tmp_path):
    skill = _skill(tmp_path)
    with pytest.raises(CaseParseError, match="does not exist"):
        _convert(_doc(_eval(files=["evals/files/nope.csv"])), skill)


def test_a_directory_entry_is_refused(tmp_path):
    skill = _with_file(tmp_path, "evals/files/a.csv")
    with pytest.raises(CaseParseError, match="is a directory"):
        _convert(_doc(_eval(files=["evals/files"])), skill)


def test_a_binary_file_is_refused(tmp_path):
    skill = _with_file(tmp_path, "evals/files/chart.png", b"\x89PNG\xff\xfe\x00")
    with pytest.raises(CaseParseError, match="not UTF-8 text"):
        _convert(_doc(_eval(files=["evals/files/chart.png"])), skill)


def test_a_file_over_the_size_limit_is_refused(tmp_path):
    skill = _with_file(tmp_path, "evals/files/big.txt", "x" * (DEFAULT_LIMITS.max_file_bytes + 1))
    with pytest.raises(CaseParseError, match="too large"):
        _convert(_doc(_eval(files=["evals/files/big.txt"])), skill)


def test_a_file_exactly_at_the_size_limit_is_read(tmp_path):
    skill = _with_file(tmp_path, "evals/files/ok.txt", "x" * DEFAULT_LIMITS.max_file_bytes)
    [case] = _convert(_doc(_eval(files=["evals/files/ok.txt"])), skill)
    assert len(case["workspace"]["files"]["evals/files/ok.txt"]) == DEFAULT_LIMITS.max_file_bytes


def test_a_parent_directory_path_is_refused(tmp_path):
    skill = _skill(tmp_path)
    (tmp_path / "outside.csv").write_text("x", encoding="utf-8")
    with pytest.raises(CaseParseError, match=r"\.\."):
        _convert(_doc(_eval(files=["../outside.csv"])), skill)


def test_an_absolute_path_is_refused(tmp_path):
    skill = _skill(tmp_path)
    with pytest.raises(CaseParseError, match="not a relative path"):
        _convert(_doc(_eval(files=[str(tmp_path / "outside.csv")])), skill)


def test_the_same_file_listed_twice_is_refused(tmp_path):
    skill = _with_file(tmp_path, "evals/files/a.csv")
    with pytest.raises(CaseParseError, match="twice"):
        _convert(_doc(_eval(files=["evals/files/a.csv", "evals/files/a.csv"])), skill)


@posix_only
def test_a_symlink_out_of_the_skill_directory_is_refused(tmp_path):
    skill = _skill(tmp_path)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (skill.path / "evals").mkdir()
    os.symlink(tmp_path / "secret.txt", skill.path / "evals" / "link.txt")
    with pytest.raises(CaseParseError, match="outside the skill directory"):
        _convert(_doc(_eval(files=["evals/link.txt"])), skill)


@posix_only
def test_a_symlink_loop_is_a_refusal_not_a_crash(tmp_path):
    skill = _skill(tmp_path)
    (skill.path / "evals").mkdir()
    os.symlink("loop", skill.path / "evals" / "loop")
    with pytest.raises(CaseParseError, match="evals/loop"):
        _convert(_doc(_eval(files=["evals/loop"])), skill)


@posix_only
def test_a_fifo_is_refused_before_it_is_opened(tmp_path):
    skill = _skill(tmp_path)
    (skill.path / "evals").mkdir()
    os.mkfifo(skill.path / "evals" / "pipe")
    # open() on a FIFO blocks until a writer connects. The refusal has to come
    # from stat(), so a hang here means the file was opened.
    with pytest.raises(CaseParseError, match="not a regular file"):
        promptly(lambda: _convert(_doc(_eval(files=["evals/pipe"])), skill))
