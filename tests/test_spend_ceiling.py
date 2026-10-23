"""The run-wide spend ceiling: `max_spend_usd` / `--max-spend` (issue #94).

Once the spend a run has recorded reaches the ceiling, no new work item
starts. Items already running finish and are scored; the rest are reported as
not run, and the gate fails. Everything here is offline: `FakeRunner` results
carry whatever cost a test scripts.
"""

from __future__ import annotations

import json
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_lens.cli import app
from skill_lens.comparison import build_delta
from skill_lens.config import Config, ConfigError, load_config
from skill_lens.gating import evaluate_gate
from skill_lens.models import (
    CaseOutcome,
    EvalCase,
    EvalScore,
    NotRunItem,
    RunReport,
    RunResult,
    Skill,
    SpendCeiling,
)
from skill_lens.orchestrator import RunOptions, run_evals
from skill_lens.reporters.console import render_console
from skill_lens.reporters.json_reporter import render_json
from skill_lens.reporters.junit import render_junit
from skill_lens.reporters.markdown import render_markdown
from skill_lens.runners.fake import FakeRunner
from skill_lens.skills.loader import load_skills


def _skill(tmp_path: Path, count: int = 4) -> list[Skill]:
    """One skill with `count` cases, `case-0` .. `case-N`, each passing under FakeRunner."""
    skill_dir = tmp_path / "spender"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: spender\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    cases = "cases:\n" + "".join(
        f"  - name: case-{i}\n    task: task-{i}\n    assertions:\n"
        f"      - kind: contains\n        value: 'ok'\n"
        for i in range(count)
    )
    (skill_dir / "spender.eval.yaml").write_text(cases, encoding="utf-8")
    return load_skills(skill_dir)


class _CountingRunner(FakeRunner):
    """Answers every case with `result`, and records which tasks it was asked to run."""

    def __init__(self, result: RunResult) -> None:
        super().__init__(default=result)
        self.tasks: list[str] = []
        self._lock = threading.Lock()

    def run(self, skill, case, workspace=None, scripts=None):
        with self._lock:
            self.tasks.append(case.task)
        return super().run(skill, case, workspace=workspace)


def _ceiling(amount: float) -> RunOptions:
    return RunOptions(max_spend_usd=amount)


# --- The orchestrator ---------------------------------------------------------


def test_no_ceiling_runs_every_item_and_reports_none(tmp_path):
    runner = _CountingRunner(RunResult(output="ok", cost_usd=1.0))
    report = run_evals(_skill(tmp_path), [runner])
    assert len(runner.tasks) == 4
    assert report.spend_ceiling is None
    assert report.not_run == []


def test_no_new_item_starts_once_recorded_spend_reaches_the_ceiling(tmp_path):
    runner = _CountingRunner(RunResult(output="ok", cost_usd=0.30))
    report = run_evals(_skill(tmp_path), [runner], options=_ceiling(0.50))
    # 0.30 is under 0.50, so the second item starts; 0.60 is not, so the third does not.
    assert runner.tasks == ["task-0", "task-1"]
    assert [o.case_name for o in report.outcomes] == ["case-0", "case-1"]
    assert [item.case_name for item in report.not_run] == ["case-2", "case-3"]
    assert report.spend_ceiling == SpendCeiling(max_spend_usd=0.50)
    # A ceiling is not an infra failure: nothing errored.
    assert report.errored == 0
    assert report.spent_usd == pytest.approx(0.60)


def test_spend_exactly_at_the_ceiling_stops_new_work(tmp_path):
    runner = _CountingRunner(RunResult(output="ok", cost_usd=0.25))
    report = run_evals(_skill(tmp_path), [runner], options=_ceiling(0.50))
    assert len(runner.tasks) == 2
    assert len(report.not_run) == 2


def test_the_first_item_always_runs(tmp_path):
    runner = _CountingRunner(RunResult(output="ok", cost_usd=5.0))
    report = run_evals(_skill(tmp_path), [runner], options=_ceiling(0.01))
    assert runner.tasks == ["task-0"]
    assert report.total == 1


