"""ProductRunner against the fake product: the real subprocess path, offline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skill_lens.models import EvalCase, Skill, WorkspaceSpec
from skill_lens.runners.product import (
    PRESETS,
    Invocation,
    Product,
    ProductRunner,
    deliver_skill,
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
    assert copilot.judge_args == ()
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
    assert set(PRESETS) == {"copilot", "claude-code"}
    for preset in PRESETS.values():
        assert preset.timeout_seconds == 600.0
        assert preset.max_output_bytes == 8_000_000


# --- delivery ---


def test_deliver_skill_writes_the_markdown_verbatim_and_the_three_bundle_dirs_only(tmp_path):
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
