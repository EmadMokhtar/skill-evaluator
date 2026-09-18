from pathlib import Path

import pytest

from skill_lens.cases.loader import CaseParseError, load_cases_for_skill, parse_cases_file
from skill_lens.models import Skill, ToolSpec

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
    from skill_lens.yaml_loading import safe_load

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
        "    task: TODO(skill-lens) the prompt a user would type\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    message = str(exc.value)
    assert "TODO(skill-lens)" in message
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
        "        returns: 'TODO(skill-lens) the JSON this tool returns'\n",
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
        "        - TODO(skill-lens) what else a good answer does\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "judge.rubric[1]" in str(exc.value)


def test_a_self_referential_yaml_anchor_is_an_authoring_error_not_a_recursion_error(tmp_path):
    # A self-referential anchor (`self: *a` inside the node `&a` itself) makes
    # a naive recursive walk loop forever. It's still a malformed eval file --
    # the loader must exit cleanly with CaseParseError, the same clean "exit 2
    # naming the file" contract as any other bad input, not a raw
    # RecursionError traceback out of skill-lens list.
    path = tmp_path / "cyclic.eval.yaml"
    path.write_text(
        "cases:\n  - &a\n    name: x\n    task: t\n    self: *a\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError):
        parse_cases_file(path)


def test_a_sentinel_in_a_comment_is_not_a_sentinel(tmp_path):
    # Comments are discarded by the YAML parser before the scan sees the data,
    # which is what lets the generated file explain the token it uses.
    path = tmp_path / "filled.eval.yaml"
    path.write_text(
        "# Replace every TODO(skill-lens) before running this file.\n"
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


def _write(tmp_path, body: str):
    path = tmp_path / "cases.eval.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_an_unknown_kind_is_rejected_at_load_time(tmp_path):
    # Before M6 this surfaced only when the case ran, after money was spent.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    assertions:\n"
        "      - kind: containz\n        value: x\n",
    )
    with pytest.raises(CaseParseError, match="containz"):
        parse_cases_file(path)


def test_contains_without_a_value_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    assertions:\n      - kind: contains\n",
    )
    with pytest.raises(CaseParseError, match="value"):
        parse_cases_file(path)


def test_file_produced_without_a_file_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    assertions:\n"
        "      - kind: file-produced\n",
    )
    with pytest.raises(CaseParseError, match="file"):
        parse_cases_file(path)


def test_a_forbidden_field_for_the_kind_is_rejected(tmp_path):
    # `value` means nothing to file-produced; accepting it silently would let
    # an author think they had asserted on content.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    assertions:\n"
        "      - kind: file-produced\n        file: r.md\n        value: hello\n",
    )
    with pytest.raises(CaseParseError, match="value"):
        parse_cases_file(path)


def test_a_file_target_without_a_workspace_is_rejected(tmp_path):
    # The assertion could never hold, so it is a mistake, not a failing skill.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    assertions:\n"
        "      - kind: contains\n        value: x\n        file: r.md\n",
    )
    with pytest.raises(CaseParseError, match="workspace"):
        parse_cases_file(path)


def test_judge_artifacts_without_a_workspace_are_rejected(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    judge:\n"
        "      artifacts: [r.md]\n      rubric: ['it is good']\n",
    )
    with pytest.raises(CaseParseError, match="workspace"):
        parse_cases_file(path)


def test_a_judge_artifact_naming_a_path_that_escapes_is_rejected(tmp_path):
    # No agent could ever produce a file at "../escape.txt" -- it is a typo
    # in the eval author's list, not a fact about the skill. Without this
    # check it would sail through and render as "(not produced)" at run
    # time, failing the rubric and blaming the skill for the author's
    # mistake. workspace.files gets the identical check above; this pins the
    # same rule for judge.artifacts.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    judge:\n"
        "      artifacts: ['../escape.txt']\n      rubric: ['it is good']\n",
    )
    with pytest.raises(CaseParseError, match="escape.txt"):
        parse_cases_file(path)


def test_a_malformed_json_schema_is_an_authoring_error(tmp_path):
    # Same class of mistake as a malformed regex.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    assertions:\n"
        "      - kind: json-schema\n        file: d.json\n"
        "        json_schema:\n          type: not-a-real-type\n",
    )
    with pytest.raises(CaseParseError, match="json_schema"):
        parse_cases_file(path)


def test_a_valid_json_schema_case_loads(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    assertions:\n"
        "      - kind: json-schema\n        file: d.json\n"
        "        json_schema:\n          type: object\n",
    )
    (case,) = parse_cases_file(path)
    assert case.assertions[0].json_schema == {"type": "object"}


