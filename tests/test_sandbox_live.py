"""The real sandbox backends, on machines that have one.

Skipped where the probe fails (Windows, a Linux without bwrap, a CI runner
whose kernel refuses user namespaces). tests/test_scripts.py covers the
wrapping at string level everywhere; this file is the proof that the strings
do what they claim. Everything here is still offline: the "network" attempt
targets 127.0.0.1 and must FAIL.
"""

from __future__ import annotations

import sys
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


def _run(tmp_path: Path, source: str) -> tuple[Workspace, str, str, int | None]:
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "probe.py").write_text(source, encoding="utf-8")
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    workspace = Workspace(root=ws_root.resolve())
    result = run_script(SkillBundle(root.resolve()), workspace, "scripts/probe.py", [], _runtime())
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
