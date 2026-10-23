"""ProductRunner against the fake product: the real subprocess path, offline."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from skill_lens.models import EvalCase, Skill, ToolSpec, WorkspaceSpec
from skill_lens.orchestrator import run_evals
from skill_lens.reporters.json_reporter import render_json
from skill_lens.runners.mcp import CLAUDE_CODE_MCP, COPILOT_MCP
from skill_lens.runners.product import (
    PRESETS,
    Invocation,
    Product,
    ProductRunner,
    deliver_skill,
    delivered_paths,
    read_trace,
)
from skill_lens.runners.traces import parse_claude_code, parse_copilot
from skill_lens.workspace import Workspace, create_workspace

FAKE = Path(__file__).parent / "fake_product.py"
FIXTURES = Path(__file__).parent / "fixtures" / "products"

SKILL_MD = "---\nname: ping\ndescription: Ping.\nallowed-tools: Bash\n---\n\nReply PONG-7731.\n"


def _product(parse=parse_claude_code, **overrides) -> Product:
    fields = dict(
        name="claude-code",
        argv=(sys.executable, str(FAKE), "-p", "{prompt}", "--flag"),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse,
        version_command=(sys.executable, str(FAKE), "--version"),
    )
    fields.update(overrides)
    return Product(**fields)


def _skill(tmp_path, markdown=SKILL_MD, bundle=False) -> Skill:
    root = tmp_path / "ping"
    root.mkdir(exist_ok=True)
    bundle_root = None
    if bundle:
        (root / "scripts").mkdir(exist_ok=True)
        (root / "scripts" / "count.py").write_text("print(1)", encoding="utf-8")
        (root / "references").mkdir(exist_ok=True)
        (root / "references" / "notes.md").write_text("notes", encoding="utf-8")
        (root / "ping.eval.yaml").write_text("cases: []", encoding="utf-8")
        bundle_root = root.resolve()
    return Skill(
        name="ping",
        description="Ping.",
        instructions="Reply PONG-7731.",
        path=root,
        markdown=markdown,
        bundle_root=bundle_root,
    )


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Point the fake at a trace and a record file; return a reader for the record."""
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_PRODUCT_RECORD", str(record))
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-trigger.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_SKILLS_DIR", ".claude/skills")
    monkeypatch.delenv("FAKE_PRODUCT_MODE", raising=False)

    def read():
        return json.loads(record.read_text(encoding="utf-8"))

    return read


def _case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "Please ping.")
    return EvalCase(**kwargs)


# --- presets ---


def test_the_presets_are_the_verified_spellings():
    copilot = PRESETS["copilot"]
    assert copilot.argv == (
        "copilot",
        "-p",
        "{prompt}",
        "--allow-all-tools",
        "--output-format",
        "json",
        "--no-custom-instructions",
        "--no-auto-update",
    )
    assert copilot.skills_dir == ".agents/skills"
    assert copilot.invoke == "/{name} {task}"
    assert copilot.parse is parse_copilot
    assert copilot.version_command == ("copilot", "--version")
    assert copilot.judge_args == ("--available-tools=skill-lens-none",)
    assert copilot.tool_flag == "--available-tools"
    claude = PRESETS["claude-code"]
    assert claude.argv == (
        "claude",
        "-p",
        "{prompt}",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--no-session-persistence",
    )
    assert claude.skills_dir == ".claude/skills"
    assert claude.parse is parse_claude_code
    assert claude.version_command == ("claude", "--version")
    assert claude.judge_args == ("--tools", "")
    assert claude.tool_flag == "--tools"
    assert copilot.mcp is COPILOT_MCP
    assert claude.mcp is CLAUDE_CODE_MCP
    assert set(PRESETS) == {"copilot", "claude-code"}
    for preset in PRESETS.values():
        assert preset.timeout_seconds == 600.0
        assert preset.max_output_bytes == 8_000_000


# --- delivery ---


def test_deliver_skill_writes_the_markdown_verbatim_and_the_bundle(tmp_path):
    skill = _skill(tmp_path, bundle=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    assert deliver_skill(skill, cwd, ".agents/skills") is True
    target = cwd / ".agents" / "skills" / "ping"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    assert sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()) == [
        "SKILL.md",
        "references/notes.md",
        "scripts/count.py",
    ]  # the eval file beside SKILL.md is never delivered