@pytest.mark.parametrize("bad", ["/abs.txt", "../escape.txt", "nested/../../x.txt", ""])
def test_a_workspace_file_that_escapes_is_rejected(tmp_path, bad):
    path = _write(
        tmp_path,
        f"cases:\n  - name: n\n    task: t\n    workspace:\n      files:\n        {bad!r}: 'x'\n",
    )
    with pytest.raises(CaseParseError):
        parse_cases_file(path)


def test_an_assertion_file_target_that_escapes_is_rejected_at_load_time(tmp_path):
    # workspace.files and judge.artifacts are validated here already; an
    # assertion's file: is the third path source and, before this fix, was
    # only caught at evaluate time -- after the runner had already run (and,
    # with a real provider, spent money). It must abort at load time (exit
    # 2), same as the other two.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    assertions:\n"
        "      - kind: contains\n        value: x\n        file: ../escape.txt\n",
    )
    with pytest.raises(CaseParseError, match="escape.txt"):
        parse_cases_file(path)


def test_a_case_tool_colliding_with_a_builtin_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    tools:\n"
        "      - name: write_file\n        description: mine\n",
    )
    with pytest.raises(CaseParseError, match="write_file"):
        parse_cases_file(path)


def test_the_same_tool_name_is_fine_without_a_workspace(tmp_path):
    # No workspace means no built-in tools, so there is nothing to collide with.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - name: write_file\n        description: mine\n",
    )
    (case,) = parse_cases_file(path)
    assert case.tools[0].name == "write_file"


def test_a_trajectory_may_name_a_builtin_when_a_workspace_exists(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n"
        "    trajectory:\n      called: [write_file]\n",
    )
    (case,) = parse_cases_file(path)
    assert case.trajectory is not None
    assert case.trajectory.called == ["write_file"]


def test_a_trajectory_naming_a_builtin_without_a_workspace_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    trajectory:\n      called: [write_file]\n",
    )
    with pytest.raises(CaseParseError, match="workspace"):
        parse_cases_file(path)


def test_a_case_with_no_workspace_still_loads_unchanged(tmp_path):
    # The whole opt-in promise: suites written before M6 must be untouched.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    assertions:\n"
        "      - kind: contains\n        value: hello\n",
    )
    (case,) = parse_cases_file(path)
    assert case.workspace is None
    assert case.assertions[0].file is None


def test_a_placeholder_in_a_mapping_key_is_refused_too(tmp_path):
    # `workspace.files` is keyed by filename. A scaffold that left the
    # filename unfilled would otherwise load, seed a file literally named
    # "TODO(skill-lens) ..." and let the case run.
    path = tmp_path / "x.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: writes a file\n"
        "    task: go\n"
        "    workspace:\n"
        "      files:\n"
        '        "TODO(skill-lens) the input file": hello\n'
        "    assertions:\n"
        "      - kind: file-produced\n"
        "        file: out.txt\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "TODO(skill-lens)" in str(exc.value)
    assert "workspace.files" in str(exc.value)


def test_a_case_tool_named_run_script_collides_with_a_built_in(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    tools:\n"
        "      - name: run_script\n        description: mine\n",
    )
    with pytest.raises(CaseParseError, match="run_script.*collides with a built-in"):
        parse_cases_file(path)


def test_a_trajectory_may_name_run_script_when_a_workspace_exists(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n"
        "    trajectory:\n      called: [run_script, read_skill_file]\n",
    )
    (case,) = parse_cases_file(path)
    assert case.trajectory is not None
    assert case.trajectory.called == ["run_script", "read_skill_file"]


def test_a_trajectory_naming_run_script_without_a_workspace_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    trajectory:\n      called: [run_script]\n",
    )
    with pytest.raises(CaseParseError, match="workspace and bundle tools only exist"):
        parse_cases_file(path)


INPUT_SCHEMA_CASE = """cases:
  - name: n
    task: t
    tools:
      - name: get-pull-request
        description: Get a pull request
        input_schema:
          type: object
          properties:
            owner: {type: string}
            pull_number: {type: integer}
          required: [owner]
        returns: '{"number": 1}'
    trajectory:
      called: [get-pull-request]
"""


def test_a_tool_may_declare_an_input_schema(tmp_path):
    cases = parse_cases_file(_write(tmp_path, INPUT_SCHEMA_CASE))
    tool = cases[0].tools[0]
    assert tool.name == "get-pull-request"
    assert tool.input_schema["required"] == ["owner"]
    assert tool.parameters == {}


def test_a_tool_may_not_declare_both_parameters_and_an_input_schema(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - name: lookup\n        parameters: {q: string}\n"
        "        input_schema: {type: object}\n",
    )
    with pytest.raises(CaseParseError, match="tool 'lookup' declares both parameters and"):
        parse_cases_file(path)


def test_an_input_schema_that_is_not_an_object_is_rejected(tmp_path):
    # Every provider requires a tool's arguments to be an object.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - name: lookup\n        input_schema: {type: string}\n",
    )
    with pytest.raises(
        CaseParseError, match="tool 'lookup' input_schema must declare type: object"
    ):
        parse_cases_file(path)