def test_a_ceiling_that_is_never_reached_runs_everything(tmp_path):
    runner = _CountingRunner(RunResult(output="ok", cost_usd=0.01))
    report = run_evals(_skill(tmp_path), [runner], options=_ceiling(10.0))
    assert len(runner.tasks) == 4
    assert report.not_run == []
    assert report.spend_ceiling == SpendCeiling(max_spend_usd=10.0)


def test_an_errored_runs_spend_counts(tmp_path):
    # Since #91 an errored run keeps what it spent; the ceiling must see it.
    runner = _CountingRunner(RunResult(error="stopped by its budget", cost_usd=0.60))
    report = run_evals(_skill(tmp_path), [runner], options=_ceiling(0.50))
    assert runner.tasks == ["task-0"]
    assert report.errored == 1
    assert len(report.not_run) == 3


class _SpendingEvaluator:
    """An evaluator that spends, as a judge does."""

    name = "judge"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        return EvalScore(evaluator=self.name, passed=True, score=1.0, cost_usd=0.60)


def test_judge_spend_counts(tmp_path):
    runner = _CountingRunner(RunResult(output="ok"))
    report = run_evals(
        _skill(tmp_path), [runner], evaluators=[_SpendingEvaluator()], options=_ceiling(0.50)
    )
    assert runner.tasks == ["task-0"]
    assert report.spent_usd == pytest.approx(0.60)


def test_baseline_spend_counts(tmp_path):
    runner = _CountingRunner(RunResult(output="ok", cost_usd=0.30))
    report = run_evals(_skill(tmp_path), [runner], baseline="none", options=_ceiling(0.50))
    # case-0 candidate, then case-0 baseline: 0.60, so case-1 never starts.
    assert [(o.case_name, o.arm) for o in report.outcomes] == [
        ("case-0", "candidate"),
        ("case-0", "baseline"),
    ]
    assert report.not_run[0] == NotRunItem(
        skill_name="spender", case_name="case-1", runner="fake", arm="candidate"
    )
    assert report.not_run[1].arm == "baseline"


def test_unpriced_spend_stops_the_run_and_names_the_outcome(tmp_path):
    # A ceiling that cannot see what was spent cannot be honoured, and an
    # unpriced 0.0 is never assumed to be free.
    runner = _CountingRunner(
        RunResult(output="ok", cost_note="no price data for x:y (LookupError)")
    )
    report = run_evals(_skill(tmp_path), [runner], options=_ceiling(0.50))
    assert runner.tasks == ["task-0"]
    assert len(report.not_run) == 3
    assert report.spend_ceiling is not None
    assert "spender :: case-0 (fake)" in report.spend_ceiling.unpriced
    assert "no price data" in report.spend_ceiling.unpriced


class _UnpricedJudge:
    name = "judge"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        return EvalScore(evaluator=self.name, passed=True, score=1.0, cost_note="not priced")


def test_unpriced_judge_spend_stops_the_run_too(tmp_path):
    runner = _CountingRunner(RunResult(output="ok"))
    report = run_evals(
        _skill(tmp_path), [runner], evaluators=[_UnpricedJudge()], options=_ceiling(0.50)
    )
    assert runner.tasks == ["task-0"]
    assert "not priced" in report.spend_ceiling.unpriced


def test_unpriced_spend_without_a_ceiling_changes_nothing(tmp_path):
    runner = _CountingRunner(RunResult(output="ok", cost_note="no price data"))
    report = run_evals(_skill(tmp_path), [runner])
    assert len(runner.tasks) == 4
    assert report.spend_ceiling is None


class _BarrierRunner(FakeRunner):
    """The first two items wait for each other, so both are in flight at once."""

    def __init__(self) -> None:
        super().__init__(default=RunResult(output="ok", cost_usd=1.0))
        self.barrier = threading.Barrier(2, timeout=10)
        self.tasks: list[str] = []
        self._lock = threading.Lock()

    def run(self, skill, case, workspace=None, scripts=None):
        with self._lock:
            self.tasks.append(case.task)
        if case.task in ("task-0", "task-1"):
            self.barrier.wait()
        return super().run(skill, case, workspace=workspace)