def test_deliver_skill_writes_every_shipped_file_but_no_eval_file(tmp_path):
    """Issue #92: templates/ and reference.md reach the product, as an install
    copies them; eval files at any depth, .git and the on-disk SKILL.md do not
    -- SKILL.md is the skill's markdown, which a baseline takes from git."""
    root = tmp_path / "ping"
    files = {
        "SKILL.md": "on disk, not delivered",
        "reference.md": "ref",
        "templates/plan.md": "plan",
        "references/notes.md": "notes",
        "ping.eval.yaml": "cases: []",
        "evals/cases.yaml": "cases: []",
        "references/deep/x.eval.yaml": "cases: []",
        "assets/evals.json": "{}",
        ".git/HEAD": "ref: refs/heads/main",
    }
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(content, encoding="utf-8")
    skill = _skill(tmp_path).model_copy(update={"bundle_root": root.resolve()})
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    assert deliver_skill(skill, cwd, ".agents/skills") is True
    target = cwd / ".agents" / "skills" / "ping"
    shipped = ["SKILL.md", "reference.md", "references/notes.md", "templates/plan.md"]
    assert sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()) == (
        shipped
    )
    assert (target / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    assert delivered_paths(skill, ".agents/skills") == [
        f".agents/skills/ping/{relative}" for relative in shipped
    ]


def test_deliver_skill_writes_nothing_for_a_skill_with_no_markdown(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    assert deliver_skill(_skill(tmp_path, markdown=""), cwd, ".agents/skills") is False
    assert list(cwd.iterdir()) == []


# --- run: the happy path ---


def test_run_delivers_the_skill_into_a_private_cwd_and_reads_the_trace(tmp_path, fake):
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error is None
    assert result.output == "PONG-7731"
    assert [call.name for call in result.tool_calls] == ["Skill", "Bash"]
    assert result.input_tokens == 464 and result.output_tokens == 19
    assert result.cost_usd == pytest.approx(0.273076)
    assert result.model == "claude-opus-5[1m]"
    assert result.latency_ms >= 0
    assert result.skill_triggered is None  # loaded mode
    seen = fake()
    assert seen["skill_files"] == ["ping/SKILL.md"]
    assert seen["argv"][-1] == "--flag"
    assert not Path(seen["cwd"]).exists()  # the private directory is gone


def test_loaded_mode_invokes_the_skill_by_the_products_spelling(tmp_path, fake):
    ProductRunner(_product()).run(_skill(tmp_path), _case(task="Please ping."))
    assert fake()["prompt"] == "/ping Please ping."


def test_offered_mode_sends_the_bare_task_and_reads_the_load_event(tmp_path, fake):
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(mode="offered"))
    assert fake()["prompt"] == "Please ping."
    assert result.skill_triggered is True


def test_offered_mode_negative_control_is_false(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-negative.jsonl"))
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(mode="offered"))
    assert result.skill_triggered is False


def test_offered_mode_under_copilot_reads_the_skill_tool_request(tmp_path, fake, monkeypatch):
    # Copilot emits `skill.invoked` for a slash invocation only; a skill the
    # model chose is a `skill` tool request (issue #50).
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "copilot-offered-trigger.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_SKILLS_DIR", ".agents/skills")
    product = _product(name="copilot", parse=parse_copilot, skills_dir=".agents/skills")
    result = ProductRunner(product).run(_skill(tmp_path), _case(mode="offered"))
    assert result.error is None
    assert fake()["prompt"] == "Please ping."
    assert fake()["skill_files"] == ["ping/SKILL.md"]
    assert [call.name for call in result.tool_calls] == ["skill"]
    assert result.skill_triggered is True


def test_offered_mode_under_copilot_negative_control_is_false(tmp_path, fake, monkeypatch):
    # The same recording with the model asking for a different skill.
    text = (FIXTURES / "copilot-offered-trigger.jsonl").read_text(encoding="utf-8")
    trace = tmp_path / "other.jsonl"
    trace.write_text(text.replace('"skill":"ping"', '"skill":"other"'), encoding="utf-8")
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(trace))
    product = _product(name="copilot", parse=parse_copilot, skills_dir=".agents/skills")
    result = ProductRunner(product).run(_skill(tmp_path), _case(mode="offered"))
    assert result.error is None
    # The request was read (it names the other skill); it just is not ours.
    assert [call.arguments for call in result.tool_calls] == [{"skill": "other"}]
    assert result.skill_triggered is False