def test_a_malformed_input_schema_is_rejected_at_load_time(tmp_path):
    # Caught before any case runs, like an assertion's json_schema.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - name: lookup\n        input_schema: {type: 5}\n",
    )
    with pytest.raises(CaseParseError, match="tool 'lookup' has an invalid input_schema"):
        parse_cases_file(path)


@pytest.mark.parametrize(
    "schema_yaml",
    ["{}", '{type: [object, "null"]}'],
    ids=["empty", "type-array-with-null"],
)
def test_an_input_schema_must_declare_a_bare_object_type(tmp_path, schema_yaml):
    # `{}` is valid JSON Schema but declares no type at all; a type *array*
    # naming object is valid JSON Schema too but is not the bare `object`
    # every provider requires a tool's arguments to be. Neither should be
    # confused with the "malformed schema" case above, which fails
    # check_schema outright.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        f"      - name: lookup\n        input_schema: {schema_yaml}\n",
    )
    with pytest.raises(
        CaseParseError, match="tool 'lookup' input_schema must declare type: object"
    ):
        parse_cases_file(path)


# --- tool libraries and ref: ---------------------------------------------------

LIBRARY_YAML = """tools:
  - name: lookup_order
    description: Look up an order by its id
    parameters:
      order_id: string
    returns: '{"id": "0000"}'
  - name: issue_refund
    description: Issue a refund for an order
    parameters:
      order_id: string
    returns: '{"ok": true}'
"""

REF_CASES = """tool_libraries:
  - ../../../shared-tools/order-api.yaml
cases:
  - name: refuses
    task: refund 1234
    tools:
      - ref: lookup_order
        returns: '{"id": "1234", "days_since_delivery": 45}'
      - ref: issue_refund
    trajectory:
      called: [lookup_order]
      forbidden: [issue_refund]
"""


def _layout(tmp_path):
    """An eval file three directories below the library, so a path relative to
    the eval file and a path relative to the working directory differ."""
    shared = tmp_path / "shared-tools"
    shared.mkdir()
    (shared / "order-api.yaml").write_text(LIBRARY_YAML, encoding="utf-8")
    evals = tmp_path / "skills" / "orders" / "evals"
    evals.mkdir(parents=True)
    path = evals / "orders.yaml"
    path.write_text(REF_CASES, encoding="utf-8")
    return path


def test_a_ref_resolves_to_the_library_tool_with_the_case_returns(tmp_path, monkeypatch):
    path = _layout(tmp_path)
    # conftest already moved us into tmp_path; move further so a path
    # resolved against the working directory could not find the library.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    (case,) = parse_cases_file(path)
    lookup, refund = case.tools
    assert type(lookup) is ToolSpec and type(refund) is ToolSpec
    assert lookup.description == "Look up an order by its id"
    assert lookup.parameters == {"order_id": "string"}
    assert lookup.returns == '{"id": "1234", "days_since_delivery": 45}'
    assert refund.returns == '{"ok": true}'
    assert case.trajectory.called == ["lookup_order"]


def test_refs_resolve_under_an_explicit_evals_path_too(tmp_path):
    path = _layout(tmp_path)
    skill = _skill(tmp_path / "unrelated")
    (case,) = load_cases_for_skill(skill, evals_path=path)
    assert [t.name for t in case.tools] == ["lookup_order", "issue_refund"]


def test_a_ref_may_sit_beside_an_inline_tool(tmp_path):
    path = _layout(tmp_path)
    path.write_text(
        REF_CASES.replace(
            "      - ref: issue_refund\n",
            "      - name: escalate\n        returns: ok\n      - ref: issue_refund\n",
        ),
        encoding="utf-8",
    )
    (case,) = parse_cases_file(path)
    assert [t.name for t in case.tools] == ["lookup_order", "escalate", "issue_refund"]


@pytest.mark.parametrize("extra", ["description: rewritten", "name: other", "parameters: {}"])
def test_a_ref_carrying_anything_but_returns_is_refused_naming_the_key(tmp_path, extra):
    path = _layout(tmp_path)
    path.write_text(
        REF_CASES.replace(
            "      - ref: issue_refund\n", f"      - ref: issue_refund\n        {extra}\n"
        ),
        encoding="utf-8",
    )
    key = extra.split(":")[0]
    with pytest.raises(
        CaseParseError, match=rf"orders.yaml: case #1 tool #2: invalid ref: entry \({key}\)"
    ):
        parse_cases_file(path)