def test_items_already_in_flight_finish_under_concurrency(tmp_path):
    runner = _BarrierRunner()
    report = run_evals(_skill(tmp_path), [runner], concurrency=2, options=_ceiling(0.50))
    # Both first items started before either finished, so both run and are
    # scored even though the first one alone crossed the ceiling. A worker
    # records an item's spend before it picks up the next, so the third never
    # starts.
    assert sorted(runner.tasks) == ["task-0", "task-1"]
    assert [o.case_name for o in report.outcomes] == ["case-0", "case-1"]
    assert [o.status for o in report.outcomes] == ["passed", "passed"]
    assert [item.case_name for item in report.not_run] == ["case-2", "case-3"]
    assert report.spent_usd == pytest.approx(2.0)


@pytest.mark.parametrize("amount", [0.0, -1.0, float("inf"), float("nan")])
def test_a_ceiling_must_be_a_positive_finite_amount(tmp_path, amount):
    with pytest.raises(ValueError, match="max_spend_usd"):
        run_evals(_skill(tmp_path), [FakeRunner()], options=_ceiling(amount))


# --- The gate -----------------------------------------------------------------


def _outcome(case: str, status: str = "passed", arm: str = "candidate", **result) -> CaseOutcome:
    return CaseOutcome(
        skill_name="s",
        case_name=case,
        runner="fake",
        status=status,
        arm=arm,
        result=RunResult(**result),
    )


def _cut_report(**kwargs) -> RunReport:
    return RunReport(
        outcomes=[_outcome("a", cost_usd=0.6)],
        not_run=[NotRunItem(skill_name="s", case_name="b", runner="fake")],
        spend_ceiling=SpendCeiling(max_spend_usd=0.5),
        **kwargs,
    )


def test_a_run_the_ceiling_cut_short_fails_the_gate_though_everything_that_ran_passed():
    gate = evaluate_gate(_cut_report())
    assert gate.passed is False
    assert gate.exit_code == 1
    assert any("1 case did not run: " in r and "$0.50" in r for r in gate.reasons)


def test_the_cut_fails_the_gate_whatever_the_pass_rate_and_error_settings():
    gate = evaluate_gate(_cut_report(), min_pass_rate=0.0, fail_on_error=False)
    assert gate.passed is False


def test_a_ceiling_reached_by_the_last_item_does_not_fail_the_gate():
    report = RunReport(
        outcomes=[_outcome("a", cost_usd=0.6)], spend_ceiling=SpendCeiling(max_spend_usd=0.5)
    )
    assert evaluate_gate(report).passed is True


def test_a_baseline_item_left_unrun_fails_the_gate_too():
    report = RunReport(
        outcomes=[_outcome("a", cost_usd=0.6)],
        not_run=[NotRunItem(skill_name="s", case_name="a", runner="fake", arm="baseline")],
        spend_ceiling=SpendCeiling(max_spend_usd=0.5),
        baseline_kind="none",
    )
    gate = evaluate_gate(report)
    assert gate.passed is False
    assert any("1 case did not run (0 candidate, 1 baseline)" in r for r in gate.reasons)


def test_unpriced_spend_under_a_ceiling_fails_the_gate_even_when_nothing_was_cut():
    report = RunReport(
        outcomes=[_outcome("a", cost_note="no price data")],
        spend_ceiling=SpendCeiling(max_spend_usd=0.5, unpriced="s :: a (fake): no price data"),
    )
    gate = evaluate_gate(report)
    assert gate.passed is False
    assert any("could not be enforced" in r and "no price data" in r for r in gate.reasons)


# --- The delta ----------------------------------------------------------------


def test_a_case_the_ceiling_cut_is_excluded_from_both_arms_of_the_delta():
    report = RunReport(
        outcomes=[
            _outcome("a", cost_usd=0.6),
            _outcome("a", arm="baseline", status="failed"),
            _outcome("b", cost_usd=0.6),
        ],
        not_run=[NotRunItem(skill_name="s", case_name="b", runner="fake", arm="baseline")],
        spend_ceiling=SpendCeiling(max_spend_usd=0.5),
        baseline_kind="none",
    )
    delta = build_delta(report)
    by_case = {case.case_name: case for case in delta.cases}
    assert by_case["a"].comparable is True
    assert by_case["b"].comparable is False
    assert "spend ceiling" in by_case["b"].exclusion_reason
    # Only `a` reaches the aggregate.
    assert delta.pass_rate_delta == pytest.approx(1.0)