def test_offered_mode_on_a_generic_product_is_unknown_not_false(tmp_path, fake):
    # A generic product has no parser, so `invoked_skills` is always empty --
    # reporting False there would claim "the skill did not fire" when the
    # runner never looked.
    product = _product(name="cli", parse=None, invoke="{task}", version_command=None)
    result = ProductRunner(product).run(_skill(tmp_path), _case(mode="offered"))
    assert result.skill_triggered is None


def test_the_baseline_none_skill_gets_no_directory_and_the_bare_task(tmp_path, fake):
    baseline = Skill(
        name="ping", description="", instructions="", path=tmp_path, variant="baseline"
    )
    ProductRunner(_product()).run(baseline, _case())  # loaded mode, nothing to invoke
    seen = fake()
    assert seen["skill_files"] == []
    assert seen["prompt"] == "Please ping."


def test_a_workspace_is_the_cwd_and_keeps_the_delivered_skill(tmp_path, fake):
    workspace = create_workspace(WorkspaceSpec(files={"in.txt": "hi"}), label="t")
    try:
        ProductRunner(_product()).run(
            _skill(tmp_path), _case(workspace=WorkspaceSpec()), workspace=workspace
        )
        seen = fake()
        assert Path(seen["cwd"]) == workspace.root
        assert (workspace.root / ".claude" / "skills" / "ping" / "SKILL.md").is_file()
        assert (workspace.root / "in.txt").is_file()
    finally:
        workspace.cleanup()


def test_scripts_is_accepted_and_ignored(tmp_path, fake):
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(), scripts=object())
    assert result.error is None


def test_a_generic_product_uses_stdout_and_reports_no_usage(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-negative.jsonl"))
    product = _product(name="cli", parse=None, invoke="{task}", version_command=None)
    result = ProductRunner(product).run(_skill(tmp_path), _case())
    assert result.error is None
    assert result.output.startswith('{"type":"system"')  # stdout verbatim
    assert not result.output.endswith("\n")
    assert result.tool_calls == []
    assert result.usage_note == "the cli product does not report token usage"
    assert result.cost_note == "the cli product does not report cost"
    assert result.model == ""
    assert fake()["prompt"] == "Please ping."


def test_the_runner_name_is_the_products(tmp_path):
    assert ProductRunner(_product(name="copilot")).name == "copilot"
    assert ProductRunner(_product()).needs_api_key is False


# --- run: every failure is RunResult.error ---


def test_a_timeout_kills_the_product_and_is_an_error(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "sleep")
    result = ProductRunner(_product(timeout_seconds=0.5)).run(_skill(tmp_path), _case())
    assert result.error == "claude-code timed out after 0.5s"


def test_a_non_zero_exit_is_an_error_with_the_stderr_tail(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "exit3")
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "claude-code exited with code 3: boom"


def test_a_trace_with_no_result_event_is_an_error(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "garbage")
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "no result event in the claude-code trace"


def test_the_products_own_failure_is_the_error(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-error.jsonl"))
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "claude-code: API Error: 401 authentication_error"
    assert result.model == "claude-opus-5[1m]"


def test_an_errored_run_keeps_the_spend_the_trace_reported(tmp_path, fake, monkeypatch):
    # A run stopped by `--max-budget-usd` is errored, but it spent money: the
    # report's totals must count it, not read it as free.
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-budget-stop.jsonl"))
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "claude-code: error_max_budget_usd"
    assert result.input_tokens == 12 + 2000 + 1500
    assert result.output_tokens == 310
    assert result.cost_usd == pytest.approx(0.0418)
    assert result.cost_note == ""
    assert result.usage_note == ""


def test_a_failed_exit_with_a_complete_trace_keeps_the_spend(tmp_path, fake, monkeypatch):
    # Claude Code exits non-zero on an error result. The complete trace is
    # still the explanation, and its spend is still the run's.
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-budget-stop.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "failed")
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "claude-code: error_max_budget_usd"
    assert result.input_tokens == 12 + 2000 + 1500
    assert result.output_tokens == 310
    assert result.cost_usd == pytest.approx(0.0418)


def test_the_report_counts_an_errored_runs_spend(tmp_path, fake, monkeypatch):
    # The point of keeping the spend: the run's totals and the JSON report
    # include it, while the case stays errored and nothing scores it.
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-budget-stop.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "failed")
    skill = _skill(tmp_path)
    (skill.path / "ping.eval.yaml").write_text(
        "cases:\n  - name: c\n    task: Please ping.\n"
        "    assertions:\n      - kind: contains\n        value: PONG-7731\n",
        encoding="utf-8",
    )
    report = run_evals([skill], [ProductRunner(_product())])
    (outcome,) = report.outcomes
    assert outcome.status == "errored"
    assert outcome.scores == []
    assert report.total_tokens == 12 + 2000 + 1500 + 310
    assert report.total_cost_usd == pytest.approx(0.0418)
    payload = json.loads(render_json(report))
    assert payload["summary"]["total_cost_usd"] == pytest.approx(0.0418)
    assert payload["outcomes"][0]["cost_usd"] == pytest.approx(0.0418)


