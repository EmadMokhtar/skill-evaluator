"""The product sandbox: the once-per-run decision, the built environment and
the wrappers. String-level everywhere; tests/test_product_sandbox_live.py is
the proof on a real backend."""

from __future__ import annotations

import subprocess

import pytest

from skill_lens.models import ProductSandboxStatus
from skill_lens.runners.product_sandbox import (
    PRODUCT_SANDBOX_OFF,
    TRUST_NOTE,
    ProductSandbox,
    ProductSetupError,
    decide_product_sandbox,
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