def test_a_case_cut_part_way_through_its_repetitions_is_excluded():
    report = RunReport(
        outcomes=[
            _outcome("a"),
            _outcome("a"),
            _outcome("a", arm="baseline", status="failed"),
        ],
        not_run=[
            NotRunItem(skill_name="s", case_name="a", runner="fake", arm="baseline", repeat_index=1)
        ],
        spend_ceiling=SpendCeiling(max_spend_usd=0.5),
        baseline_kind="none",
        repeat=2,
    )
    assert build_delta(report).cases[0].comparable is False


# --- The reporters ------------------------------------------------------------


def test_the_console_names_what_did_not_run_and_the_ceiling():
    report = _cut_report()
    text = render_console(report, gate=evaluate_gate(report))
    assert "1 passed, 0 failed, 0 errored, 1 not run" in text
    assert "Not run (stopped by the spend ceiling):" in text
    assert "s :: b (fake)" in text
    assert "Spend ceiling: $0.50" in text


def test_the_console_is_unchanged_without_a_ceiling():
    report = RunReport(outcomes=[_outcome("a")])
    text = render_console(report)
    assert "not run" not in text
    assert "Spend ceiling" not in text


def test_the_json_report_carries_the_ceiling_and_every_item_not_run():
    report = _cut_report()
    payload = json.loads(render_json(report, gate=evaluate_gate(report)))
    assert payload["summary"]["not_run"] == 1
    assert payload["not_run"] == [
        {
            "skill_name": "s",
            "case_name": "b",
            "runner": "fake",
            "arm": "candidate",
            "repeat_index": 0,
        }
    ]
    assert payload["spend_ceiling"] == {
        "max_spend_usd": 0.5,
        "spent_usd": pytest.approx(0.6),
        "unpriced": "",
    }


def test_the_json_report_has_no_ceiling_when_none_was_set():
    payload = json.loads(render_json(RunReport(outcomes=[_outcome("a")])))
    assert payload["spend_ceiling"] is None
    assert payload["not_run"] == []
    assert payload["summary"]["not_run"] == 0


def test_junit_skips_each_item_not_run_and_errors_the_run_so_it_is_never_green():
    report = _cut_report()
    root = ET.fromstring(render_junit(report, gate=evaluate_gate(report)))
    skipped = [case for case in root.iter("testcase") if case.find("skipped") is not None]
    assert [case.get("name") for case in skipped] == ["b"]
    assert "spend ceiling" in skipped[0].find("skipped").get("message")
    errors = [case for case in root.iter("testcase") if case.find("error") is not None]
    assert [case.get("name") for case in errors] == ["spend ceiling"]
    assert root.get("skipped") == "1"
    assert root.get("errors") == "1"
    assert root.get("tests") == "3"


def test_junit_names_only_candidate_items_not_run():
    report = RunReport(
        outcomes=[_outcome("a", cost_usd=0.6)],
        not_run=[NotRunItem(skill_name="s", case_name="a", runner="fake", arm="baseline")],
        spend_ceiling=SpendCeiling(max_spend_usd=0.5),
        baseline_kind="none",
    )
    root = ET.fromstring(render_junit(report, gate=evaluate_gate(report)))
    assert [case for case in root.iter("testcase") if case.find("skipped") is not None] == []
    # The run is still red: the comparison it was asked for did not finish.
    assert root.get("errors") == "1"


def test_markdown_counts_what_did_not_run_and_lists_it():
    report = _cut_report()
    text = render_markdown(report, gate=evaluate_gate(report))
    assert "1 not run" in text
    assert "Spend ceiling" in text
    assert "Not run (1)" in text


# --- Configuration and the flag ----------------------------------------------


def test_the_config_key_defaults_to_no_ceiling():
    assert Config().max_spend_usd is None


