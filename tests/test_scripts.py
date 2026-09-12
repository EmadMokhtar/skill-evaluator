"""Running a bundled script: policy, sandbox wrapping, the probe.

`run_script` itself is covered further down this file (Task 6). Everything
here is string-level or injected: no real sandbox is invoked, so the tests
pass on every platform. The real backends are exercised in
tests/test_sandbox_live.py, skipped where absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from skill_lens.scripts import (
    DEFAULT_INTERPRETERS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    SandboxStatus,
    ScriptPolicy,
    bwrap_argv,
    macos_profile,
    probe_sandbox,
    wrap,
)

WS = Path("/private/var/folders/ab/T/skill-lens-x")
SCRATCH = Path("/private/var/folders/ab/T/skill-lens-scratch-y")
TEMPDIR = Path("/private/var/folders/ab/T")


def test_the_defaults_are_the_documented_ones():
    assert dict(DEFAULT_INTERPRETERS) == {"py": ("python3",), "sh": ("bash",)}
    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert DEFAULT_MAX_OUTPUT_BYTES == 20_000
    policy = ScriptPolicy()
    assert policy.sandbox == "auto"
    assert dict(policy.interpreters) == dict(DEFAULT_INTERPRETERS)


def test_the_macos_profile_denies_network_and_writes_then_reallows_the_two_directories():
    profile = macos_profile(WS, SCRATCH, TEMPDIR)
    lines = profile.splitlines()
    assert lines[0] == "(version 1)"
    assert "(allow default)" in lines
    assert "(deny network*)" in lines
    assert "(deny file-write*)" in lines
    assert f'(allow file-write* (subpath "{WS}") (subpath "{SCRATCH}"))' in lines
    assert '(allow file-write-data (literal "/dev/null"))' in lines
    # Sibling workspaces vanish: deny the whole temp dir, re-allow our two.
    assert lines.index(f'(deny file-read* (subpath "{TEMPDIR}"))') < lines.index(
        f'(allow file-read* (subpath "{WS}") (subpath "{SCRATCH}"))'
    )


def test_the_macos_profile_escapes_quotes_and_backslashes_in_paths():
    odd = Path('/tmp/we"ird\\dir')
    profile = macos_profile(odd, SCRATCH, TEMPDIR)
    assert '(subpath "/tmp/we\\"ird\\\\dir")' in profile


def test_the_bwrap_argv_binds_the_two_directories_over_a_read_only_root():
    argv = bwrap_argv(WS, SCRATCH, TEMPDIR, ["python3", "x.py"])
    assert argv[:7] == ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc"]
    tmpfs = argv.index("--tmpfs")
    assert argv[tmpfs + 1] == str(TEMPDIR)
    # The tmpfs must come BEFORE the binds, or it would hide them.
    assert tmpfs < argv.index("--bind")
    assert argv[argv.index("--bind") : argv.index("--bind") + 3] == ["--bind", str(WS), str(WS)]
    for flag in ("--unshare-net", "--unshare-pid", "--die-with-parent", "--new-session"):
        assert flag in argv
    assert argv[-3:] == ["--", "python3", "x.py"]


def test_wrap_leaves_argv_alone_without_a_backend():
    assert wrap("none", ["python3", "x.py"], WS, SCRATCH, TEMPDIR) == ["python3", "x.py"]


def test_wrap_prefixes_sandbox_exec_with_the_profile():
    wrapped = wrap("sandbox-exec", ["python3", "x.py"], WS, SCRATCH, TEMPDIR)
    assert wrapped[:2] == ["sandbox-exec", "-p"]
    assert wrapped[2] == macos_profile(WS, SCRATCH, TEMPDIR)
    assert wrapped[3:] == ["python3", "x.py"]


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