def test_a_placeholder_in_a_ref_returns_is_caught_before_any_library_is_read(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").unlink()
    path.write_text(
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - ref: lookup_order\n        returns: TODO(skill-lens) fill\n",
        encoding="utf-8",
    )
    with pytest.raises(
        CaseParseError, match=r"placeholder TODO\(skill-lens\) at tools\[0\]\.returns"
    ):
        parse_cases_file(path)


def test_a_ref_with_no_tool_libraries_key_says_to_add_one(tmp_path):
    path = _write(
        tmp_path, "cases:\n  - name: n\n    task: t\n    tools:\n      - ref: lookup_order\n"
    )
    with pytest.raises(
        CaseParseError,
        match=r"cases.eval.yaml: case #1 tool #1 references tool 'lookup_order' but the "
        r"file declares no tool_libraries:",
    ):
        parse_cases_file(path)


def test_an_unknown_ref_lists_what_the_imports_declare(tmp_path):
    path = _layout(tmp_path)
    path.write_text(REF_CASES.replace("ref: issue_refund", "ref: cancel_order"), encoding="utf-8")
    with pytest.raises(
        CaseParseError,
        match=r"case #1 tool #2 references tool 'cancel_order', which no imported library "
        r"declares; the imports declare: issue_refund, lookup_order",
    ):
        parse_cases_file(path)


def test_a_library_error_names_the_eval_file_and_the_library(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").write_text(
        "tools: [unclosed\n", encoding="utf-8"
    )
    with pytest.raises(CaseParseError) as info:
        parse_cases_file(path)
    assert "orders.yaml: invalid YAML in tool library" in str(info.value)
    assert "order-api.yaml" in str(info.value)


def test_a_missing_library_names_the_entry_and_the_eval_file(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").unlink()
    with pytest.raises(
        CaseParseError,
        match=r"orders.yaml: tool_libraries\[0\] '../../../shared-tools/order-api.yaml' "
        r"does not exist",
    ):
        parse_cases_file(path)


def test_a_ref_twice_in_one_case_hits_the_duplicate_check(tmp_path):
    path = _layout(tmp_path)
    path.write_text(REF_CASES.replace("ref: issue_refund", "ref: lookup_order"), encoding="utf-8")
    with pytest.raises(CaseParseError, match="declares tool 'lookup_order' more than once"):
        parse_cases_file(path)


def test_a_ref_beside_an_inline_tool_of_the_same_name_hits_the_duplicate_check(tmp_path):
    path = _layout(tmp_path)
    path.write_text(
        REF_CASES.replace("      - ref: issue_refund\n", "      - name: lookup_order\n"),
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError, match="declares tool 'lookup_order' more than once"):
        parse_cases_file(path)


def test_a_ref_to_a_builtin_name_collides_in_a_workspace_case(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").write_text(
        "tools:\n  - name: read_file\n", encoding="utf-8"
    )
    path.write_text(
        "tool_libraries: [../../../shared-tools/order-api.yaml]\n"
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    tools:\n      - ref: read_file\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError, match="collides with a built-in workspace tool"):
        parse_cases_file(path)


def test_a_ref_collides_with_the_offered_skill_name(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").write_text(
        "tools:\n  - name: orders\n", encoding="utf-8"
    )
    path.write_text(
        "tool_libraries: [../../../shared-tools/order-api.yaml]\n"
        "cases:\n  - name: n\n    task: t\n    mode: offered\n    tools:\n      - ref: orders\n",
        encoding="utf-8",
    )
    skill = Skill(name="orders", description="", instructions="", path=tmp_path)
    with pytest.raises(
        CaseParseError, match="collides with the name skill 'orders' is offered under"
    ):
        parse_cases_file(path, skill)


def test_two_cases_sharing_an_anchored_tools_list_both_resolve(tmp_path):
    path = _layout(tmp_path)
    path.write_text(
        "tool_libraries: [../../../shared-tools/order-api.yaml]\n"
        "cases:\n"
        "  - name: one\n    task: t\n    tools: &shared\n      - ref: lookup_order\n"
        "  - name: two\n    task: t\n    tools: *shared\n",
        encoding="utf-8",
    )
    one, two = parse_cases_file(path)
    assert one.tools[0].description == two.tools[0].description == "Look up an order by its id"


def test_a_file_with_tool_libraries_but_no_refs_loads_unchanged(tmp_path):
    path = _layout(tmp_path)
    path.write_text(
        "tool_libraries: [../../../shared-tools/order-api.yaml]\n"
        "cases:\n  - name: n\n    task: t\n",
        encoding="utf-8",
    )
    (case,) = parse_cases_file(path)
    assert case.tools == []


@pytest.mark.parametrize("value", ["shared-tools/x.yaml", "{a: b}"])
def test_a_tool_libraries_value_that_is_not_a_list_is_refused(tmp_path, value):
    path = _write(tmp_path, f"tool_libraries: {value}\ncases: []\n")
    with pytest.raises(
        CaseParseError, match="cases.eval.yaml: tool_libraries must be a list of paths"
    ):
        parse_cases_file(path)