def test_an_errored_copilot_run_keeps_the_traces_notes(tmp_path, fake, monkeypatch):
    # Copilot prices per premium request and this trace has no shutdown
    # metrics: the notes say so on the errored result too, so a 0.0 there is
    # never read as "this run was free".
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "copilot-error.jsonl"))
    result = ProductRunner(_product(parse=parse_copilot, name="copilot")).run(
        _skill(tmp_path), _case()
    )
    assert result.error is not None
    assert result.cost_note == (
        "copilot bills per premium request, not per token; 0 premium request(s)"
    )
    assert result.usage_note == "copilot did not report token usage"


def test_a_truncated_trace_is_an_error_naming_the_cap(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "huge")
    monkeypatch.setenv("FAKE_PRODUCT_BYTES", "5000")
    result = ProductRunner(_product(max_output_bytes=4000)).run(_skill(tmp_path), _case())
    assert result.error == (
        "claude-code output exceeded 4000 bytes; raise [runners.claude-code] max_output_bytes"
    )


def test_a_missing_executable_is_an_error_not_a_raise(tmp_path, fake):
    product = _product(argv=("/nonexistent/product", "-p", "{prompt}"))
    result = ProductRunner(product).run(_skill(tmp_path), _case())
    assert result.error is not None
    assert result.error.startswith("cannot start /nonexistent/product:")


def test_an_unsafe_skill_name_is_refused_before_writing_anything(tmp_path):
    # A `--baseline previous` skill's name comes from a historical SKILL.md's
    # frontmatter, which never passes the once-per-run preflight a candidate
    # skill's name does -- so deliver_skill must guard it itself.
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    workspace = Workspace(root=ws_root)
    skill = Skill(name="../escape", description="d", instructions="i", path=tmp_path, markdown="m")
    result = ProductRunner(_product()).run(skill, _case(), workspace=workspace)
    assert result.error is not None
    assert result.error.startswith("skill name '../escape' cannot be a directory name")
    assert list(ws_root.rglob("*")) == []  # nothing was written
    assert not any(p.name == "escape" for p in tmp_path.iterdir())


def test_an_oversized_prompt_is_refused_before_anything_starts(tmp_path, fake):
    task = "x" * (100 * 1024 + 1)
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(task=task))
    size = len(f"/ping {task}".encode())
    assert result.error == (
        f"prompt is {size} bytes; a product runner sends at most 102400 bytes as one argument"
    )


def test_the_private_cwd_is_removed_even_on_failure(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "exit3")
    ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert not Path(fake()["cwd"]).exists()


# --- run: a run that ended before reporting its spend says so ---
#
# The product ran, so it may have spent money. With empty notes, the report's
# totals would read its 0.0 as "free".


def test_a_timed_out_run_says_its_spend_is_unknown(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "sleep")
    result = ProductRunner(_product(timeout_seconds=0.5)).run(_skill(tmp_path), _case())
    assert result.cost_usd == 0.0
    assert result.cost_note == "claude-code timed out before reporting cost"
    assert result.usage_note == "claude-code timed out before reporting token usage"


