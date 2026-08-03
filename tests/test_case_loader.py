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


def test_unreadable_non_utf8_eval_file_raises_case_parse_error(tmp_path):
    """Item 4: a non-UTF-8 byte must fail fast with a precise message naming
    the file, not escape as a raw UnicodeDecodeError traceback.
    """
    path = tmp_path / "broken.eval.yaml"
    path.write_bytes(b"\xff\xfe invalid")
    with pytest.raises(CaseParseError, match="broken.eval.yaml"):
        parse_cases_file(path)


def test_typoed_assertion_key_singular_raises_instead_of_silently_passing(tmp_path):
    """Item 3: a typo'd `assertion:` (singular) instead of `assertions:` used
    to be silently dropped by Pydantic (no model_config), producing a case
    with zero assertions that AssertionEvaluator treats as vacuously passing.
    With extra="forbid" on EvalCase, this must now raise a CaseParseError
    naming the file.
    """
    path = tmp_path / "typo.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: typo'd key\n"
        "    task: do it\n"
        "    assertion:\n"
        "      - kind: contains\n"
        "        value: x\n"
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "typo.eval.yaml" in str(exc.value)
    assert "assertion" in str(exc.value)


def test_trajectory_called_naming_an_undeclared_tool_raises(tmp_path):
    # A typo in `called:` (e.g. lookup_ordr instead of lookup_order) can never
    # pass -- it isn't a signal about the skill, it's a mistake in the case
    # file, and must abort the run rather than score as a failure.
    path = tmp_path / "typo.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: order lookup\n"
        "    task: look up order 1234\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "    trajectory:\n"
        "      called: [lookup_ordr]\n"
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "typo.eval.yaml" in str(exc.value)
    assert "order lookup" in str(exc.value)
    assert "lookup_ordr" in str(exc.value)


def test_trajectory_forbidden_naming_an_undeclared_tool_raises(tmp_path):
    path = tmp_path / "typo.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: order lookup\n"
        "    task: look up order 1234\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "    trajectory:\n"
        "      forbidden: [issue_refnd]\n"
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "typo.eval.yaml" in str(exc.value)
    assert "order lookup" in str(exc.value)
    assert "issue_refnd" in str(exc.value)


def test_trajectory_order_naming_an_undeclared_tool_raises(tmp_path):
    path = tmp_path / "typo.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: order lookup\n"
        "    task: look up order 1234\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "    trajectory:\n"
        "      order: [lookup_order, issue_refnd]\n"
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "typo.eval.yaml" in str(exc.value)
    assert "issue_refnd" in str(exc.value)


def test_trajectory_referencing_only_declared_tools_is_fine(tmp_path):
    path = tmp_path / "ok.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: order lookup\n"
        "    task: look up order 1234\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "      - name: issue_refund\n"
        "    trajectory:\n"
        "      called: [lookup_order]\n"
        "      forbidden: [issue_refund]\n"
        "      order: [lookup_order]\n"
    )
    cases = parse_cases_file(path)
    assert cases[0].trajectory.called == ["lookup_order"]


def test_duplicate_tool_names_in_one_case_raise(tmp_path):
    # Two ToolSpec entries with the same name reach the adapter as
    # "UserError: Tool name conflicts with existing tool" -- an errored case
    # indistinguishable from a real skill regression. This is a mistake in the
    # case file and must abort the run instead.
    path = tmp_path / "dupe.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: order lookup\n"
        "    task: look up order 1234\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "        description: first\n"
        "      - name: lookup_order\n"
        "        description: second\n"
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "dupe.eval.yaml" in str(exc.value)
    assert "order lookup" in str(exc.value)
    assert "lookup_order" in str(exc.value)


def test_non_ascii_eval_yaml_loads_regardless_of_platform_encoding(tmp_path):
    # Regression test: eval YAML is always UTF-8; read_text() must pin the
    # encoding rather than inherit a platform default that would mangle it.
    path = tmp_path / "accented.eval.yaml"
    path.write_text(
        "cases:\n  - name: café test\n    task: décrire\n"
        "    assertions:\n      - kind: contains\n        value: 日本語\n",
        encoding="utf-8",
    )
    cases = parse_cases_file(path)
    assert cases[0].name == "café test"
    assert cases[0].task == "décrire"
    assert cases[0].assertions[0].value == "日本語"


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "x.eval.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_judge_block_with_an_empty_rubric_is_an_authoring_error(tmp_path):
    path = write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    judge:
      expected: something good
