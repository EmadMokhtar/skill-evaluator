"""Everything a product runner refuses before any case runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from skill_lens.cases.loader import parse_cases_file
from skill_lens.models import EvalCase, Skill, ToolSpec, TrajectorySpec
from skill_lens.runners import product as product_module
from skill_lens.runners.mcp import CLAUDE_CODE_MCP, COPILOT_MCP, BridgeSetupError
from skill_lens.runners.product import TRUST_NOTE, Product, ProductRunner, ProductSetupError
from skill_lens.runners.product_sandbox import (
    HOME_PREFIX,
    Isolation,
    ProductSandbox,
    environment_refusal,
)
from skill_lens.runners.traces import parse_claude_code

FAKE = Path(__file__).parent / "fake_product.py"


def _product(**overrides) -> Product:
    fields = dict(
        name="claude-code",
        argv=(sys.executable, str(FAKE), "-p", "{prompt}"),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse_claude_code,
        version_command=(sys.executable, str(FAKE), "--version"),
    )
    fields.update(overrides)
    return Product(**fields)


def _skill(name="ping") -> Skill:
    return Skill(name=name, description="d", instructions="i", path=Path("/tmp/x"), markdown="m")


def _case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "t")
    return EvalCase(**kwargs)


def test_a_clean_run_records_the_executable_version_and_trust():
    status = ProductRunner(_product()).preflight([_skill()], {"ping": [_case()]})
    assert status.name == "claude-code"
    assert status.executable == sys.executable
    assert status.version == "fake 1.2.3"
    assert status.trust == TRUST_NOTE


def test_a_missing_executable_names_the_runner_and_the_command():
    product = _product(argv=("no-such-product-xyz", "-p", "{prompt}"), version_command=None)
    with pytest.raises(
        ProductSetupError, match=r"runner claude-code: 'no-such-product-xyz' is not on PATH"
    ):
        ProductRunner(product).preflight([], {})


def test_the_message_says_where_a_cli_command_is_configured():
    product = _product(
        name="cli", argv=("no-such-product-xyz", "{prompt}"), parse=None, version_command=None
    )
    with pytest.raises(ProductSetupError, match=r"\[runners\.cli\] command"):
        ProductRunner(product).preflight([], {})


def test_a_version_probe_that_fails_is_a_setup_error(monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_VERSION_FAILS", "1")
    with pytest.raises(
        ProductSetupError, match=r"runner claude-code: .*--version exited with code 1: cannot start"
    ):
        ProductRunner(_product()).preflight([], {})


def test_a_generic_product_has_no_version_probe():
    product = _product(name="cli", parse=None, version_command=None)
    assert ProductRunner(product).preflight([], {}).version == ""


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b"])
def test_a_skill_name_that_is_not_one_directory_name_is_refused(name):
    with pytest.raises(ProductSetupError, match=r"cannot be a directory name"):
        ProductRunner(_product()).preflight([_skill(name)], {name: [_case()]})


def test_a_case_with_mock_tools_is_refused_where_no_bridge_can_reach():
    case = _case(name="uses tools", tools=[ToolSpec(name="lookup")])
    # A preset whose `command` names another executable: `mcp` is None.
    with pytest.raises(ProductSetupError) as info:
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})
    assert "case 'uses tools' of skill 'ping' declares tools:" in str(info.value)
    assert "claude-code runner cannot provide" in str(info.value)
    assert "[runners.claude-code] command names another executable" in str(info.value)
    assert "--mcp-config" in str(info.value)
    # The generic product has no flag at all.
    product = _product(name="cli", parse=None, version_command=None)
    with pytest.raises(ProductSetupError) as info:
        ProductRunner(product).preflight([_skill()], {"ping": [case]})
    assert "cli runner cannot provide -- the cli runner has no flag" in str(info.value)
    assert "use a preset (copilot, claude-code)" in str(info.value)


def test_a_case_with_mock_tools_is_accepted_under_a_bridged_product_and_the_bridge_is_probed(
    monkeypatch,
):
    probed: list[bool] = []
    monkeypatch.setattr(product_module, "probe_bridge", lambda *_a: probed.append(True))
    case = _case(name="uses tools", tools=[ToolSpec(name="lookup")])
    status = ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).preflight([_skill()], {"ping": [case]})
    assert status.name == "claude-code"
    assert probed == [True]  # once, however many cases declare tools
    ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).preflight(
        [_skill()], {"ping": [case, _case(name="also", tools=[ToolSpec(name="x")])]}
    )
    assert probed == [True, True]


def test_the_bridge_really_starts_in_preflight():
    case = _case(tools=[ToolSpec(name="lookup")])
    ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).preflight([_skill()], {"ping": [case]})


def test_the_bridge_is_not_probed_when_no_case_declares_tools(monkeypatch):
    def explode(*_a) -> None:
        raise BridgeSetupError("must not run")

    monkeypatch.setattr(product_module, "probe_bridge", explode)
    ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).preflight([_skill()], {"ping": [_case()]})


def test_a_bridge_that_does_not_start_is_a_setup_error_naming_the_runner(monkeypatch):
    def explode(*_a) -> None:
        raise BridgeSetupError("the MCP bridge does not start under /x: boom")

    monkeypatch.setattr(product_module, "probe_bridge", explode)
    case = _case(tools=[ToolSpec(name="lookup")])
    with pytest.raises(
        ProductSetupError, match=r"runner claude-code: the MCP bridge does not start under /x: boom"
    ):
        ProductRunner(_product(mcp=CLAUDE_CODE_MCP)).preflight([_skill()], {"ping": [case]})


@pytest.mark.parametrize("element", ["--available-tools", "--available-tools=bash"])
def test_a_table_flag_that_hides_mcp_tools_is_refused_when_a_case_declares_tools(
    element, monkeypatch
):
    monkeypatch.setattr(product_module, "probe_bridge", lambda *_a: None)
    product = _product(
        name="copilot", argv=(sys.executable, str(FAKE), "-p", "{prompt}", element), mcp=COPILOT_MCP
    )
    case = _case(tools=[ToolSpec(name="lookup")])
    with pytest.raises(ProductSetupError) as info:
        ProductRunner(product).preflight([_skill()], {"ping": [case]})
    assert f"runner copilot: {element!r} in [runners.copilot] would hide" in str(info.value)
    # Without a tools case the flag is the user's business.
    ProductRunner(product).preflight([_skill()], {"ping": [_case()]})


def test_claude_codes_tools_flag_does_not_hide_mcp_tools_so_it_is_not_refused(monkeypatch):
    # Verified: `claude --tools ""` still lists the MCP tool in its init event.
    monkeypatch.setattr(product_module, "probe_bridge", lambda *_a: None)
    product = _product(
        argv=(sys.executable, str(FAKE), "-p", "{prompt}", "--tools", ""), mcp=CLAUDE_CODE_MCP
    )
    case = _case(tools=[ToolSpec(name="lookup")])
    ProductRunner(product).preflight([_skill()], {"ping": [case]})


def test_a_generic_product_refuses_trajectory_and_offered():
    product = _product(name="cli", parse=None, version_command=None)
    with pytest.raises(ProductSetupError, match=r"trajectory:.*cli runner records no tool calls"):
        ProductRunner(product).preflight(
            [_skill()], {"ping": [_case(trajectory=TrajectorySpec(called=["x"]))]}
        )
    with pytest.raises(ProductSetupError, match=r"mode: offered.*cli runner cannot observe"):
        ProductRunner(product).preflight([_skill()], {"ping": [_case(mode="offered")]})


def test_a_preset_accepts_trajectory_and_offered():
    cases = [_case(trajectory=TrajectorySpec(called=["Bash"])), _case(name="o", mode="offered")]
    ProductRunner(_product()).preflight([_skill()], {"ping": cases})  # no raise


def test_only_the_cases_given_are_inspected():
    # A case the orchestrator filtered out never reaches preflight; nothing
    # here re-discovers it.
    ProductRunner(_product()).preflight([_skill()], {})  # no raise, no cases


def test_a_referenced_tool_is_refused_like_an_inline_one(tmp_path):
    # `ref:` resolves in the case loader, so preflight sees a ToolSpec and
    # treats it like an inline one -- refused here because this product
    # cannot take the bridge; nothing product-specific to add.
    (tmp_path / "lib.yaml").write_text("tools:\n  - name: lookup\n", encoding="utf-8")
    path = tmp_path / "ping.eval.yaml"
    path.write_text(
        "tool_libraries: [lib.yaml]\ncases:\n  - name: uses tools\n    task: t\n"
        "    tools:\n      - ref: lookup\n",
        encoding="utf-8",
    )
    (case,) = parse_cases_file(path)
    with pytest.raises(
        ProductSetupError, match="case 'uses tools' of skill 'ping' declares tools:"
    ):
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})


def test_a_product_tool_name_reaches_a_preset_from_yaml_beside_a_workspace(tmp_path):
    # The documented combination: no `tools:` (a product refuses them), a
    # `workspace:` (the product's working directory), and a trajectory naming
    # the product's own tool. The loader must let it through and the preset
    # must accept it -- skill-lens cannot list a product's tools, so it does
    # not pretend to check the name.
    from skill_lens.cases.loader import parse_cases_file

    path = tmp_path / "p.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: greps\n"
        "    task: count the errors in app.log\n"
        "    workspace:\n"
        "      files:\n"
        "        app.log: 'ERROR x'\n"
        "    trajectory:\n"
        "      called: [Bash]\n"
        "      forbidden: [Write]\n",
        encoding="utf-8",
    )
    cases = parse_cases_file(path)
    status = ProductRunner(_product()).preflight([_skill()], {"ping": cases})
    assert status.name == "claude-code"


def _workspace_case(**kwargs) -> EvalCase:
    from skill_lens.models import WorkspaceSpec

    return _case(workspace=WorkspaceSpec(), **kwargs)


@pytest.mark.parametrize(
    "assertion",
    [
        {"kind": "file-produced", "file_glob": "*/*/*/SKILL.md"},
        {"kind": "file-produced", "file": ".claude/skills/ping/SKILL.md"},
        {"kind": "contains", "value": "m", "file_glob": ".claude/skills/*/SKILL.md"},
    ],
)
def test_a_file_check_that_reaches_the_delivered_skill_is_refused(assertion):
    from skill_lens.models import AssertionSpec

    case = _workspace_case(assertions=[AssertionSpec(**assertion)])
    with pytest.raises(
        ProductSetupError, match="copy of the skill the claude-code runner delivers"
    ):
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})


def test_a_judge_artifact_that_reaches_the_delivered_skill_is_refused():
    from skill_lens.models import JudgeSpec

    case = _workspace_case(
        judge=JudgeSpec(rubric=["r"], artifacts=[{"glob": ".claude/*/ping/SKILL.md"}])
    )
    with pytest.raises(ProductSetupError, match="copy of the skill"):
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})


def test_a_bundled_file_counts_as_delivered(tmp_path):
    from skill_lens.models import AssertionSpec

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "init.sh").write_text("echo", encoding="utf-8")
    skill = Skill(
        name="ping",
        description="d",
        instructions="i",
        path=tmp_path / "SKILL.md",
        markdown="m",
        bundle_root=tmp_path,
    )
    case = _workspace_case(
        assertions=[
            AssertionSpec(kind="file-produced", file_glob=".claude/skills/ping/scripts/*.sh")
        ]
    )
    with pytest.raises(ProductSetupError, match="copy of the skill"):
        ProductRunner(_product()).preflight([skill], {"ping": [case]})


def test_a_file_check_on_the_skill_s_own_output_is_accepted():
    from skill_lens.models import AssertionSpec, JudgeSpec

    case = _workspace_case(
        assertions=[AssertionSpec(kind="file-produced", file_glob=".planning/*/task_plan.md")],
        judge=JudgeSpec(
            rubric=["r"], artifacts=["progress.md", {"glob": ".planning/*/progress.md"}]
        ),
    )
    ProductRunner(_product()).preflight([_skill()], {"ping": [case]})


def test_a_file_glob_list_with_one_entry_on_the_delivered_skill_is_refused():
    from skill_lens.models import AssertionSpec

    case = _workspace_case(
        assertions=[
            AssertionSpec(kind="file-produced", file_glob=["*.md", ".claude/skills/*/SKILL.md"])
        ]
    )
    with pytest.raises(ProductSetupError, match="copy of the skill"):
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})


# --- the product sandbox (issue #99) ---

HOME_ONLY = ProductSandbox("auto", "none", "test")
SANDBOXED = ProductSandbox("auto", "sandbox-exec", "test")
NO_AUTH = {"PATH": "/usr/bin"}


def test_a_preset_with_no_auth_variable_is_refused():
    refusal = environment_refusal(_product(), HOME_ONLY, "runner", parent=NO_AUTH)
    assert refusal == (
        'runner claude-code: product_sandbox = "auto" gives the product a temporary home '
        "directory, so it cannot use a login stored in your home directory or keychain; "
        "export CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY, "
        "name the credential your setup uses in [runners.claude-code] pass_env (a name "
        "containing TOKEN, KEY, SECRET, PASSWORD or CREDENTIAL), or set "
        'product_sandbox = "off"'
    )


def test_copilot_is_told_its_own_variable():
    refusal = environment_refusal(_product(name="copilot"), HOME_ONLY, "judge", parent=NO_AUTH)
    assert refusal is not None
    assert refusal.startswith('judge copilot: product_sandbox = "auto"')
    assert "export COPILOT_GITHUB_TOKEN, name the credential" in refusal


def test_an_empty_auth_variable_does_not_count():
    parent = {**NO_AUTH, "CLAUDE_CODE_OAUTH_TOKEN": ""}
    assert environment_refusal(_product(), HOME_ONLY, "runner", parent=parent) is not None


@pytest.mark.parametrize("name", ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"])
def test_one_auth_variable_is_enough(name):
    parent = {**NO_AUTH, name: "set"}
    assert environment_refusal(_product(), HOME_ONLY, "runner", parent=parent) is None


def test_a_base_url_alone_is_not_auth():
    parent = {**NO_AUTH, "ANTHROPIC_BASE_URL": "https://gateway"}
    assert environment_refusal(_product(), HOME_ONLY, "runner", parent=parent) is not None


@pytest.mark.parametrize(
    "name",
    [
        "AWS_SECRET_ACCESS_KEY",
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AWS_SHARED_CREDENTIALS_FILE",
        "MY_GATEWAY_PASSWORD",
    ],
)
def test_a_credential_named_in_pass_env_satisfies_the_check(name):
    product = _product(pass_env=("CLAUDE_CODE_USE_BEDROCK", name))
    parent = {**NO_AUTH, "CLAUDE_CODE_USE_BEDROCK": "1", name: "set"}
    assert environment_refusal(product, HOME_ONLY, "runner", parent=parent) is None


def test_a_pass_env_name_that_is_no_credential_does_not_satisfy_the_check():
    # A Bedrock setup that logs in through `aws sso login` keeps its
    # credentials in ~/.aws, which the temporary home hides; a region and a
    # switch are set, but nothing to sign in with -- exit 2, not N errored cases.
    product = _product(pass_env=("CLAUDE_CODE_USE_BEDROCK", "AWS_REGION", "AWS_PROFILE"))
    parent = {**NO_AUTH, "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "eu", "AWS_PROFILE": "w"}
    assert environment_refusal(product, HOME_ONLY, "runner", parent=parent) is not None


def test_a_wrapper_is_not_auth_checked():
    assert environment_refusal(_product(wrapper=True), HOME_ONLY, "runner", parent=NO_AUTH) is None


def test_cli_needs_pass_env_under_isolation():
    refusal = environment_refusal(_product(name="cli"), HOME_ONLY, "runner", parent=NO_AUTH)
    assert refusal == (
        'runner cli: product_sandbox = "auto" builds the product\'s environment from an '
        "allowlist, so the command gets none of your variables unless you name them; set "
        "[runners.cli] pass_env to the variables it needs (pass_env = [] when it needs none), "
        'or set product_sandbox = "off"'
    )
    named = _product(name="cli", pass_env=())
    assert environment_refusal(named, HOME_ONLY, "runner", parent=NO_AUTH) is None


def test_off_checks_no_environment():
    off = ProductSandbox("off", "none", 'product_sandbox = "off"')
    assert environment_refusal(_product(name="cli"), off, "runner", parent=NO_AUTH) is None
    assert environment_refusal(_product(), off, "runner", parent=NO_AUTH) is None


def test_preflight_raises_the_refusal(monkeypatch):
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    runner = ProductRunner(_product(), sandbox=HOME_ONLY)
    with pytest.raises(ProductSetupError, match="cannot use a login stored in your home"):
        runner.preflight([_skill()], {"ping": [_case()]})


def test_the_version_probe_runs_in_the_temporary_home(tmp_path, monkeypatch):
    record = tmp_path / "version-home.txt"
    monkeypatch.setenv("FAKE_PRODUCT_VERSION_RECORD", str(record))
    product = _product(pass_env=("FAKE_PRODUCT_VERSION_RECORD",), wrapper=True)
    ProductRunner(product, sandbox=HOME_ONLY).preflight([_skill()], {"ping": [_case()]})
    home = Path(record.read_text(encoding="utf-8"))
    assert home.name == "home"
    assert home.parent.name.startswith(HOME_PREFIX)
    assert not home.parent.exists()


def test_a_product_that_fails_inside_the_sandbox_names_off(monkeypatch):
    monkeypatch.setattr(ProductSandbox, "wrap", lambda self, argv, writable, tempdir=None: argv)
    monkeypatch.setenv("FAKE_PRODUCT_VERSION_FAILS", "1")
    product = _product(pass_env=("FAKE_PRODUCT_VERSION_FAILS",), wrapper=True)
    with pytest.raises(ProductSetupError) as caught:
        ProductRunner(product, sandbox=SANDBOXED).preflight([_skill()], {"ping": [_case()]})
    message = str(caught.value)
    assert message.startswith(f"runner claude-code: {sys.executable} {FAKE} --version exited")
    assert message.endswith(
        'under sandbox-exec -- set product_sandbox = "off" to run it without one'
    )


def test_the_status_carries_the_sandbox():
    product = _product(wrapper=True)
    status = ProductRunner(product, sandbox=HOME_ONLY).preflight([_skill()], {"ping": [_case()]})
    assert status.sandbox == HOME_ONLY.status()
    assert status.trust == HOME_ONLY.trust()


def test_off_reports_the_old_sentence_and_an_off_status():
    status = ProductRunner(_product()).preflight([_skill()], {"ping": [_case()]})
    assert status.trust == TRUST_NOTE
    assert status.sandbox is not None and status.sandbox.mode == "off"


def test_the_bridge_probe_runs_under_the_isolation(monkeypatch):
    seen: list = []
    monkeypatch.setattr(product_module, "probe_bridge", lambda isolation: seen.append(isolation))
    product = _product(mcp=CLAUDE_CODE_MCP, wrapper=True)
    runner = ProductRunner(product, sandbox=HOME_ONLY)
    tools = [ToolSpec(name="lookup", returns="ok")]
    runner.preflight([_skill()], {"ping": [_case(tools=tools)]})
    (isolation,) = seen
    assert isinstance(isolation, Isolation)
    assert isolation.root is not None and isolation.root.name.startswith(HOME_PREFIX)
