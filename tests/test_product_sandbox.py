"""The product sandbox: the once-per-run decision, the built environment and
the wrappers. String-level everywhere; tests/test_product_sandbox_live.py is
the proof on a real backend."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from skill_lens import scripts
from skill_lens.models import ProductSandboxStatus
from skill_lens.runners.product import Product
from skill_lens.runners.product_sandbox import (
    CONTROLLED_ENV,
    PRODUCT_SANDBOX_OFF,
    TRUST_NOTE,
    ProductSandbox,
    ProductSetupError,
    decide_product_sandbox,
    product_environment,
)


def _completed(returncode: int, stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=b"", stderr=stderr)


def _recording_run(seen: dict):
    def run(argv, **_kwargs):
        seen["argv"] = argv
        return _completed(0)

    return run


# --- the decision ---


def test_off_probes_nothing():
    def which(_name):
        raise AssertionError("which must not be called")

    def run(*_a, **_k):
        raise AssertionError("run must not be called")

    assert decide_product_sandbox("off", system="Darwin", which=which, run=run) == (
        PRODUCT_SANDBOX_OFF
    )


def test_auto_on_macos_uses_sandbox_exec_without_a_network_denial():
    seen: dict = {}
    sandbox = decide_product_sandbox(
        "auto", system="Darwin", which=lambda _n: "/usr/bin/sandbox-exec", run=_recording_run(seen)
    )
    assert sandbox == ProductSandbox("auto", "sandbox-exec", "sandbox-exec probe succeeded")
    profile = seen["argv"][2]
    assert "(deny file-write*)" in profile
    assert "network" not in profile
    assert seen["argv"][-1] == "/usr/bin/true"


def test_auto_on_linux_uses_bwrap_without_unshare_net():
    seen: dict = {}
    sandbox = decide_product_sandbox(
        "auto", system="Linux", which=lambda _n: "/usr/bin/bwrap", run=_recording_run(seen)
    )
    assert sandbox.backend == "bwrap"
    assert "--unshare-net" not in seen["argv"]
    assert "--unshare-pid" in seen["argv"]


def test_auto_with_no_backend_keeps_home_isolation():
    sandbox = decide_product_sandbox("auto", system="Windows", which=lambda _n: "x", run=None)
    assert sandbox.backend == "none"
    assert sandbox.home_isolated is True
    assert sandbox.detail == "no sandbox backend on Windows"


def test_required_with_no_backend_raises():
    with pytest.raises(ProductSetupError) as caught:
        decide_product_sandbox("required", system="Linux", which=lambda _n: None, run=None)
    assert str(caught.value) == (
        'product_sandbox = "required" but no sandbox is available: bwrap not found on PATH'
    )


def test_required_with_a_backend_is_the_backend():
    sandbox = decide_product_sandbox(
        "required",
        system="Linux",
        which=lambda _n: "/usr/bin/bwrap",
        run=lambda *_a, **_k: _completed(0),
    )
    assert sandbox == ProductSandbox("required", "bwrap", "bwrap probe succeeded")


def test_a_failed_probe_reads_as_none():
    def run(*_a, **_k):
        return _completed(1, b"bwrap: setting up uid map: Permission denied\nmore\n")

    sandbox = decide_product_sandbox("auto", system="Linux", which=lambda _n: "/b", run=run)
    assert sandbox.backend == "none"
    assert sandbox.detail == "bwrap probe failed: bwrap: setting up uid map: Permission denied"


def test_off_is_not_home_isolated():
    assert PRODUCT_SANDBOX_OFF.home_isolated is False
    assert PRODUCT_SANDBOX_OFF.detail == 'product_sandbox = "off"'


# --- the trust notes ---


def test_the_off_note_is_todays_sentence():
    assert PRODUCT_SANDBOX_OFF.trust() == TRUST_NOTE
    assert TRUST_NOTE == (
        "runs with permission prompts disabled and the full environment; no skill-lens "
        "sandbox applies; bundled scripts are reachable through the product's own tools"
    )


def test_the_home_only_note_says_why_and_that_it_is_no_sandbox():
    sandbox = ProductSandbox("auto", "none", "bwrap not found on PATH")
    assert sandbox.trust() == (
        "runs with permission prompts disabled, a temporary home directory and only an "
        "allowlisted environment; that is not a sandbox: no skill-lens sandbox applies "
        "(bwrap not found on PATH), and the product can read and write anything you can; "
        "bundled scripts are reachable through the product's own tools"
    )


def test_the_home_only_note_never_says_sandboxed():
    note = ProductSandbox("auto", "none", "no sandbox backend on Windows").trust()
    assert "that is not a sandbox" in note
    assert "under " not in note


@pytest.mark.parametrize("backend", ["sandbox-exec", "bwrap"])
def test_the_sandboxed_note_says_what_it_stops_and_what_it_does_not(backend):
    sandbox = ProductSandbox("auto", backend, f"{backend} probe succeeded")
    assert sandbox.trust() == (
        "runs with permission prompts disabled, a temporary home directory and only an "
        f"allowlisted environment, under {backend}: it cannot change files outside its "
        "working directory and its own temporary directories, or read other skill-lens "
        "temporary directories; it can read anything else you can, and reach the network; "
        "bundled scripts are reachable through the product's own tools"
    )


def test_status_mirrors_the_decision():
    sandbox = ProductSandbox("required", "bwrap", "bwrap probe succeeded")
    assert sandbox.status() == ProductSandboxStatus(
        mode="required", backend="bwrap", home_isolated=True, detail="bwrap probe succeeded"
    )
    assert PRODUCT_SANDBOX_OFF.status().home_isolated is False


# --- the built environment ---

ROOT = Path("/tmp/skill-lens-home-abc")
PARENT = {
    "PATH": "/usr/bin",
    "LANG": "en_US.UTF-8",
    "HOME": "/Users/you",
    "OPENAI_API_KEY": "sk-harness",
    "CLAUDE_CONFIG_DIR": "/Users/you/.claude",
    "COPILOT_HOME": "/Users/you/.copilot",
    "HTTPS_PROXY": "http://proxy:3128",
    "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
    "ANTHROPIC_BASE_URL": "https://gateway",
    "COPILOT_GITHUB_TOKEN": "ghp",
    "GH_TOKEN": "gh",
    "AWS_PROFILE": "work",
}


def _product(name: str = "claude-code", **overrides) -> Product:
    fields = dict(
        name=name,
        argv=("agent", "-p", "{prompt}"),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=None,
        version_command=None,
    )
    fields.update(overrides)
    return Product(**fields)


def test_the_environment_is_built_not_inherited():
    env = product_environment(_product("cli"), ROOT, parent=PARENT, windows=False)
    assert "OPENAI_API_KEY" not in env
    assert "CLAUDE_CONFIG_DIR" not in env
    assert "COPILOT_HOME" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["HOME"] == str(ROOT / "home")
    for key in ("TMPDIR", "TMP", "TEMP"):
        assert env[key] == str(ROOT / "tmp")
    assert env["XDG_CONFIG_HOME"] == str(ROOT / "home" / ".config")
    assert env["XDG_CACHE_HOME"] == str(ROOT / "home" / ".cache")
    assert env["XDG_DATA_HOME"] == str(ROOT / "home" / ".local" / "share")
    assert env["XDG_STATE_HOME"] == str(ROOT / "home" / ".local" / "state")
    assert "USERPROFILE" not in env


def test_claude_code_passes_its_own_variables():
    env = product_environment(_product("claude-code"), ROOT, parent=PARENT, windows=False)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == PARENT["CLAUDE_CODE_OAUTH_TOKEN"]
    assert env["ANTHROPIC_BASE_URL"] == "https://gateway"
    assert env["DISABLE_AUTOUPDATER"] == "1"
    assert "COPILOT_GITHUB_TOKEN" not in env
    assert "GH_TOKEN" not in env


def test_copilot_passes_its_own_variables():
    env = product_environment(_product("copilot"), ROOT, parent=PARENT, windows=False)
    assert env["COPILOT_GITHUB_TOKEN"] == PARENT["COPILOT_GITHUB_TOKEN"]
    assert env["GH_TOKEN"] == PARENT["GH_TOKEN"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "DISABLE_AUTOUPDATER" not in env


def test_cli_passes_no_product_variables():
    env = product_environment(_product("cli"), ROOT, parent=PARENT, windows=False)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "COPILOT_GITHUB_TOKEN" not in env


def test_pass_env_names_pass_when_set():
    product = _product("cli", pass_env=("AWS_PROFILE", "NOT_SET_ANYWHERE"))
    env = product_environment(product, ROOT, parent=PARENT, windows=False)
    assert env["AWS_PROFILE"] == "work"
    assert "NOT_SET_ANYWHERE" not in env


def test_a_wrapper_keeps_the_preset_variables():
    product = _product("claude-code", wrapper=True)
    env = product_environment(product, ROOT, parent=PARENT, windows=False)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == PARENT["CLAUDE_CODE_OAUTH_TOKEN"]
    assert env["DISABLE_AUTOUPDATER"] == "1"


def test_windows_points_userprofile_and_appdata_into_the_home():
    parent = {**PARENT, "SystemRoot": "C:\\Windows", "APPDATA": "C:\\real"}
    env = product_environment(_product("cli"), ROOT, parent=parent, windows=True)
    assert env["USERPROFILE"] == str(ROOT / "home")
    assert env["APPDATA"] == str(ROOT / "home" / "AppData" / "Roaming")
    assert env["LOCALAPPDATA"] == str(ROOT / "home" / "AppData" / "Local")
    assert env["SystemRoot"] == "C:\\Windows"


def test_the_controlled_names_are_the_ones_the_isolation_owns():
    assert frozenset(
        {
            "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMPDIR", "TMP", "TEMP",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
            "CLAUDE_CONFIG_DIR", "COPILOT_HOME",
        }
    ) == CONTROLLED_ENV  # fmt: skip


def test_a_controlled_name_in_pass_env_never_overrides_the_isolation():
    # Config refuses it; the builder still never lets it through.
    product = _product("cli", pass_env=("HOME", "CLAUDE_CONFIG_DIR"))
    env = product_environment(product, ROOT, parent=PARENT, windows=False)
    assert env["HOME"] == str(ROOT / "home")
    assert "CLAUDE_CONFIG_DIR" not in env


# --- the OS wrappers ---

TEMPDIR = Path("/tmp/T")
WORKSPACE = TEMPDIR / "skill-lens-ws-1"
HOME_ROOT = TEMPDIR / "skill-lens-home-1"
BRIDGE = TEMPDIR / "skill-lens-mcp-1"


def test_the_product_profile_allows_the_network_and_confines_writes():
    profile = scripts.product_macos_profile([WORKSPACE, HOME_ROOT, BRIDGE], TEMPDIR)
    lines = profile.splitlines()
    assert lines[:3] == ["(version 1)", "(allow default)", "(deny file-write*)"]
    assert "network" not in profile
    assert lines[3] == (
        f'(allow file-write* (subpath "{WORKSPACE}") (subpath "{HOME_ROOT}") (subpath "{BRIDGE}"))'
    )
    assert '(allow file-write-data (literal "/dev/null"))' in lines
    assert f'(deny file-read* (subpath "{TEMPDIR}"))' in lines
    assert (
        f'(allow file-read* (subpath "{WORKSPACE}") (subpath "{HOME_ROOT}") (subpath "{BRIDGE}"))'
    ) in lines
    assert lines[-1] == f'(allow file-read-metadata (literal "{TEMPDIR}"))'


def test_the_product_bwrap_argv_shares_the_network():
    argv = scripts.product_bwrap_argv([WORKSPACE, HOME_ROOT], TEMPDIR, ["agent", "-p", "hi"])
    assert "--unshare-net" not in argv
    tmpfs = argv.index("--tmpfs")
    assert argv[tmpfs + 1] == str(TEMPDIR)
    binds = [i for i, element in enumerate(argv) if element == "--bind"]
    assert [argv[i + 1] for i in binds] == [str(WORKSPACE), str(HOME_ROOT)]
    assert all(argv[i + 1] == argv[i + 2] for i in binds)
    assert all(i > tmpfs for i in binds)
    # The tmpfs is made read-only after the binds (bwrap creates their mount
    # points inside it, and --remount-ro is not recursive), so a write under
    # the temp dir is refused, as sandbox-exec refuses it -- not discarded.
    remount = argv.index("--remount-ro")
    assert argv[remount + 1] == str(TEMPDIR)
    assert all(remount > i for i in binds)
    assert argv[-7:] == [
        "--unshare-pid", "--die-with-parent", "--new-session", "--", "agent", "-p", "hi",
    ]  # fmt: skip


def test_wrap_under_none_is_the_identity():
    sandbox = ProductSandbox("auto", "none", "test")
    assert sandbox.wrap(["agent", "-p", "hi"], [WORKSPACE], TEMPDIR) == ["agent", "-p", "hi"]


def test_wrap_under_sandbox_exec_prefixes_the_profile():
    sandbox = ProductSandbox("auto", "sandbox-exec", "test")
    wrapped = sandbox.wrap(["agent"], [WORKSPACE], TEMPDIR)
    assert wrapped[:2] == ["sandbox-exec", "-p"]
    assert wrapped[2] == scripts.product_macos_profile([WORKSPACE], TEMPDIR)
    assert wrapped[3:] == ["agent"]


def test_wrap_under_bwrap_is_the_product_argv():
    sandbox = replace(ProductSandbox("auto", "none", "test"), backend="bwrap")
    assert sandbox.wrap(["agent"], [WORKSPACE], TEMPDIR) == scripts.product_bwrap_argv(
        [WORKSPACE], TEMPDIR, ["agent"]
    )