""",
    )
    with pytest.raises(CaseParseError, match="empty rubric"):
        parse_cases_file(path)


def test_a_blank_rubric_entry_is_an_authoring_error(tmp_path):
    # A stray blank list item ("" or whitespace-only) loads fine as valid YAML
    # and would render as a check with no text -- a model asked to verify
    # nothing will likely return a vacuous pass. Reachable from ordinary YAML,
    # so it gets the same guard as an empty rubric list.
    path = write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    judge:
      expected: something good
      rubric:
        - a real check
        - "   "
""",
    )
    with pytest.raises(CaseParseError, match="entry 2 is blank"):
        parse_cases_file(path)


def test_skill_triggered_on_a_loaded_case_is_an_authoring_error(tmp_path):
    # A loaded skill is always in force, so the check could never be false.
    path = write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    trajectory:
      skill_triggered: true
""",
    )
    with pytest.raises(CaseParseError, match="mode: offered"):
        parse_cases_file(path)


def test_skill_triggered_is_accepted_on_an_offered_case(tmp_path):
    path = write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    mode: offered
    trajectory:
      skill_triggered: false
""",
    )
    cases = parse_cases_file(path)
    assert cases[0].trajectory.skill_triggered is False


def test_a_case_tool_colliding_with_the_offered_skill_name_is_an_authoring_error(tmp_path):
    skill = Skill(name="order-support", description="d", instructions="i", path=tmp_path)
    write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    mode: offered
    tools:
      - name: order_support
        description: not the skill
""",
    )
    with pytest.raises(CaseParseError, match="collides"):
        load_cases_for_skill(skill)


def test_the_collision_check_only_applies_to_offered_cases(tmp_path):
    # In loaded mode nothing is offered, so the name is free.
    skill = Skill(name="order-support", description="d", instructions="i", path=tmp_path)
    write(
        tmp_path,
        """
cases:
  - name: c
    task: t
    tools:
      - name: order_support
        description: just a tool
""",
    )
    assert len(load_cases_for_skill(skill)) == 1


def test_a_sentinel_in_a_case_is_an_authoring_error(tmp_path):
    path = tmp_path / "unfilled.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: handles the common case\n"
        "    task: TODO(skill-eval) the prompt a user would type\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    message = str(exc.value)
    assert "TODO(skill-eval)" in message
    assert "task" in message
    assert str(path) in message


def test_a_sentinel_nested_in_a_tool_names_the_field(tmp_path):
    path = tmp_path / "unfilled.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: takes the right path\n"
        "    task: refund order 1234\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "        description: look an order up\n"
        "        returns: 'TODO(skill-eval) the JSON this tool returns'\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "tools[0].returns" in str(exc.value)


def test_a_sentinel_in_a_rubric_entry_names_its_position(tmp_path):
    path = tmp_path / "unfilled.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: explains itself\n"
        "    task: refund order 1234\n"
        "    judge:\n"
        "      rubric:\n"
        "        - The reply names order 1234\n"
        "        - TODO(skill-eval) what else a good answer does\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "judge.rubric[1]" in str(exc.value)


def test_a_sentinel_in_a_comment_is_not_a_sentinel(tmp_path):
    # Comments are discarded by the YAML parser before the scan sees the data,
    # which is what lets the generated file explain the token it uses.
    path = tmp_path / "filled.eval.yaml"
    path.write_text(
        "# Replace every TODO(skill-eval) before running this file.\n"
        "cases:\n"
        "  - name: handles the common case\n"
        "    task: greet Ada\n"
        "    assertions:\n"
        "      - kind: contains\n"
        "        value: Ada\n",
        encoding="utf-8",
    )
    cases = parse_cases_file(path)
    assert [case.name for case in cases] == ["handles the common case"]
