"""Running a bundled script: policy, sandbox wrapping, the probe.

`run_script` itself is covered further down this file (Task 6). Everything
here is string-level or injected: no real sandbox is invoked, so the tests
pass on every platform. The real backends are exercised in
tests/test_sandbox_live.py, skipped where absent.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from skill_lens.bundle import SkillBundle
from skill_lens.models import Skill
from skill_lens.scripts import (
    DEFAULT_INTERPRETERS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    SCRATCH_PREFIX,
    SandboxStatus,
    ScriptPolicy,
    ScriptResult,
    ScriptRuntime,
    ScriptSetupError,
    bwrap_argv,
    macos_profile,
    preflight,
    probe_sandbox,
    run_script,
    script_environment,
    wrap,
)
from skill_lens.workspace import Workspace, WorkspaceLimits

WS = Path("/private/var/folders/ab/T/skill-lens-x")
SCRATCH = Path("/private/var/folders/ab/T/skill-lens-scratch-y")
TEMPDIR = Path("/private/var/folders/ab/T")
BUNDLE = Path("/private/var/folders/ab/T/pytest-of-user/pytest-1/test-case0/skill")


def test_the_defaults_are_the_documented_ones():
    assert dict(DEFAULT_INTERPRETERS) == {"py": ("python3",), "sh": ("bash",)}
    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert DEFAULT_MAX_OUTPUT_BYTES == 20_000
    policy = ScriptPolicy()
    assert policy.sandbox == "auto"
    assert dict(policy.interpreters) == dict(DEFAULT_INTERPRETERS)


def test_the_macos_profile_denies_network_and_writes_then_reallows_the_two_directories():
    profile = macos_profile(WS, SCRATCH, TEMPDIR, BUNDLE)
    lines = profile.splitlines()
    assert lines[0] == "(version 1)"
    assert "(allow default)" in lines
    assert "(deny network*)" in lines
    assert "(deny file-write*)" in lines
    assert f'(allow file-write* (subpath "{WS}") (subpath "{SCRATCH}"))' in lines
    assert '(allow file-write-data (literal "/dev/null"))' in lines
    # Sibling workspaces vanish: deny the whole temp dir, re-allow our two --
    # plus the bundle, which also lives under the temp dir (e.g. pytest's
    # tmp_path, or an extracted baseline) and must stay readable.
    assert lines.index(f'(deny file-read* (subpath "{TEMPDIR}"))') < lines.index(
        f'(allow file-read* (subpath "{WS}") (subpath "{SCRATCH}") (subpath "{BUNDLE}"))'
    )


def test_the_macos_profile_escapes_quotes_and_backslashes_in_paths():
    odd = Path('/tmp/we"ird\\dir')
    profile = macos_profile(odd, SCRATCH, TEMPDIR, BUNDLE)
    assert '(subpath "/tmp/we\\"ird\\\\dir")' in profile


def test_the_bwrap_argv_binds_the_two_directories_and_the_bundle_over_a_read_only_root():
    argv = bwrap_argv(WS, SCRATCH, TEMPDIR, ["python3", "x.py"], BUNDLE)
    assert argv[:7] == ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc"]
    tmpfs = argv.index("--tmpfs")
    assert argv[tmpfs + 1] == str(TEMPDIR)
    # The tmpfs must come BEFORE the binds, or it would hide them.
    assert tmpfs < argv.index("--bind")
    last_bind = len(argv) - 1 - argv[::-1].index("--bind")
    assert argv[argv.index("--bind") : argv.index("--bind") + 3] == ["--bind", str(WS), str(WS)]
    # The bundle is read-only, and bound in after the two writable binds so
    # the tmpfs above cannot have hidden it, and before --unshare-net.
    ro_bind = argv.index("--ro-bind", last_bind)
    assert ro_bind > last_bind
    assert argv[ro_bind : ro_bind + 3] == ["--ro-bind", str(BUNDLE), str(BUNDLE)]
    assert ro_bind < argv.index("--unshare-net")
    for flag in ("--unshare-net", "--unshare-pid", "--die-with-parent", "--new-session"):
        assert flag in argv
    assert argv[-3:] == ["--", "python3", "x.py"]


def test_wrap_leaves_argv_alone_without_a_backend():
    assert wrap("none", ["python3", "x.py"], WS, SCRATCH, TEMPDIR, BUNDLE) == [
        "python3",
        "x.py",
    ]


def test_wrap_prefixes_sandbox_exec_with_the_profile():
    wrapped = wrap("sandbox-exec", ["python3", "x.py"], WS, SCRATCH, TEMPDIR, BUNDLE)
    assert wrapped[:2] == ["sandbox-exec", "-p"]
    assert wrapped[2] == macos_profile(WS, SCRATCH, TEMPDIR, BUNDLE)
    assert wrapped[3:] == ["python3", "x.py"]


def test_wrap_prefixes_bwrap_with_the_bundle_bound_read_only():
    wrapped = wrap("bwrap", ["python3", "x.py"], WS, SCRATCH, TEMPDIR, BUNDLE)
    assert wrapped == bwrap_argv(WS, SCRATCH, TEMPDIR, ["python3", "x.py"], BUNDLE)


def _completed(returncode: int, stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=b"", stderr=stderr)


def test_off_never_probes():
    def which(_name):
        raise AssertionError("which must not be called")

    def run(*_a, **_k):
        raise AssertionError("run must not be called")

    status = probe_sandbox("off", system="Darwin", which=which, run=run)
    assert status == SandboxStatus(backend="none", detail='script_sandbox = "off"')


def test_macos_with_a_working_sandbox_exec():
    seen = {}

    def run(argv, **_kwargs):
        seen["argv"] = argv
        return _completed(0)

    status = probe_sandbox(
        "auto", system="Darwin", which=lambda _n: "/usr/bin/sandbox-exec", run=run
    )
    assert status == SandboxStatus(backend="sandbox-exec", detail="sandbox-exec probe succeeded")
    assert seen["argv"][:2] == ["sandbox-exec", "-p"]
    assert seen["argv"][-1] == "/usr/bin/true"


def test_macos_without_sandbox_exec_on_path():
    status = probe_sandbox("auto", system="Darwin", which=lambda _n: None, run=None)
    assert status == SandboxStatus(backend="none", detail="sandbox-exec not found on PATH")


def test_macos_refuses_a_temp_directory_it_cannot_express(monkeypatch):
    # tempfile.gettempdir() caches its answer in tempfile.tempdir; set that
    # directly so the injected path is picked up without touching the real
    # filesystem temp dir. The directory need not exist -- the check must
    # happen before any process is spawned, probe included.
    monkeypatch.setattr(tempfile, "tempdir", '/tmp/we"ird')

    def run(*_a, **_k):
        raise AssertionError("the probe must not run: the temp path is unexpressible")

    status = probe_sandbox(
        "auto", system="Darwin", which=lambda _n: "/usr/bin/sandbox-exec", run=run
    )
    assert status == SandboxStatus(
        backend="none",
        detail=(
            "temp directory path contains a double quote, which a sandbox-exec "
            "profile cannot express"
        ),
    )


def test_linux_reports_the_first_stderr_line_of_a_failing_bwrap_probe():
    def run(_argv, **_kwargs):
        return _completed(1, b"bwrap: setting up uid map: Permission denied\nmore\n")

    status = probe_sandbox("auto", system="Linux", which=lambda _n: "/usr/bin/bwrap", run=run)
    assert status == SandboxStatus(
        backend="none",
        detail="bwrap probe failed: bwrap: setting up uid map: Permission denied",
    )


def test_linux_with_a_working_bwrap():
    def run(*_a, **_k):
        return _completed(0)

    status = probe_sandbox("auto", system="Linux", which=lambda _n: "/usr/bin/bwrap", run=run)
    assert status == SandboxStatus(backend="bwrap", detail="bwrap probe succeeded")


def test_a_probe_that_cannot_start_is_none_with_the_error():
    def run(*_a, **_k):
        raise OSError("exec format error")

    status = probe_sandbox("auto", system="Linux", which=lambda _n: "/usr/bin/bwrap", run=run)
    assert status.backend == "none"
    assert status.detail == "bwrap probe failed: exec format error"


def test_an_unknown_platform_has_no_backend():
    status = probe_sandbox("auto", system="Windows", which=lambda _n: "x", run=None)
    assert status == SandboxStatus(backend="none", detail="no sandbox backend on Windows")


@pytest.mark.parametrize("mode", ["auto", "required"])
def test_required_and_auto_probe_the_same_way(mode):
    # The difference between them is preflight's decision, not the probe's.
    status = probe_sandbox(mode, system="Darwin", which=lambda _n: None, run=None)
    assert status.backend == "none"


NO_SANDBOX = SandboxStatus(backend="none", detail="test")


def _policy(**overrides) -> ScriptPolicy:
    # sys.executable, so no test depends on a python3 on PATH.
    settings = {"interpreters": {"py": (sys.executable,)}, "sandbox": "off"}
    settings.update(overrides)
    return ScriptPolicy(**settings)


def _runtime(**overrides) -> ScriptRuntime:
    return ScriptRuntime(policy=_policy(**overrides), sandbox=NO_SANDBOX)


def _bundle(tmp_path, **scripts: str) -> SkillBundle:
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    for name, source in scripts.items():
        (root / "scripts" / name).write_text(source, encoding="utf-8")
    return SkillBundle(root.resolve())


def _workspace(tmp_path, **limits) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(root=root.resolve(), limits=WorkspaceLimits(**limits))


# --- environment -------------------------------------------------------------


def test_the_environment_is_an_allowlist_not_a_denylist(tmp_path):
    parent = {
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "LANG": "C.UTF-8",
        "OPENAI_API_KEY": "sk-secret",
        "AWS_SECRET_ACCESS_KEY": "also-secret",
        "TMPDIR": "/somewhere/else",
    }
    env = script_environment(tmp_path, parent=parent, windows=False)
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/x"
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["TMPDIR"] == env["TMP"] == env["TEMP"] == str(tmp_path)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_windows_keeps_what_python_needs_to_start(tmp_path):
    parent = {"PATH": "C:\\x", "SystemRoot": "C:\\Windows", "COMSPEC": "cmd.exe"}
    assert script_environment(tmp_path, parent=parent, windows=True)["SystemRoot"] == "C:\\Windows"
    assert "SystemRoot" not in script_environment(tmp_path, parent=parent, windows=False)


# --- preflight ---------------------------------------------------------------


def _skill(tmp_path, *scripts: str) -> Skill:
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in scripts:
        (root / "scripts" / name).write_text("", encoding="utf-8")
    return Skill(name="pdf", path=root, bundle_root=root.resolve())


def test_preflight_rejects_a_missing_interpreter_naming_the_script(tmp_path):
    skill = _skill(tmp_path, "count.py")
    policy = ScriptPolicy(sandbox="off", interpreters={"py": ("python3",)})
    with pytest.raises(ScriptSetupError, match=r"count\.py needs python3, which is not on PATH"):
        preflight([skill], policy, which=lambda _n: None)


def test_preflight_ignores_files_with_no_mapped_extension(tmp_path):
    skill = _skill(tmp_path, "data.json", "notes.txt")
    runtime = preflight([skill], ScriptPolicy(sandbox="off"), which=lambda _n: None)
    assert runtime.sandbox.backend == "none"


def test_preflight_skips_skills_without_a_bundle(tmp_path):
    skill = Skill(name="bare", path=tmp_path)
    runtime = preflight([skill], ScriptPolicy(sandbox="off", interpreters={"py": ("nope",)}))
    assert runtime.policy.sandbox == "off"


def test_preflight_required_without_a_backend_is_a_setup_error(tmp_path):
    policy = ScriptPolicy(sandbox="required")
    with pytest.raises(ScriptSetupError, match='script_sandbox = "required" but no sandbox'):
        preflight([], policy, system="Windows")


def test_preflight_auto_without_a_backend_records_the_reason(tmp_path):
    runtime = preflight([], ScriptPolicy(sandbox="auto"), system="Windows")
    assert runtime.sandbox == SandboxStatus(backend="none", detail="no sandbox backend on Windows")


# --- run_script --------------------------------------------------------------

PRINTS = "import sys\nprint('out', *sys.argv[1:])\nprint('err', file=sys.stderr)\nsys.exit(3)\n"


def test_exit_code_stdout_stderr_and_args_round_trip(tmp_path):
    bundle = _bundle(tmp_path, **{"count.py": PRINTS})
    result = run_script(bundle, _workspace(tmp_path), "scripts/count.py", ["a", 2], _runtime())
    assert result.refused is None
    assert result.exit_code == 3
    assert result.timed_out is False
    assert result.stdout.strip() == "out a 2"
    assert result.stderr.strip() == "err"
    assert result.workspace_warning is None


def test_an_argument_with_shell_metacharacters_reaches_the_script_literally(tmp_path):
    # shell=False, argv always: run through a shell, "; echo pwned" would
    # execute as a second command instead of arriving as one literal argument.
    bundle = _bundle(tmp_path, **{"count.py": PRINTS})
    payload = "a; echo pwned; $(true) && b|c"
    result = run_script(bundle, _workspace(tmp_path), "scripts/count.py", [payload], _runtime())
    assert result.refused is None
    assert result.stdout.strip() == f"out {payload}"


def test_the_working_directory_is_the_workspace(tmp_path):
    bundle = _bundle(tmp_path, **{"cwd.py": "import os; print(os.getcwd())"})
    workspace = _workspace(tmp_path)
    result = run_script(bundle, workspace, "scripts/cwd.py", [], _runtime())
    assert Path(result.stdout.strip()).resolve() == workspace.root


def test_a_script_can_write_into_the_workspace(tmp_path):
    bundle = _bundle(tmp_path, **{"w.py": "open('out.txt', 'w').write('hello')"})
    workspace = _workspace(tmp_path)
    run_script(bundle, workspace, "scripts/w.py", [], _runtime())
    assert workspace.read("out.txt") == "hello"


def test_tmpdir_points_at_a_scratch_directory_that_is_gone_afterwards(tmp_path):
    bundle = _bundle(tmp_path, **{"t.py": "import os, tempfile; print(tempfile.gettempdir())"})
    workspace = _workspace(tmp_path)
    result = run_script(bundle, workspace, "scripts/t.py", [], _runtime())
    scratch = Path(result.stdout.strip())
    assert scratch.name.startswith(SCRATCH_PREFIX)
    assert not scratch.is_relative_to(workspace.root)
    assert not scratch.exists()


def test_the_provider_key_never_reaches_a_script(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-planted")
    bundle = _bundle(tmp_path, **{"env.py": "import os; print(sorted(os.environ))"})
    result = run_script(bundle, _workspace(tmp_path), "scripts/env.py", [], _runtime())
    assert "OPENAI_API_KEY" not in result.stdout
    assert "PATH" in result.stdout


def test_a_script_that_outlives_the_timeout_is_stopped_and_reported(tmp_path):
    source = "import time; print('start', flush=True); time.sleep(30)"
    bundle = _bundle(tmp_path, **{"sleep.py": source})
    started = time.monotonic()
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/sleep.py", [], _runtime(timeout_seconds=2.0)
    )
    assert time.monotonic() - started < 10
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.stdout.strip() == "start"


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows uses taskkill")
def test_a_timeout_kills_the_grandchild_too(tmp_path):
    # The script starts a sleeper and waits on it. Killing only the direct
    # child would leave the sleeper running until its own timer expired.
    source = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "child.wait()\n"
    )
    bundle = _bundle(tmp_path, **{"spawn.py": source})
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/spawn.py", [], _runtime(timeout_seconds=2.0)
    )
    assert result.timed_out is True
    grandchild = int(result.stdout.strip())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(grandchild, 9)
        pytest.fail("the grandchild survived the group kill")


_SPAWN_AND_EXIT = (
    "import subprocess, sys\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(child.pid, flush=True)\n"
)


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    os.kill(pid, 9)
    return False


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows uses taskkill")
def test_a_normal_exit_kills_what_the_script_left_running(tmp_path):
    # The guarantee in the docs: a script that starts a sleeper and exits at
    # once leaves nothing behind. Only the timeout path used to kill the
    # group; a script exiting normally, with its child still running, is the
    # common shape of "leaves something behind".
    bundle = _bundle(tmp_path, **{"orphan.py": _SPAWN_AND_EXIT})
    result = run_script(bundle, _workspace(tmp_path), "scripts/orphan.py", [], _runtime())
    assert result.timed_out is False
    assert result.exit_code == 0
    grandchild = int(result.stdout.strip())
    assert _gone_within(grandchild, 5), "the orphaned grandchild survived a normal exit"


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows uses taskkill")
def test_a_normal_exit_kills_what_the_script_left_running_without_waitid(tmp_path, monkeypatch):
    # CPython does not provide os.waitid on macOS before 3.13, and macOS is
    # the platform with sandbox-exec. The fallback -- wait, then killpg --
    # must give the same guarantee, so it is exercised on every interpreter
    # by deleting the attribute rather than only where it is genuinely absent.
    monkeypatch.delattr(os, "waitid", raising=False)
    bundle = _bundle(tmp_path, **{"orphan.py": _SPAWN_AND_EXIT})
    result = run_script(bundle, _workspace(tmp_path), "scripts/orphan.py", [], _runtime())
    assert result.timed_out is False
    assert result.exit_code == 0
    grandchild = int(result.stdout.strip())
    assert _gone_within(grandchild, 5), "the orphaned grandchild survived the fallback kill"


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows uses taskkill")
def test_a_timeout_kills_the_sleeper_without_waitid(tmp_path, monkeypatch):
    # The regression shape: an AttributeError before the kill would leave a
    # timed-out script running. The fallback must still stop it.
    monkeypatch.delattr(os, "waitid", raising=False)
    source = "import os, time; print(os.getpid(), flush=True); time.sleep(300)"
    bundle = _bundle(tmp_path, **{"sleep.py": source})
    started = time.monotonic()
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/sleep.py", [], _runtime(timeout_seconds=1.0)
    )
    assert time.monotonic() - started < 10
    assert result.timed_out is True
    assert result.exit_code is None
    assert _gone_within(int(result.stdout.strip()), 5), "the timed-out script survived"


def test_output_past_the_cap_is_cut_with_the_exact_count(tmp_path):
    bundle = _bundle(tmp_path, **{"big.py": "import sys; sys.stdout.write('x' * 1000)"})
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/big.py", [], _runtime(max_output_bytes=100)
    )
    assert result.stdout == "x" * 100 + "\n... [truncated, 900 bytes omitted]"


def test_output_within_the_cap_carries_no_marker(tmp_path):
    bundle = _bundle(tmp_path, **{"small.py": "import sys; sys.stdout.write('x' * 100)"})
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/small.py", [], _runtime(max_output_bytes=100)
    )
    assert result.stdout == "x" * 100


def test_a_script_writing_past_a_workspace_cap_yields_a_warning(tmp_path):
    bundle = _bundle(tmp_path, **{"fill.py": "open('big.txt', 'w').write('x' * 50)"})
    workspace = _workspace(tmp_path, max_total_bytes=10)
    result = run_script(bundle, workspace, "scripts/fill.py", [], _runtime())
    assert result.workspace_warning == (
        "warning: the working directory now holds 50 bytes; max_total_bytes is 10"
    )


def test_a_refused_path_is_returned_not_raised(tmp_path):
    bundle = _bundle(tmp_path, **{"count.py": PRINTS})
    result = run_script(bundle, _workspace(tmp_path), "scripts/nope.py", [], _runtime())
    assert result == ScriptResult(
        refused="refused: no such script 'scripts/nope.py'; bundled scripts: scripts/count.py"
    )


def test_an_interpreter_missing_at_call_time_is_a_refusal(tmp_path, monkeypatch):
    # Baseline bundles are not preflighted, so this can happen after preflight.
    bundle = _bundle(tmp_path, **{"x.sh": "echo hi"})
    monkeypatch.setattr("skill_lens.scripts.shutil.which", lambda _n: None)
    runtime = _runtime(interpreters={"sh": ("definitely-not-a-shell",)})
    result = run_script(bundle, _workspace(tmp_path), "scripts/x.sh", [], runtime)
    assert result.refused == (
        "refused: scripts/x.sh needs definitely-not-a-shell, which is not on PATH"
    )


@pytest.mark.skipif(os.name == "nt", reason="shutil.which needs a PATHEXT suffix on Windows")
def test_an_interpreter_that_will_not_start_is_a_refusal(tmp_path):
    # Executable bit set (or shutil.which would return None and the refusal
    # would be "not on PATH"), but not a real binary: exec fails.
    bundle = _bundle(tmp_path, **{"x.py": "print(1)"})
    broken = tmp_path / "broken"
    broken.write_text("not executable", encoding="utf-8")
    broken.chmod(0o755)
    runtime = ScriptRuntime(
        policy=ScriptPolicy(sandbox="off", interpreters={"py": (str(broken),)}), sandbox=NO_SANDBOX
    )
    result = run_script(bundle, _workspace(tmp_path), "scripts/x.py", [], runtime)
    assert result.refused is not None
    assert result.refused.startswith(f"refused: cannot start {broken}")


# --- guard hardening (fix round 1) --------------------------------------------


def test_a_symlink_planted_over_the_capture_file_does_not_leak_its_target(tmp_path):
    # A script that unlinks $TMPDIR/stdout and replaces it with a symlink to
    # another skill-lens workspace must not make the harness read that
    # target's content back instead of what was actually captured.
    target = tmp_path / "secret.txt"
    target.write_text("attacker content", encoding="utf-8")
    source = (
        "import os\n"
        "print('real output', flush=True)\n"
        "capture = os.path.join(os.environ['TMPDIR'], 'stdout')\n"
        "os.remove(capture)\n"
        f"os.symlink({str(target)!r}, capture)\n"
    )
    bundle = _bundle(tmp_path, **{"swap.py": source})
    result = run_script(bundle, _workspace(tmp_path), "scripts/swap.py", [], _runtime())
    assert result.stdout.strip() == "real output"
    assert "attacker" not in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="os.mkfifo does not exist on Windows")
def test_a_fifo_planted_over_the_capture_file_does_not_hang_the_call(tmp_path):
    # A script that replaces $TMPDIR/stdout with a FIFO must not make the
    # harness block forever trying to read it back after the run is over.
    source = (
        "import os\n"
        "print('real output', flush=True)\n"
        "capture = os.path.join(os.environ['TMPDIR'], 'stdout')\n"
        "os.remove(capture)\n"
        "os.mkfifo(capture)\n"
    )
    bundle = _bundle(tmp_path, **{"fifo.py": source})
    started = time.monotonic()
    result = run_script(bundle, _workspace(tmp_path), "scripts/fifo.py", [], _runtime())
    assert time.monotonic() - started < 10
    assert result.stdout.strip() == "real output"


def test_a_null_byte_in_an_argument_is_a_refusal_not_a_crash(tmp_path):
    # A model can put a NUL byte inside a JSON tool-call argument; str()
    # preserves it, and Popen raises ValueError for it rather than OSError.
    bundle = _bundle(tmp_path, **{"count.py": PRINTS})
    result = run_script(bundle, _workspace(tmp_path), "scripts/count.py", ["a\x00b"], _runtime())
    assert result.refused is not None
    assert result.refused.startswith("refused: cannot start")


def test_a_capture_file_that_cannot_be_created_is_a_refusal(tmp_path, monkeypatch):
    # Opening the stdout/stderr capture files sat outside any handler: a full
    # disk or a read-only temp filesystem after mkdtemp succeeded would have
    # propagated as a raw OSError. Patch Path.open narrowly -- only for a
    # path literally named "stdout" -- so the rest of run_script (resolving
    # the script, creating the scratch directory) still goes through the
    # real filesystem; this is the least invasive way to exercise a disk
    # failure that arrives exactly when the capture files are opened.
    bundle = _bundle(tmp_path, **{"count.py": PRINTS})
    real_open = Path.open

    def failing_open(self, *args, **kwargs):
        if self.name == "stdout":
            raise OSError("no space left on device")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    result = run_script(bundle, _workspace(tmp_path), "scripts/count.py", [], _runtime())
    assert result.refused == "refused: cannot create the capture files: no space left on device"
