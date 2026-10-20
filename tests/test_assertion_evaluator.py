import json
import os

import pytest

from promptly import promptly
from skill_lens.cases.loader import _ASSERTION_FIELDS
from skill_lens.evaluators.assertion import (
    ASSERTION_KINDS,
    AssertionEvaluator,
    InvalidAssertionValue,
    UnknownAssertionKind,
)
from skill_lens.evaluators.base import Evaluator
from skill_lens.models import AssertionSpec, EvalCase, RunResult, WorkspaceSpec
from skill_lens.paths import PatternRefused
from skill_lens.workspace import Workspace


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


def test_a_surrogate_bearing_file_is_an_authoring_error_not_a_crash(tmp_path):
    # A lone UTF-16 surrogate ('\ud800') is half of a UTF-16 pair and cannot
    # be encoded as UTF-8. YAML carries it happily, and before this fix
    # check_relative_path accepted it, so it reached os.path.realpath (inside
    # Workspace.resolve) and raised UnicodeEncodeError -- a plain ValueError
    # that matches none of _subject_text's except clauses (PathRefused,
    # UnicodeDecodeError, OSError) and escaped as an unhandled traceback,
    # exit 1 -- telling CI "the skill got worse" for what is actually a typo
    # in the eval file. It must instead raise InvalidAssertionValue, the same
    # authoring-error outcome as any other refused path.
    result = RunResult(output="", workspace=tmp_path.resolve())
    case = _case(AssertionSpec(kind="contains", value="x", file="a\ud800b.txt"))
    with pytest.raises(InvalidAssertionValue):
        AssertionEvaluator().evaluate(case, result)


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


# --- what the run put at a well-formed path ---------------------------------
#
# The author's path is checked first (an authoring error); what the skill
# then put there is the skill's doing, and a refusal to read it is a failed
# check -- never exit 2, which under --concurrency would also cancel every
# queued case.

posix_only = pytest.mark.skipif(os.name == "nt", reason="FIFOs and symlinks are POSIX features")


@posix_only
@pytest.mark.parametrize("kind", ["contains", "file-produced"])
def test_a_symlink_the_run_planted_fails_the_check_rather_than_aborting(tmp_path, kind):
    os.symlink("/etc", tmp_path / "triage.md")
    result = RunResult(output="", workspace=tmp_path.resolve())
    spec = (
        AssertionSpec(kind=kind, file="triage.md")
        if kind == "file-produced"
        else AssertionSpec(kind=kind, value="x", file="triage.md")
    )
    score = AssertionEvaluator().evaluate(_case(spec), result)
    assert not score.passed
    assert not score.errored
    assert "resolves outside" in score.checks[0].evidence


@posix_only
@pytest.mark.parametrize("kind", ["contains", "file-produced"])
def test_a_fifo_the_run_planted_fails_the_check_promptly(tmp_path, kind):
    os.mkfifo(tmp_path / "triage.md")
    result = RunResult(output="", workspace=tmp_path.resolve())
    spec = (
        AssertionSpec(kind=kind, file="triage.md")
        if kind == "file-produced"
        else AssertionSpec(kind=kind, value="x", file="triage.md")
    )
    score = promptly(lambda: AssertionEvaluator().evaluate(_case(spec), result))
    assert not score.passed
    assert not score.errored
    assert "not a regular file" in score.checks[0].evidence


@posix_only
def test_a_symlink_loop_the_run_planted_fails_the_check(tmp_path):
    os.symlink("triage.md", tmp_path / "triage.md")
    result = RunResult(output="", workspace=tmp_path.resolve())
    case = _case(AssertionSpec(kind="contains", value="x", file="triage.md"))
    score = AssertionEvaluator().evaluate(case, result)
    assert not score.passed
    assert not score.errored


def test_a_file_over_the_read_cap_fails_the_check_naming_the_cap(tmp_path):
    with (tmp_path / "triage.md").open("wb") as handle:
        handle.seek(2_000_000)
        handle.write(b"x")
    result = RunResult(output="", workspace=tmp_path.resolve())
    case = _case(AssertionSpec(kind="contains", value="x", file="triage.md"))
    score = AssertionEvaluator().evaluate(case, result)
    assert not score.passed
    assert not score.errored
    assert "max_file_bytes" in score.checks[0].evidence


def test_a_file_over_the_read_cap_still_counts_as_produced(tmp_path):
    # file-produced asks whether the file exists, not whether it is readable.
    with (tmp_path / "triage.md").open("wb") as handle:
        handle.seek(2_000_000)
        handle.write(b"x")
    result = RunResult(output="", workspace=tmp_path.resolve())
    case = _case(AssertionSpec(kind="file-produced", file="triage.md"))
    assert AssertionEvaluator().evaluate(case, result).passed


# --- file_glob ---------------------------------------------------------------

PLAN = ".planning/*/task_plan.md"


def _tree(tmp_path, files: dict[str, str]) -> RunResult:
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return RunResult(output="chat output", workspace=tmp_path.resolve())


@pytest.mark.parametrize(
    "spec",
    [
        AssertionSpec(kind="file-produced", file_glob=PLAN),
        AssertionSpec(kind="contains", value="## Goal", file_glob=PLAN),
        AssertionSpec(kind="not_contains", value="pickle", file_glob=PLAN),
        AssertionSpec(kind="regex", value="(?m)^## Goal", file_glob=PLAN),
        AssertionSpec(kind="equals", value="## Goal", file_glob=PLAN),
    ],
)
def test_a_file_glob_reads_the_one_file_it_matches(tmp_path, spec):
    result = _tree(tmp_path, {".planning/2026-10-11-todo/task_plan.md": "## Goal\n"})
    assert AssertionEvaluator().evaluate(_case(spec), result).passed is True


