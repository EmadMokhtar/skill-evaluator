from pathlib import Path

import pytest
from pydantic import ValidationError

from skill_lens.models import (
    AssertionSpec,
    BudgetSpec,
    CallArgsSpec,
    CaseOutcome,
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeRequest,
    JudgeSpec,
    JudgeVerdict,
    ProductStatus,
    RubricCheck,
    RunReport,
    RunResult,
    ScriptNote,
    ScriptStatus,
    Skill,
    ToolCall,
    ToolRef,
    ToolSpec,
    TrajectorySpec,
    WorkspaceSpec,
)


def _result(output="ok", error=None):
    return RunResult(output=output, error=error)


def test_skill_holds_metadata_and_body():
    skill = Skill(
        name="pdf",
        description="Work with PDFs",
        instructions="Do the thing.",
        path=Path("/skills/pdf"),
    )
    assert skill.name == "pdf"
    assert skill.instructions == "Do the thing."


def test_run_result_defaults_are_empty_and_not_errored():
    result = _result()
    assert result.tool_calls == []
    assert result.cost_usd == 0.0
    assert result.errored is False


def test_run_result_with_error_is_errored():
    assert _result(error="boom").errored is True


def test_tool_call_roundtrips_arguments():
    call = ToolCall(name="search", arguments={"q": "x"})
    assert call.arguments["q"] == "x"


def _outcome(skill, case, status):
    return CaseOutcome(
        skill_name=skill,
        case_name=case,
        runner="fake",
        status=status,
        scores=[EvalScore(evaluator="assertion", passed=status == "passed", score=1.0, detail="")],
        result=_result(),
    )


def test_run_report_aggregates_counts_and_pass_rate():
    report = RunReport(
        outcomes=[
            _outcome("a", "c1", "passed"),
            _outcome("a", "c2", "failed"),
            _outcome("b", "c3", "errored"),
            _outcome("b", "c4", "passed"),
        ]
    )
    assert report.total == 4
    assert report.passed == 2
    assert report.failed == 1
    assert report.errored == 1
    assert report.pass_rate == 0.5


def test_pass_rate_by_skill_groups_correctly():
    report = RunReport(
        outcomes=[
            _outcome("a", "c1", "passed"),
            _outcome("a", "c2", "failed"),
            _outcome("b", "c3", "passed"),
        ]
    )
    assert report.pass_rate_by_skill() == {"a": 0.5, "b": 1.0}


def test_empty_report_has_zero_pass_rate():
    report = RunReport()
    assert report.total == 0
    assert report.pass_rate == 0.0


def test_tool_spec_rejects_an_unsupported_parameter_type():
    # An unknown type is an authoring mistake in the user's YAML, so it must be
    # rejected at parse time rather than reaching the runner.
    with pytest.raises(ValidationError):
        ToolSpec(name="lookup", parameters={"order_id": "uuid"})


def test_tool_spec_accepts_a_hyphenated_name():
    # MCP servers routinely name tools `get-pull-request`. A mock must answer to
    # the name the live server uses, and both providers accept a hyphen.
    assert ToolSpec(name="get-pull-request").name == "get-pull-request"


@pytest.mark.parametrize("name", ["look up", "a.b", "café", "", "x" * 65])
def test_tool_spec_rejects_a_name_no_provider_would_register(name):
    # ^[A-Za-z0-9_-]{1,64}$ is the rule OpenAI and Anthropic enforce. A name
    # outside it could never be registered, so it is an authoring error.
    with pytest.raises(ValidationError, match="tool name must match"):
        ToolSpec(name=name)


def test_tool_spec_accepts_a_sixty_four_character_name():
    assert len(ToolSpec(name="x" * 64).name) == 64


def test_tool_spec_accepts_a_full_declaration():
    spec = ToolSpec(
        name="lookup_order",
        description="Look up an order by its id",
        parameters={"order_id": "string", "verbose": "boolean"},
        returns='{"id": "1234"}',
    )
    assert spec.parameters["order_id"] == "string"
    assert spec.returns == '{"id": "1234"}'