def test_a_cut_trace_says_its_spend_is_unknown(tmp_path, fake, monkeypatch):
    # The cap falls inside the trace, before its `result` event.
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "huge")
    monkeypatch.setenv("FAKE_PRODUCT_BYTES", "5000")
    result = ProductRunner(_product(max_output_bytes=500)).run(_skill(tmp_path), _case())
    assert result.error is not None
    assert result.cost_note == (
        "the claude-code trace was cut at max_output_bytes before reporting cost"
    )
    assert result.usage_note == (
        "the claude-code trace was cut at max_output_bytes before reporting token usage"
    )


def test_a_trace_with_no_result_event_says_its_spend_is_unknown(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "garbage")
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.cost_note == "the claude-code trace ended before reporting cost"
    assert result.usage_note == "the claude-code trace ended before reporting token usage"


def test_a_non_zero_exit_with_no_result_event_says_its_spend_is_unknown(
    tmp_path, fake, monkeypatch
):
    # The exit code explains the error; the notes still explain the 0.0. A
    # product that exits before reaching the model (a bad flag) gets the same
    # notes: with no `result` event, nothing tells the two apart.
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "exit3")
    result = ProductRunner(_product()).run(_skill(tmp_path), _case())
    assert result.error == "claude-code exited with code 3: boom"
    assert result.cost_note == "the claude-code trace ended before reporting cost"
    assert result.usage_note == "the claude-code trace ended before reporting token usage"


def _copilot() -> Product:
    return _product(parse=parse_copilot, name="copilot")


COPILOT_PREMIUM = "copilot bills per premium request, not per token; "


def test_a_timed_out_copilot_run_says_its_spend_is_unknown(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "sleep")
    result = ProductRunner(replace(_copilot(), timeout_seconds=0.5)).run(_skill(tmp_path), _case())
    assert result.error == "copilot timed out after 0.5s"
    assert result.cost_note == COPILOT_PREMIUM + (
        "copilot timed out before reporting premium requests"
    )
    assert result.usage_note == "copilot timed out before reporting token usage"


