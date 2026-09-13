"""The real sandbox backends, on machines that have one.

Skipped where the probe fails (Windows, a Linux without bwrap, a CI runner
whose kernel refuses user namespaces). tests/test_scripts.py covers the
wrapping at string level everywhere; this file is the proof that the strings
do what they claim. Everything here is still offline: the "network" attempt
targets 127.0.0.1 and must FAIL.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from promptly import promptly
from skill_lens.bundle import SkillBundle
from skill_lens.evaluators.assertion import AssertionEvaluator
from skill_lens.models import AssertionSpec, EvalCase, RunResult, WorkspaceSpec
from skill_lens.scripts import ScriptPolicy, ScriptRuntime, probe_sandbox, run_script
from skill_lens.workspace import Workspace, create_workspace

STATUS = probe_sandbox("auto")
pytestmark = pytest.mark.skipif(
    STATUS.backend == "none", reason=f"no sandbox on this machine: {STATUS.detail}"
)


def _runtime() -> ScriptRuntime:
    policy = ScriptPolicy(sandbox="auto", interpreters={"py": (sys.executable,)})
    return ScriptRuntime(policy=policy, sandbox=STATUS)


def _run(
    tmp_path: Path, source: str, args: list[object] | None = None
) -> tuple[Workspace, str, str, int | None]:
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "probe.py").write_text(source, encoding="utf-8")
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    workspace = Workspace(root=ws_root.resolve())
    result = run_script(
        SkillBundle(root.resolve()), workspace, "scripts/probe.py", args or [], _runtime()
    )
    assert result.refused is None, result.refused
    return workspace, result.stdout, result.stderr, result.exit_code


def test_the_network_is_blocked(tmp_path):
    source = (
        "import socket\n"
        "s = socket.socket()\n"
        "s.settimeout(2)\n"
        "try:\n"
        "    s.connect(('127.0.0.1', 9))\n"
        "except OSError as exc:\n"
        "    print('blocked', type(exc).__name__)\n"
        "else:\n"
        "    print('connected')\n"
    )
    _, out, _, _ = _run(tmp_path, source)
    assert out.startswith("blocked")


def test_writes_outside_the_workspace_are_refused_and_inside_succeed(tmp_path):
    outside = tmp_path / "outside.txt"
    source = (
        "import pathlib\n"
        f"try:\n    pathlib.Path({str(outside)!r}).write_text('x')\n"
        "    print('outside: wrote')\n"
        "except OSError:\n    print('outside: refused')\n"
        "pathlib.Path('inside.txt').write_text('y')\n"
        "print('inside: wrote')\n"
    )
    workspace, out, err, _ = _run(tmp_path, source)
    assert "outside: refused" in out, err
    assert "inside: wrote" in out
    assert not outside.exists()
    assert workspace.read("inside.txt") == "y"


def test_a_sibling_skill_lens_directory_is_unreadable(tmp_path):
    import tempfile

    sibling = Path(tempfile.mkdtemp(prefix="skill-lens-other-")).resolve()
    try:
        (sibling / "secret.txt").write_text("baseline output", encoding="utf-8")
        source = (
            "import pathlib\n"
            f"try:\n    print(pathlib.Path({str(sibling / 'secret.txt')!r}).read_text())\n"
            "except OSError:\n    print('unreadable')\n"
        )
        _, out, _, _ = _run(tmp_path, source)
        assert out.strip() == "unreadable"
    finally:
        import shutil

        shutil.rmtree(sibling, ignore_errors=True)


def test_the_bundle_is_read_only(tmp_path):
    source = (
        "import pathlib, sys\n"
        "here = pathlib.Path(sys.argv[0]).parent\n"
        "try:\n    (here / 'evil.py').write_text('x')\n    print('wrote')\n"
        "except OSError:\n    print('refused')\n"
    )
    _, out, _, _ = _run(tmp_path, source)
    assert out.strip() == "refused"


def test_a_real_workspace_survives_the_temp_dir_deny(tmp_path):
    """The other tests' workspaces live under `tmp_path`, one level below the
    temp root. A production workspace does not: `create_workspace` makes a
    `skill-lens-<label>-*` directory directly under the temp root, which is
    exactly what the profile's allow line is written against. This is the
    layout the deny/allow pair must get right, not an artifact of the test
    harness's own nesting.
    """
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "probe.py").write_text(
        "import pathlib\n"
        "pathlib.Path('inside.txt').write_text('from the real workspace')\n"
        "print(pathlib.Path('inside.txt').read_text())\n",
        encoding="utf-8",
    )
    workspace = create_workspace(WorkspaceSpec(), label="live")
    try:
        result = run_script(
            SkillBundle(root.resolve()), workspace, "scripts/probe.py", [], _runtime()
        )
        assert result.refused is None, result.refused
        assert result.stdout.strip() == "from the real workspace"
        assert workspace.read("inside.txt") == "from the real workspace"
    finally:
        workspace.cleanup()


def test_what_a_script_plants_in_the_workspace_is_a_failed_check_not_an_abort(tmp_path):
    # The whole-branch review's C1/I2 scenario, end to end under the real
    # backend: a symlink out of the workspace and a FIFO are both writes the
    # sandbox allows (they land inside the workspace), and the evaluator must
    # score them as failures rather than block on one or exit 2 on the other.
    source = "import os\nos.symlink('/etc', 'triage.md')\nos.mkfifo('pipe.md')\nprint('planted')\n"
    workspace, out, _, code = _run(tmp_path, source)
    assert out.strip() == "planted" and code == 0
    result = RunResult(output="", workspace=workspace.root)
    case = EvalCase(
        name="planted",
        task="t",
        workspace=WorkspaceSpec(),
        assertions=[
            AssertionSpec(kind="file-produced", file="triage.md"),
            AssertionSpec(kind="contains", value="x", file="pipe.md"),
        ],
    )
    score = promptly(lambda: AssertionEvaluator().evaluate(case, result))
    assert not score.passed
    assert not score.errored
    assert "resolves outside" in score.checks[0].evidence
    assert "not a regular file" in score.checks[1].evidence


# --- the harness's own environment ----------------------------------------
#
# The allowlist stops a script *inheriting* the provider key. Whether a
# script can ask the OS for the harness's environment is a separate question,
# and these two tests pin the answer on macOS. The target is a child spawned
# with the secret in its exec-time environment, never `monkeypatch.setenv`
# on this process: the kernel serves the block it copied at exec, and a
# `setenv` afterwards changes nothing it would show -- a test planting the
# secret that way could not fail whatever the sandbox did.


@pytest.fixture
def secret_holder() -> Iterator[int]:
    """A same-user, non-Apple process whose exec-time environment holds a secret.

    Non-Apple matters: the kernel withholds a platform binary's environment
    from other processes, and `/bin/sleep` would make either test pass for
    the wrong reason. skill-lens itself is a Python from a venv, like this.
    """
    child = subprocess.Popen(  # noqa: S603 - fixed argv, shell=False
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env={"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "planted-secret"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield child.pid
    finally:
        child.kill()
        child.wait()


MACOS_ONLY = pytest.mark.skipif(
    STATUS.backend != "sandbox-exec", reason="pins the macOS backend's behaviour"
)


@MACOS_ONLY
def test_ps_cannot_be_executed_under_sandbox_exec(tmp_path, secret_holder):
    # `/bin/ps` is setuid root and sandbox-exec refuses to exec a setuid
    # binary, so the `ps -E` route to another process's environment is closed
    # -- as a side effect of the sandbox, not of any rule in the profile.
    source = (
        "import subprocess, sys\n"
        "try:\n"
        "    out = subprocess.run(['ps', '-Eww', '-p', sys.argv[1]], capture_output=True,"
        " text=True, check=False)\n"
        "    print('ran', repr(out.stdout))\n"
        "except OSError as exc:\n"
        "    print('refused', exc.errno)\n"
    )
    _, out, err, _ = _run(tmp_path, source, [secret_holder])
    assert out.startswith("refused 1"), out + err
    assert "planted-secret" not in out


@MACOS_ONLY
def test_the_environment_sysctl_is_a_documented_gap_under_sandbox_exec(tmp_path, secret_holder):
    # This pins a gap, not a guarantee. `ps -E` is a front end for the
    # `kern.procargs2` sysctl, and sandbox-exec does not gate that read:
    # verified against a blanket `(deny sysctl-read)` that refused
    # `kern.maxproc` in the same process, and against `(sysctl-name
    # "kern.procargs2")`, `(sysctl-name-prefix ...)` and `process-info*`
    # rules, none of which changed the result. docs/runners.md and
    # docs/security.md say so. If this test starts failing, macOS closed
    # the read, and those paragraphs -- and the recommendation that a key
    # never sits in the harness environment of an unsandboxed run -- are
    # due for an update.
    source = (
        "import ctypes, ctypes.util, sys\n"
        "libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)\n"
        "mib = (ctypes.c_int * 3)(1, 49, int(sys.argv[1]))  # CTL_KERN, KERN_PROCARGS2, pid\n"
        "size = ctypes.c_size_t(0)\n"
        "if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:\n"
        "    print('refused', ctypes.get_errno())\n"
        "else:\n"
        "    buf = ctypes.create_string_buffer(size.value or 1)\n"
        "    libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0)\n"
        "    print('visible' if b'planted-secret' in buf.raw[: size.value] else 'hidden')\n"
    )
    _, out, err, _ = _run(tmp_path, source, [secret_holder])
    assert out.strip() == "visible", out + err