def test_tool_spec_carries_an_input_schema():
    schema = {"type": "object", "properties": {"owner": {"type": "string"}}}
    spec = ToolSpec(name="get_pull_request", input_schema=schema)
    assert spec.input_schema == schema
    assert spec.parameters == {}


def test_tool_spec_input_schema_defaults_to_none():
    assert ToolSpec(name="ping").input_schema is None


def test_case_carries_tools_trajectory_and_budget():
    case = EvalCase(
        name="refund",
        task="refund order 1234",
        tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})],
        trajectory=TrajectorySpec(called=["lookup_order"], forbidden=["issue_refund"]),
        budget=BudgetSpec(max_tokens=4000),
    )
    assert case.tools[0].name == "lookup_order"
    assert case.trajectory.called == ["lookup_order"]
    assert case.budget.max_tokens == 4000


def test_case_without_the_new_blocks_keeps_working():
    case = EvalCase(name="plain", task="hello")
    assert case.tools == []
    assert case.trajectory is None
    assert case.budget is None


def test_trajectory_spec_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        TrajectorySpec(calls=["lookup_order"])


def test_budget_spec_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        BudgetSpec(max_token=10)


def test_run_result_tokens_is_the_sum_of_input_and_output():
    result = RunResult(input_tokens=112, output_tokens=15)
    assert result.tokens == 127


def test_run_result_rejects_writing_tokens_directly():
    # `tokens` is derived. Accepting it silently would let a runner report a
    # total that disagrees with the split it was priced from.
    with pytest.raises(ValidationError):
        RunResult(tokens=127)


def test_a_case_defaults_to_loaded_mode_with_no_judge():
    case = EvalCase(name="c", task="t")
    assert case.mode == "loaded"
    assert case.judge is None


def test_a_case_can_declare_offered_mode_and_a_rubric():
    case = EvalCase(
        name="c",
        task="t",
        mode="offered",
        judge=JudgeSpec(expected="a plain answer", rubric=["names the order id"]),
        trajectory=TrajectorySpec(skill_triggered=True),
    )
    assert case.mode == "offered"
    assert case.judge.rubric == ["names the order id"]
    assert case.trajectory.skill_triggered is True


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValidationError):
        EvalCase(name="c", task="t", mode="offerred")


def test_a_judge_spec_forbids_unknown_keys():
    # Without extra="forbid" a typo like `rubrics:` yields a vacuously-passing case.
    with pytest.raises(ValidationError):
        JudgeSpec(rubrics=["oops"])


def test_a_result_reports_no_triggering_decision_by_default():
    # None means "this run was not an offered run", which is distinct from False.
    assert RunResult().skill_triggered is None


def test_a_verdict_knows_when_it_errored():
    assert JudgeVerdict().errored is False
    assert JudgeVerdict(error="boom").errored is True


def test_a_request_carries_the_checks_it_wants_graded():
    request = JudgeRequest(
        task="why?",
        output="because",
        checks=[RubricCheck(id="r1", text="explains why")],
    )
    assert [check.id for check in request.checks] == ["r1"]


def test_an_errored_score_cannot_also_be_passed():
    # The two must never disagree: an infra failure is not a green case.
    with pytest.raises(ValidationError):
        EvalScore(evaluator="judge", passed=True, errored=True)


def test_a_score_carries_per_check_evidence():
    score = EvalScore(
        evaluator="judge",
        passed=False,
        checks=[CheckResult(id="r1", passed=False, evidence="never mentions the window")],
    )
    assert score.checks[0].evidence == "never mentions the window"
    assert score.cost_usd == 0.0


def test_judge_cost_is_summed_across_outcomes_and_kept_off_the_run_cost():
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="s",
                case_name="c",
                runner="fake",
                status="passed",
                scores=[EvalScore(evaluator="judge", passed=True, cost_usd=0.002)],
                result=RunResult(cost_usd=0.01),
            )
        ]
    )
    assert report.judge_cost_usd == pytest.approx(0.002)
    assert report.outcomes[0].result.cost_usd == pytest.approx(0.01)