def test_a_cut_copilot_trace_says_its_spend_is_unknown(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "copilot-trigger.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "huge")
    result = ProductRunner(replace(_copilot(), max_output_bytes=500)).run(_skill(tmp_path), _case())
    assert result.error is not None
    assert result.cost_note == COPILOT_PREMIUM + (
        "the copilot trace was cut at max_output_bytes before reporting premium requests"
    )
    assert result.usage_note == (
        "the copilot trace was cut at max_output_bytes before reporting token usage"
    )


def test_a_stopped_run_keeps_what_its_trace_reported_before_the_stop(tmp_path, fake, monkeypatch):
    # Copilot reports tokens in `session.shutdown`, before its `result` event:
    # a run that hangs after it has real token figures, and only the premium
    # request count is unknown.
    lines = (FIXTURES / "copilot-trigger.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "partial.jsonl").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(tmp_path / "partial.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "stall")
    result = ProductRunner(replace(_copilot(), timeout_seconds=1)).run(_skill(tmp_path), _case())
    assert result.error == "copilot timed out after 1s"
    assert result.input_tokens > 0
    assert result.usage_note == ""
    assert result.cost_note == COPILOT_PREMIUM + (
        "copilot timed out before reporting premium requests"
    )


def test_a_run_that_reported_its_spend_before_hanging_keeps_it(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "stall")
    result = ProductRunner(_product(timeout_seconds=1)).run(_skill(tmp_path), _case())
    assert result.error == "claude-code timed out after 1s"
    assert result.cost_usd > 0
    assert (result.cost_note, result.usage_note) == ("", "")


def test_a_generic_product_that_fails_still_says_it_reports_no_spend(tmp_path, fake, monkeypatch):
    # `cli` never reports spend, so its notes say that however the run ended;
    # "timed out before reporting cost" would imply it would have.
    product = _product(name="cli", parse=None, invoke="{task}", version_command=None)
    for mode, overrides in (("exit3", {}), ("sleep", {"timeout_seconds": 0.5})):
        monkeypatch.setenv("FAKE_PRODUCT_MODE", mode)
        result = ProductRunner(replace(product, **overrides)).run(_skill(tmp_path), _case())
        assert result.error is not None
        assert result.cost_note == "the cli product does not report cost"
        assert result.usage_note == "the cli product does not report token usage"


def test_a_run_that_started_no_process_keeps_empty_notes(tmp_path, fake):
    # Nothing ran, so nothing was spent: the 0.0 is a measurement.
    oversized = ProductRunner(_product()).run(_skill(tmp_path), _case(task="x" * (100 * 1024)))
    missing = ProductRunner(_product(argv=("/nonexistent/product", "-p", "{prompt}"))).run(
        _skill(tmp_path), _case()
    )
    unsafe = ProductRunner(_product()).run(
        Skill(name="../escape", description="d", instructions="i", path=tmp_path, markdown="m"),
        _case(),
    )
    for result in (oversized, missing, unsafe):
        assert result.error is not None
        assert (result.cost_note, result.usage_note) == ("", "")


# --- read_trace: the exit-code-vs-trace-error precedence rule ---


def test_read_trace_prefers_the_products_own_error_to_the_exit_code():
    stdout = (FIXTURES / "copilot-error.jsonl").read_text(encoding="utf-8")
    trace = read_trace(
        _product(name="copilot", parse=parse_copilot), Invocation(stdout=stdout, exit_code=1)
    )
    assert trace.error == (
        "copilot: 402 You have exceeded your monthly quota (Request ID: REDACTED)"
    )


def test_read_trace_falls_back_to_the_exit_code_when_the_trace_has_no_error():
    stdout = (FIXTURES / "claude-code-negative.jsonl").read_text(encoding="utf-8")
    trace = read_trace(
        _product(), Invocation(stdout=stdout, stderr="something broke\n", exit_code=2)
    )
    assert trace.error == "claude-code exited with code 2: something broke"
    # The trace did report its spend, so the exit code changes none of it.
    assert trace.cost_usd > 0
    assert (trace.cost_note, trace.usage_note) == ("", "")


def test_read_trace_a_generic_products_non_zero_exit_is_an_error():
    product = _product(name="cli", parse=None, invoke="{task}", version_command=None)
    trace = read_trace(product, Invocation(stdout="", stderr="boom", exit_code=3))
    assert trace.error == "cli exited with code 3: boom"


def test_read_trace_a_generic_products_success_is_stdout_verbatim():
    product = _product(name="cli", parse=None, invoke="{task}", version_command=None)
    trace = read_trace(product, Invocation(stdout="hello\n", exit_code=0))
    assert trace.output == "hello"
    assert trace.usage_note == "the cli product does not report token usage"
    assert trace.cost_note == "the cli product does not report cost"


def test_invoke_starts_the_executable_preflight_resolved(tmp_path, fake, monkeypatch):
    # `shutil.which` honours PATHEXT, `Popen(shell=False)` does not: a Windows
    # `.cmd` shim that passed preflight must start here too, so argv[0] is
    # resolved the same way before the process is spawned.
    import skill_lens.runners.product as product_module

    def which(name):
        return sys.executable if name == "python-under-another-name" else None

    monkeypatch.setattr(product_module.shutil, "which", which)
    product = _product(argv=("python-under-another-name", str(FAKE), "-p", "{prompt}"))
    result = ProductRunner(product).run(_skill(tmp_path), _case())
    assert result.error is None
    assert result.output == "PONG-7731"


# --- mock tools, through the MCP bridge ---

TOOLS = [
    ToolSpec(
        name="lookup_order",
        description="Look up an order.",
        parameters={"order_id": "string"},
        returns='{"order_id": "A-17", "status": "shipped"}',
    )
]


def _bridge_dir(seen: dict) -> Path:
    """The bridge's directory, from the config element the product was handed."""
    element = seen["argv"][-1]
    for prefix in ("--mcp-config=", "--additional-mcp-config=@"):
        if element.startswith(prefix):
            return Path(element[len(prefix) :]).parent
    raise AssertionError(f"no config element in {seen['argv']}")


def test_a_case_with_tools_hands_the_product_the_bridge_and_maps_the_names_back(
    tmp_path, fake, monkeypatch
):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-mcp.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_MCP_CALL", "lookup_order")
    runner = ProductRunner(_product(mcp=CLAUDE_CODE_MCP))
    result = runner.run(_skill(tmp_path), _case(tools=TOOLS))
    assert result.error is None
    seen = fake()
    # The config element is the last argument -- after the table's own args.
    assert seen["argv"][-2] == "--flag"
    assert seen["argv"][-1].startswith("--mcp-config=")
    mcp = seen["mcp"]
    assert mcp["server"] == "skill-lens"
    assert mcp["config"]["mcpServers"]["skill-lens"]["command"] == sys.executable
    assert mcp["initialize"]["serverInfo"]["name"] == "skill-lens"
    assert mcp["tools"] == ["lookup_order"]
    assert mcp["call"] == {
        "content": [{"type": "text", "text": '{"order_id": "A-17", "status": "shipped"}'}]
    }
    assert mcp["exit_code"] == 0  # the server left when the product closed its stdin
    # The trace names the tool the product's way; the result names it the case's way.
    assert [call.name for call in result.tool_calls] == ["Skill", "ToolSearch", "lookup_order"]
    assert result.tool_calls[2].arguments == {"order_id": "A-17"}
    assert result.output == "Order A-17 is shipped."
    # The bridge's files were never in the working directory, and are gone now.
    assert _bridge_dir(seen) != Path(seen["cwd"])
    assert not _bridge_dir(seen).exists()
    assert seen["skill_files"] == ["ping/SKILL.md"]


def test_under_copilot_the_bridge_takes_the_products_own_spellings(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "copilot-mcp.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_SKILLS_DIR", ".agents/skills")
    product = _product(
        name="copilot", parse=parse_copilot, skills_dir=".agents/skills", mcp=COPILOT_MCP
    )
    result = ProductRunner(product).run(_skill(tmp_path), _case(tools=TOOLS))
    assert result.error is None
    seen = fake()
    assert seen["argv"][-1].startswith("--additional-mcp-config=@")
    entry = seen["mcp"]["config"]["mcpServers"]["skill-lens"]
    assert entry["type"] == "local" and entry["tools"] == ["*"]
    assert seen["mcp"]["tools"] == ["lookup_order"]
    assert [call.name for call in result.tool_calls] == ["lookup_order"]
    assert result.output == "Order A-17 is shipped."


def test_the_bridge_stays_out_of_a_workspace(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-mcp.jsonl"))
    workspace = create_workspace(WorkspaceSpec(files={"in.txt": "hi"}), label="t")
    try:
        result = ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).run(
            _skill(tmp_path), _case(tools=TOOLS, workspace=WorkspaceSpec()), workspace=workspace
        )
        assert result.error is None
        assert workspace.listing() == [".claude/skills/ping/SKILL.md", "in.txt"]
        assert _bridge_dir(fake()) != workspace.root
    finally:
        workspace.cleanup()


def test_a_product_that_never_connects_is_an_error_not_a_failed_trajectory(
    tmp_path, fake, monkeypatch
):
    # The product ran to a clean result, but never asked the bridge for its
    # tools: the model had none, which says nothing about the skill.
    monkeypatch.setenv("FAKE_PRODUCT_TRACE", str(FIXTURES / "claude-code-mcp.jsonl"))
    monkeypatch.setenv("FAKE_PRODUCT_MCP", "ignore")
    result = ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).run(_skill(tmp_path), _case(tools=TOOLS))
    assert result.error is not None
    assert "never listed the case's mock tools" in result.error
    assert "[runners.claude-code] args" in result.error
    assert result.cost_usd == pytest.approx(0.0271188)  # the product ran, and spent
    assert fake()["mcp"]["ignored"] is True


def test_a_product_failure_is_reported_before_the_connection_check(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_MODE", "exit3")
    monkeypatch.setenv("FAKE_PRODUCT_MCP", "ignore")
    result = ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).run(_skill(tmp_path), _case(tools=TOOLS))
    assert result.error == "claude-code exited with code 3: boom"
    assert not _bridge_dir(fake()).exists()  # removed on the failure path too


def test_a_case_without_tools_gets_no_bridge(tmp_path, fake):
    result = ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).run(_skill(tmp_path), _case())
    assert result.error is None
    seen = fake()
    assert seen["argv"][-1] == "--flag"
    assert seen["mcp"] is None


def test_a_product_that_cannot_take_the_bridge_errors_a_tools_case(tmp_path, fake):
    # Preflight refuses this before a run; a caller that skipped preflight
    # still gets an errored result, never a raise or a silent run without
    # the tools.
    result = ProductRunner(_product()).run(_skill(tmp_path), _case(tools=TOOLS))
    assert result.error == "the claude-code runner cannot serve the case's tools:"
    assert not (tmp_path / "record.json").exists()  # the product never started
