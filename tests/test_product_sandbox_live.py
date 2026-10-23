"""The product sandbox on a real backend, on machines that have one.

Skipped where the product probe finds none (Windows, a Linux without bwrap,
a CI runner whose kernel refuses user namespaces). tests/test_product_sandbox.py
covers the profile and argv at string level everywhere; this file is the proof
that they do what the trust note claims. The "product" is a `cli` command
running this interpreter. Everything stays offline: the network check
connects to a socket this test listens on at 127.0.0.1, and must SUCCEED --
a product has to reach its provider.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from skill_lens.models import EvalCase, Skill, WorkspaceSpec
from skill_lens.runners.product import Product, ProductRunner
from skill_lens.runners.product_sandbox import decide_product_sandbox
from skill_lens.workspace import Workspace, create_workspace

SANDBOX = decide_product_sandbox("auto")
# CI's macOS job sets this, so a runner whose sandbox stops working fails the
# job instead of skipping every test here and passing.
if os.environ.get("SKILL_LENS_REQUIRE_SANDBOX") and SANDBOX.backend == "none":
    raise RuntimeError(
        f"SKILL_LENS_REQUIRE_SANDBOX is set, but no product sandbox works: {SANDBOX.detail}"
    )
pytestmark = pytest.mark.skipif(
    SANDBOX.backend == "none", reason=f"no sandbox on this machine: {SANDBOX.detail}"
)

# Reads its plan from the prompt (one argv element), tries each thing, prints
# what happened as JSON -- the run's output.
PROBE = """
import json, os, pathlib, socket, sys
plan = json.loads(sys.argv[1])
out = {}
def write(path):
    try:
        pathlib.Path(path).write_text("x", encoding="utf-8")
        return "wrote"
    except OSError:
        return "refused"
out["cwd"] = write(pathlib.Path.cwd() / "inside.txt")
out["home"] = write(pathlib.Path(os.environ["HOME"]) / "home.txt")
out["outside"] = write(plan["outside"])
try:
    out["sibling"] = pathlib.Path(plan["sibling"]).read_text(encoding="utf-8")
except OSError:
    out["sibling"] = "refused"
connection = socket.socket()
connection.settimeout(5)
try:
    connection.connect(("127.0.0.1", plan["port"]))
    out["network"] = "connected"
except OSError as exc:
    out["network"] = "blocked " + type(exc).__name__
print(json.dumps(out))
"""


@pytest.fixture
def listener() -> Iterator[int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


@pytest.fixture
def workspace() -> Iterator[Workspace]:
    created = create_workspace(WorkspaceSpec(), label="live")
    try:
        yield created
    finally:
        shutil.rmtree(created.root, ignore_errors=True)


def _probe(tmp_path: Path, workspace: Workspace, port: int) -> tuple[dict, Path, Path]:
    outside = tmp_path / "outside.txt"
    sibling = tmp_path / "sibling" / "secret.txt"
    sibling.parent.mkdir()
    sibling.write_text("the other arm's file", encoding="utf-8")
    plan = json.dumps({"outside": str(outside), "sibling": str(sibling), "port": port})
    product = Product(
        name="cli",
        argv=(sys.executable, "-c", PROBE, "{prompt}"),
        skills_dir=".agents/skills",
        invoke="{task}",
        parse=None,
        version_command=None,
        pass_env=(),
    )
    skill = Skill(name="probe", description="d", instructions="i", path=tmp_path, markdown="")
    case = EvalCase(name="probe", task=plan, workspace=WorkspaceSpec())
    result = ProductRunner(product, sandbox=SANDBOX).run(skill, case, workspace)
    assert result.error is None, result.error
    return json.loads(result.output), outside, sibling


def test_the_product_writes_its_working_directory_and_home(tmp_path, workspace, listener):
    seen, _, _ = _probe(tmp_path, workspace, listener)
    assert seen["cwd"] == "wrote"
    assert seen["home"] == "wrote"
    assert (workspace.root / "inside.txt").is_file()


def test_a_write_outside_is_refused_and_never_reaches_the_host(tmp_path, workspace, listener):
    # Refused under both backends: sandbox-exec denies it, and bwrap's temp
    # dir is remounted read-only after the binds rather than left a writable
    # tmpfs where the write would vanish.
    seen, outside, _ = _probe(tmp_path, workspace, listener)
    assert seen["outside"] == "refused"
    assert not outside.exists()


def test_a_sibling_temporary_directory_cannot_be_read(tmp_path, workspace, listener):
    seen, _, _ = _probe(tmp_path, workspace, listener)
    assert seen["sibling"] == "refused"


def test_the_network_is_reachable(tmp_path, workspace, listener):
    seen, _, _ = _probe(tmp_path, workspace, listener)
    assert seen["network"] == "connected"


def test_the_product_can_write_its_own_standard_streams(tmp_path, workspace):
    # `echo msg >/dev/stderr` is a common idiom in hooks and scripts; under
    # `set -e` a refusal would abort them and score the case as failed.
    product = Product(
        name="cli",
        argv=(
            "/bin/bash",
            "-c",
            "set -e; echo e >/dev/stderr; echo o >/dev/stdout; echo f >/dev/fd/1",
            "{prompt}",
        ),
        skills_dir=".agents/skills",
        invoke="{task}",
        parse=None,
        version_command=None,
        pass_env=(),
    )
    skill = Skill(name="probe", description="d", instructions="i", path=tmp_path, markdown="")
    case = EvalCase(name="streams", task="x", workspace=WorkspaceSpec())
    result = ProductRunner(product, sandbox=SANDBOX).run(skill, case, workspace)
    assert result.error is None, result.error
    assert result.output == "o\nf"