def test_totals_are_summed_across_both_arms():
    # Unlike passed/failed/pass_rate (candidate arm only), the totals read
    # every outcome: a baseline run still spent real tokens and money even
    # though the gate never looks at it.
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="s",
                case_name="c",
                runner="fake",
                status="passed",
                arm="candidate",
                result=RunResult(input_tokens=10, output_tokens=5, cost_usd=0.01, latency_ms=100),
            ),
            CaseOutcome(
                skill_name="s",
                case_name="c",
                runner="fake",
                status="failed",
                arm="baseline",
                result=RunResult(input_tokens=20, output_tokens=5, cost_usd=0.02, latency_ms=200),
            ),
        ]
    )
    assert report.total_tokens == 40
    assert report.total_cost_usd == pytest.approx(0.03)
    assert report.total_latency_ms == 300


def test_pricing_degraded_is_true_when_any_outcome_in_either_arm_has_a_cost_note():
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="s",
                case_name="c",
                runner="fake",
                status="passed",
                arm="candidate",
                result=RunResult(cost_usd=0.0),
            ),
            CaseOutcome(
                skill_name="s",
                case_name="c",
                runner="fake",
                status="failed",
                arm="baseline",
                result=RunResult(cost_usd=0.0, cost_note="no price data for x (KeyError)"),
            ),
        ]
    )
    assert report.pricing_degraded is True


def test_pricing_degraded_is_false_when_no_result_carries_a_cost_note():
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="s",
                case_name="c",
                runner="fake",
                status="passed",
                result=RunResult(cost_usd=0.01),
            )
        ]
    )
    assert report.pricing_degraded is False


def test_workspace_spec_defaults_to_no_files():
    assert WorkspaceSpec().files == {}


def test_workspace_spec_forbids_unknown_keys():
    # Without extra="forbid" a typo like `file:` would yield a workspace that
    # silently seeds nothing.
    with pytest.raises(ValidationError):
        WorkspaceSpec(file={"a.txt": "x"})


def test_assertion_value_is_optional_but_distinguishes_empty_from_absent():
    # `equals` with value "" is a real assertion meaning "the output is empty",
    # so a "" default would make it indistinguishable from a missing field.
    assert AssertionSpec(kind="file-produced", file="report.md").value is None
    assert AssertionSpec(kind="equals", value="").value == ""


def test_assertion_carries_a_file_target_and_an_inline_schema():
    spec = AssertionSpec(
        kind="json-schema",
        file="totals.json",
        json_schema={"type": "object", "required": ["units"]},
    )
    assert spec.file == "totals.json"
    assert spec.json_schema == {"type": "object", "required": ["units"]}


def test_assertion_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        AssertionSpec(kind="contains", value="x", schema={"type": "object"})


def test_case_has_no_workspace_by_default():
    # Every suite that exists today must run byte-identically.
    assert EvalCase(name="n", task="t").workspace is None


def test_case_accepts_a_workspace_block():
    case = EvalCase(name="n", task="t", workspace=WorkspaceSpec(files={"in.csv": "a,b\n1,2\n"}))
    assert case.workspace is not None
    assert case.workspace.files["in.csv"] == "a,b\n1,2\n"


def test_judge_spec_names_no_artifacts_by_default():
    assert JudgeSpec(rubric=["r"]).artifacts == []


def test_run_result_has_no_workspace_by_default():
    assert RunResult().workspace is None


def test_run_result_carries_a_workspace_path():
    result = RunResult(workspace=Path("/tmp/x"))
    assert result.workspace == Path("/tmp/x")


def test_run_result_still_forbids_extra_fields():
    # `tokens` stays derived: writing it must remain a loud error.
    with pytest.raises(ValidationError):
        RunResult(tokens=5)


def test_judge_request_carries_artifacts():
    assert JudgeRequest(task="t").artifacts == {}
    assert JudgeRequest(task="t", artifacts={"r.md": "body"}).artifacts == {"r.md": "body"}