@pytest.mark.parametrize("text", ["0", "-1", "inf", "nan"])
def test_the_config_key_refuses_a_ceiling_that_is_not_a_positive_amount(tmp_path, text):
    path = tmp_path / "skill-lens.toml"
    path.write_text(f"max_spend_usd = {text}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_spend_usd"):
        load_config(path)


def _cli_skill(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    _skill(root, count=1)
    return root


def _spend_ceiling(out: Path) -> dict | None:
    return json.loads(out.read_text(encoding="utf-8"))["spend_ceiling"]


def test_the_flag_reaches_the_report(tmp_path):
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        app,
        ["run", str(_cli_skill(tmp_path)), "--max-spend", "2.5", "--json-output", str(out)],
    )
    assert result.exit_code == 1, result.stdout  # FakeRunner's "[fake] ..." lacks "ok"
    assert _spend_ceiling(out)["max_spend_usd"] == 2.5


def test_the_flag_overrides_the_config_file(tmp_path):
    config = tmp_path / "skill-lens.toml"
    config.write_text("max_spend_usd = 1.0\n", encoding="utf-8")
    out = tmp_path / "report.json"
    args = ["run", str(_cli_skill(tmp_path)), "--config", str(config), "--json-output", str(out)]
    CliRunner().invoke(app, args)
    assert _spend_ceiling(out)["max_spend_usd"] == 1.0
    CliRunner().invoke(app, [*args, "--max-spend", "3"])
    assert _spend_ceiling(out)["max_spend_usd"] == 3.0


@pytest.mark.parametrize("value", ["0", "-2", "inf", "nan"])
def test_a_flag_that_is_not_a_positive_amount_is_a_user_error(tmp_path, value):
    result = CliRunner().invoke(app, ["run", str(_cli_skill(tmp_path)), "--max-spend", value])
    assert result.exit_code == 2


# --- Review follow-ups ---------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "spelled"),
    [
        (0.5, "$0.50"),
        (5.0, "$5.00"),
        (0.30000000000000004, "$0.30"),
        (0.005, "$0.005"),
        (1e-07, "$0.0000001"),
        (4e-07, "$0.0000004"),
        (1e-12, "$0.000000000001"),
    ],
)
def test_a_dollar_amount_is_spelled_as_set_and_never_as_a_bare_point(amount, spelled):
    from skill_lens.gating import usd

    assert usd(amount) == spelled


def test_every_reporter_labels_a_repetition_not_run_the_same_way():
    report = RunReport(
        outcomes=[_outcome("a", cost_usd=0.6)],
        not_run=[
            NotRunItem(skill_name="s", case_name="a", runner="fake", repeat_index=1),
        ],
        spend_ceiling=SpendCeiling(max_spend_usd=0.5),
        repeat=2,
    )
    gate = evaluate_gate(report)
    assert "s :: a (fake, run 2/2)" in render_console(report, gate=gate)
    assert "`s` :: `a` (fake, run 2/2)" in render_markdown(report, gate=gate)


def test_the_cost_of_discarded_retries_counts_toward_the_ceiling(tmp_path):
    runner = _CountingRunner(RunResult(output="ok", cost_usd=0.10, retry_cost_usd=0.45))
    report = run_evals(_skill(tmp_path), [runner], options=_ceiling(0.50))
    assert runner.tasks == ["task-0"]
    assert report.spent_usd == pytest.approx(0.55)
    assert report.total_cost_usd == pytest.approx(0.55)


def test_an_outcomes_spend_is_its_run_its_retries_and_its_judges():
    outcome = CaseOutcome(
        skill_name="s",
        case_name="a",
        runner="fake",
        status="passed",
        result=RunResult(cost_usd=0.1, retry_cost_usd=0.2),
        scores=[EvalScore(evaluator="judge", passed=True, cost_usd=0.3, cost_note="x")],
    )
    assert outcome.spent_usd == pytest.approx(0.6)
    assert outcome.unpriced_note == "x"


class _UnpricedRunner(_CountingRunner):
    """A runner that knows before it starts that its spend is never priced."""

    def __init__(self) -> None:
        super().__init__(RunResult(output="ok"))

    def unpriced(self) -> str:
        return "bills per request, not in US dollars"


