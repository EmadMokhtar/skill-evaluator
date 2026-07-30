from pathlib import Path

import pytest

from skill_eval.cases.loader import CaseParseError, load_cases_for_skill, parse_cases_file
from skill_eval.models import Skill

CASES_YAML = """cases:
  - name: extracts text
    task: Extract the text from report.pdf
    tags: [smoke]
    assertions:
      - kind: contains
        value: pdfplumber
  - name: handles missing file
    task: Extract from nope.pdf
    assertions:
      - kind: not_contains
        value: traceback
"""


def _skill(tmp_path, name="pdf"):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    return Skill(name=name, description="", instructions="", path=skill_dir)


def test_parses_cases_from_a_yaml_file(tmp_path):
    path = tmp_path / "x.eval.yaml"
    path.write_text(CASES_YAML)
    cases = parse_cases_file(path)
    assert [c.name for c in cases] == ["extracts text", "handles missing file"]
    assert cases[0].assertions[0].kind == "contains"
    assert cases[0].assertions[0].value == "pdfplumber"
    assert cases[0].tags == ["smoke"]
    assert cases[1].tags == []


def test_discovers_evals_directory_beside_skill(tmp_path):
    skill = _skill(tmp_path)
    evals = skill.path / "evals"
    evals.mkdir()
    (evals / "basic.yaml").write_text(CASES_YAML)
    assert len(load_cases_for_skill(skill)) == 2


def test_discovers_dot_eval_yaml_beside_skill(tmp_path):
    skill = _skill(tmp_path)
    (skill.path / "pdf.eval.yaml").write_text(CASES_YAML)
    assert len(load_cases_for_skill(skill)) == 2


def test_explicit_evals_path_overrides_convention(tmp_path):
    skill = _skill(tmp_path)
    (skill.path / "pdf.eval.yaml").write_text(CASES_YAML)
    other = tmp_path / "other.yaml"
    other.write_text("cases:\n  - name: only one\n    task: do it\n")
    cases = load_cases_for_skill(skill, evals_path=other)
    assert [c.name for c in cases] == ["only one"]


def test_skill_with_no_evals_returns_empty(tmp_path):
    assert load_cases_for_skill(_skill(tmp_path)) == []


def test_case_missing_task_raises_with_file_and_field(tmp_path):
    path = tmp_path / "bad.eval.yaml"
    path.write_text("cases:\n  - name: no task here\n")
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "bad.eval.yaml" in str(exc.value)
    assert "task" in str(exc.value)


def test_malformed_yaml_raises_with_path(tmp_path):
    path = tmp_path / "broken.eval.yaml"
    path.write_text("cases: [unclosed\n")
    with pytest.raises(CaseParseError, match="broken.eval.yaml"):
        parse_cases_file(path)


def test_missing_explicit_path_raises(tmp_path):
    with pytest.raises(CaseParseError, match="does not exist"):
        load_cases_for_skill(_skill(tmp_path), evals_path=Path(tmp_path / "nope.yaml"))


def test_cases_non_list_scalar_raises_with_file_and_field(tmp_path):
    path = tmp_path / "nonlist.eval.yaml"
    path.write_text("cases: true\n")
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "nonlist.eval.yaml" in str(exc.value)
    assert "cases" in str(exc.value)


def test_cases_explicit_null_raises_with_file_and_field(tmp_path):
    path = tmp_path / "null_cases.eval.yaml"
    path.write_text("cases:\n")
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "null_cases.eval.yaml" in str(exc.value)
    assert "cases" in str(exc.value)


def test_cases_empty_list_returns_empty(tmp_path):
    path = tmp_path / "empty.eval.yaml"
    path.write_text("cases: []\n")
    cases = parse_cases_file(path)
    assert cases == []


def test_bare_yes_no_on_off_assertion_values_parse_as_strings(tmp_path):
    path = tmp_path / "bools.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: bare bool-like literals\n"
        "    task: check literals\n"
        "    assertions:\n"
        "      - kind: contains\n"
        "        value: yes\n"
        "      - kind: contains\n"
        "        value: no\n"
        "      - kind: contains\n"
        "        value: on\n"
        "      - kind: contains\n"
        "        value: off\n"
    )
    cases = parse_cases_file(path)
    values = [assertion.value for assertion in cases[0].assertions]
    assert values == ["yes", "no", "on", "off"]
    assert all(isinstance(v, str) for v in values)


def test_genuine_true_false_still_parse_as_bool_via_shared_loader():
    from skill_eval.yaml_loading import safe_load

    data = safe_load("flag_true: true\nflag_false: false\n")
    assert data["flag_true"] is True
    assert data["flag_false"] is False