def test_a_skill_has_no_bundle_unless_one_is_set():
    # None is the safe default: a Skill built by hand in a test, and the
    # --baseline none skill the orchestrator builds, must never carry the
    # candidate's scripts.
    skill = Skill(name="pdf", path=Path("/tmp/pdf"))
    assert skill.bundle_root is None


def test_script_status_records_the_backend_and_why():
    status = ScriptStatus(sandbox="none", detail="bwrap not found on PATH")
    assert status.sandbox == "none"
    with pytest.raises(ValidationError):
        ScriptStatus(sandbox="firejail", detail="")


def test_a_run_report_defaults_to_scripts_off():
    report = RunReport()
    assert report.scripts is None
    assert report.script_notes == []
    report = RunReport(
        scripts=ScriptStatus(sandbox="bwrap", detail="bwrap probe succeeded"),
        script_notes=[ScriptNote(skill_name="pdf", script_count=2)],
    )
    assert report.scripts.sandbox == "bwrap"
    assert report.script_notes[0].script_count == 2


def test_run_result_carries_a_usage_note_for_tokens_it_could_not_count():
    result = RunResult(usage_note="copilot did not report token usage")
    assert result.tokens == 0
    assert result.usage_note == "copilot did not report token usage"


def test_a_product_status_is_a_typed_record():
    status = ProductStatus(
        name="copilot", executable="/usr/local/bin/copilot", version="1.0.37", trust="t"
    )
    assert status.model_dump() == {
        "name": "copilot",
        "executable": "/usr/local/bin/copilot",
        "version": "1.0.37",
        "trust": "t",
    }


def test_a_report_lists_no_products_by_default():
    assert RunReport().products == []


def test_a_skill_defaults_to_no_markdown():
    assert Skill(name="s", path=Path("/tmp/s")).markdown == ""


def test_a_tool_ref_carries_a_name_and_optionally_returns():
    assert ToolRef(ref="lookup_order").returns is None
    assert ToolRef(ref="lookup_order", returns="{}").returns == "{}"


def test_a_tool_ref_refuses_any_other_key():
    # The library owns the contract; a case may set only the scenario.
    with pytest.raises(ValidationError, match="description"):
        ToolRef(ref="lookup_order", description="rewritten")
    with pytest.raises(ValidationError, match="ref"):
        ToolRef(returns="{}")
    with pytest.raises(ValidationError, match="ref"):
        ToolRef(ref="")


def test_a_trajectory_may_check_the_arguments_a_tool_was_called_with():
    spec = TrajectorySpec(
        call_args=[CallArgsSpec(tool="list_threads", contains={"status": "active"})]
    )
    assert spec.call_args[0].tool == "list_threads"
    assert spec.call_args[0].contains == {"status": "active"}
    assert spec.call_args[0].equals is None
    assert spec.call_args[0].every is False


def test_a_trajectory_without_call_args_has_an_empty_list():
    assert TrajectorySpec(called=["a"]).call_args == []


def test_a_call_args_entry_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        CallArgsSpec(tool="a", contains={"x": 1}, contain={"x": 1})


def test_a_call_args_entry_needs_contains_or_equals():
    # An entry with no subject would pass on any call: that is `called:`
    # spelled longer, so it is refused rather than honoured vacuously.
    with pytest.raises(ValidationError, match="exactly one of contains or equals"):
        CallArgsSpec(tool="a")


def test_a_call_args_entry_takes_only_one_of_contains_and_equals():
    with pytest.raises(ValidationError, match="exactly one of contains or equals"):
        CallArgsSpec(tool="a", contains={"x": 1}, equals={"x": 1})


def test_an_empty_contains_is_refused():
    # {} is a subset of every argument dict, so the check could never fail.
    with pytest.raises(ValidationError, match="empty contains"):
        CallArgsSpec(tool="a", contains={})


def test_an_empty_equals_means_called_with_no_arguments():
    assert CallArgsSpec(tool="a", equals={}).equals == {}