def test_a_ceiling_a_runner_can_never_price_is_refused_before_any_case(tmp_path):
    from skill_lens.orchestrator import SpendCeilingError

    runner = _UnpricedRunner()
    with pytest.raises(SpendCeilingError, match="runner fake: bills per request"):
        run_evals(_skill(tmp_path), [runner], options=_ceiling(5.0))
    assert runner.tasks == []


def test_a_ceiling_a_judge_can_never_price_is_refused_too(tmp_path):
    from skill_lens.judges.fake import FakeJudge
    from skill_lens.orchestrator import SpendCeilingError

    class _UnpricedJudge(FakeJudge):
        def unpriced(self) -> str:
            return "no cost reported"

    with pytest.raises(SpendCeilingError, match="judge fake: no cost reported"):
        run_evals(_skill(tmp_path), [FakeRunner()], judge=_UnpricedJudge(), options=_ceiling(5.0))


def test_an_unpriceable_runner_without_a_ceiling_runs(tmp_path):
    runner = _UnpricedRunner()
    run_evals(_skill(tmp_path), [runner])
    assert len(runner.tasks) == 4


def test_which_seats_can_never_be_priced():
    from skill_lens.runners.langchain import LangChainRunner
    from skill_lens.runners.product import PRESETS, ProductRunner
    from skill_lens.runners.pydantic_ai import PydanticAIRunner

    assert "premium request" in ProductRunner(PRESETS["copilot"]).unpriced()
    assert ProductRunner(PRESETS["claude-code"]).unpriced() == ""
    cli = Config(runners={"cli": {"command": ["agent", "{prompt}"]}}).product("cli")
    assert "does not report cost" in ProductRunner(cli).unpriced()
    assert PydanticAIRunner(model="openai:gpt-4o-mini").unpriced() == ""
    assert "no price data" in PydanticAIRunner(model="ollama:no-such-model").unpriced()
    assert "no price data" in LangChainRunner(model="ollama:no-such-model").unpriced()


def test_the_cli_refuses_a_ceiling_it_could_never_enforce(tmp_path):
    config = tmp_path / "skill-lens.toml"
    config.write_text(
        'judge = "pydantic-ai"\njudge_model = "ollama:no-such-model"\n', encoding="utf-8"
    )
    result = CliRunner().invoke(
        app,
        ["run", str(_cli_skill(tmp_path)), "--config", str(config), "--max-spend", "1"],
    )
    assert result.exit_code == 2
    assert "max_spend_usd cannot be enforced for judge pydantic-ai" in result.stdout


def _looping_model():
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    return FunctionModel(
        lambda messages, info: ModelResponse(
            parts=[ToolCallPart(tool_name="poll", args={})],
            usage=RequestUsage(input_tokens=40, output_tokens=20),
        )
    )


def _budgeted_skill(tmp_path: Path) -> list[Skill]:
    skill_dir = tmp_path / "budgeted"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: budgeted\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    (skill_dir / "budgeted.eval.yaml").write_text(
        "cases:\n"
        "  - name: loops\n    task: go\n"
        "    tools:\n      - name: poll\n"
        "    budget:\n      max_tokens: 100\n",
        encoding="utf-8",
    )
    return load_skills(skill_dir)


def test_a_case_stopped_at_its_budget_fails_and_stays_in_the_delta(tmp_path):
    from skill_lens.runners.pydantic_ai import PydanticAIRunner

    report = run_evals(
        _budgeted_skill(tmp_path), [PydanticAIRunner(model=_looping_model())], baseline="none"
    )
    # Both arms ran away and were stopped: failed, not errored, so the pair
    # is still compared rather than dropped as "every repetition errored".
    assert [(o.arm, o.status) for o in report.outcomes] == [
        ("candidate", "failed"),
        ("baseline", "failed"),
    ]
    assert report.errored == 0
    assert report.baseline_errored == 0
    delta = build_delta(report)
    assert delta.cases[0].comparable is True
    evidence = report.outcomes[0].scores[2].checks[0].evidence
    assert "stopped the run before the agent finished" in evidence