def test_json_schema_reads_the_one_file_a_glob_matches(tmp_path):
    result = _tree(tmp_path, {"out/run-7.json": json.dumps({"total": 3})})
    spec = AssertionSpec(
        kind="json-schema",
        file_glob="out/*.json",
        json_schema={"type": "object", "required": ["total"]},
    )
    assert AssertionEvaluator().evaluate(_case(spec), result).passed is True


def test_a_glob_matching_nothing_fails_and_shows_the_workspace(tmp_path):
    result = _tree(tmp_path, {"todo.py": "x"})
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="file-produced", file_glob=PLAN)), result
    )
    [check] = score.checks
    assert check.passed is False
    assert check.id == "file-produced[0]"
    assert f"no file matched {PLAN}; workspace held todo.py" in check.evidence


def test_a_glob_matching_several_files_fails_and_names_them(tmp_path):
    result = _tree(
        tmp_path,
        {".planning/a/task_plan.md": "## Goal", ".planning/b/task_plan.md": "## Goal"},
    )
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="contains", value="## Goal", file_glob=PLAN)), result
    )
    [check] = score.checks
    assert check.passed is False
    assert (
        f"{PLAN} matched 2 files: .planning/a/task_plan.md, .planning/b/task_plan.md; "
        "exactly one is required"
    ) in check.evidence


def test_the_evidence_says_a_pattern_was_used(tmp_path):
    result = _tree(tmp_path, {".planning/x/task_plan.md": "y"})
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="regex", value="Goal", file_glob=PLAN)), result
    )
    assert f"file_glob='{PLAN}'" in score.checks[0].evidence


@pytest.mark.skipif(os.name == "nt", reason="symlinks are a POSIX feature here")
def test_a_matched_symlink_pointing_outside_is_a_failed_check(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("## Goal", encoding="utf-8")
    (tmp_path / "out").mkdir()
    os.symlink(outside, tmp_path / "out" / "report.md")
    result = RunResult(output="o", workspace=tmp_path.resolve())
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="contains", value="Goal", file_glob="out/*.md")), result
    )
    assert score.passed is False
    assert "resolves outside" in score.checks[0].evidence


def test_a_glob_that_bypassed_validation_is_still_an_authoring_error(tmp_path):
    spec = AssertionSpec.model_construct(
        kind="file-produced", value=None, file=None, file_glob="**/x.md", json_schema=None
    )
    case = EvalCase.model_construct(
        name="n", task="t", workspace=WorkspaceSpec(), assertions=[spec], tools=[]
    )
    with pytest.raises(PatternRefused, match="'\\*\\*'"):
        AssertionEvaluator().evaluate(case, _tree(tmp_path, {}))


def test_one_evaluation_walks_the_workspace_once_per_pattern(tmp_path, monkeypatch):
    result = _tree(tmp_path, {".planning/x/task_plan.md": "## Goal"})
    seen: list[str] = []
    real_match = Workspace.match

    def counting_match(self, pattern):
        seen.append(pattern)
        return real_match(self, pattern)

    monkeypatch.setattr(Workspace, "match", counting_match)
    case = _case(
        AssertionSpec(kind="file-produced", file_glob=PLAN),
        AssertionSpec(kind="contains", value="Goal", file_glob=PLAN),
    )
    assert AssertionEvaluator().evaluate(case, result).passed is True
    assert seen == [PLAN]


ROOT_OR_NAMED = ["task_plan.md", ".planning/*/task_plan.md"]


def test_a_file_glob_list_reads_the_file_in_either_place(tmp_path):
    for layout in ("task_plan.md", ".planning/run-1/task_plan.md"):
        root = tmp_path / layout.replace("/", "_")
        root.mkdir()
        result = _tree(root, {layout: "## Goal\n"})
        spec = AssertionSpec(kind="regex", value="(?m)^## Goal", file_glob=ROOT_OR_NAMED)
        assert AssertionEvaluator().evaluate(_case(spec), result).passed is True, layout


def test_a_file_glob_list_matching_in_two_places_fails(tmp_path):
    result = _tree(tmp_path, {"task_plan.md": "x", ".planning/a/task_plan.md": "x"})
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="file-produced", file_glob=ROOT_OR_NAMED)), result
    )
    assert score.passed is False
    assert "matched 2 files: .planning/a/task_plan.md, task_plan.md" in score.checks[0].evidence


def test_a_file_glob_list_matching_nothing_names_every_entry(tmp_path):
    result = _tree(tmp_path, {"todo.py": "x"})
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="file-produced", file_glob=ROOT_OR_NAMED)), result
    )
    assert "no file matched task_plan.md or .planning/*/task_plan.md" in score.checks[0].evidence


def test_two_checks_on_one_list_walk_the_workspace_once(tmp_path, monkeypatch):
    result = _tree(tmp_path, {"task_plan.md": "## Goal"})
    seen = []
    real_match = Workspace.match

    def counting_match(self, pattern):
        seen.append(pattern)
        return real_match(self, pattern)

    monkeypatch.setattr(Workspace, "match", counting_match)
    case = _case(
        AssertionSpec(kind="file-produced", file_glob=ROOT_OR_NAMED),
        AssertionSpec(kind="contains", value="Goal", file_glob=ROOT_OR_NAMED),
    )
    assert AssertionEvaluator().evaluate(case, result).passed is True
    assert len(seen) == 1
