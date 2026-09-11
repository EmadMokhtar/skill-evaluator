"""Assert the wiring of the security checks that a reviewer relies on.

A workflow cannot be executed by the test suite, so what is testable is its
shape. Each assertion below corresponds to a decision that is invisible once
made and expensive when silently removed: the audit runs on a timer as well
as on every change, it gates a release, it runs before a push, and every
copy of the command is the same command.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY = REPO_ROOT / ".github" / "workflows" / "security.yml"
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
PYPROJECT = REPO_ROOT / "pyproject.toml"

AUDIT = "uv audit"


@pytest.fixture
def workflow() -> dict:
    return safe_load(SECURITY.read_text(encoding="utf-8"))


@pytest.fixture
def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _audit_commands_in(steps: list[dict]) -> list[str]:
    """Every line in a `run:` block that invokes the audit, stripped."""
    found = []
    for step in steps:
        for line in str(step.get("run", "")).splitlines():
            if AUDIT in line:
                found.append(line.strip())
    return found


def _security_audit_command(workflow: dict) -> str:
    commands = _audit_commands_in(workflow["jobs"]["audit"]["steps"])
    assert len(commands) == 1, f"expected exactly one audit invocation, found {commands!r}"
    return commands[0]


def test_the_audit_runs_on_a_schedule(workflow):
    """A new advisory can be published against an unchanged lockfile.

    Pull request and push triggers only see the lockfile when someone touches
    the repository; a vulnerability disclosed on a quiet week would go
    unnoticed until the next unrelated change. The timer is what makes the
    check a monitor rather than a one-off.
    """
    schedules = workflow["on"]["schedule"]
    assert schedules and all("cron" in entry for entry in schedules)


def test_the_audit_runs_on_every_change_too(workflow):
    on = workflow["on"]
    assert "pull_request" in on
    assert "main" in on["push"]["branches"]


def test_the_audit_can_be_started_by_hand(workflow):
    """After a fix lands, or when an advisory is in the news, nobody should
    have to wait for the timer or invent a commit to re-check."""
    assert "workflow_dispatch" in workflow["on"]


def test_the_audit_needs_no_write_permission(workflow):
    """The job reads a lockfile and calls a public API. Anything more is a
    token waiting to be misused by a compromised action."""
    assert workflow["permissions"] == {}
    assert workflow["jobs"]["audit"]["permissions"] == {"contents": "read"}


def test_the_audit_refuses_a_stale_lockfile(workflow):
    """Without `--locked`, uv would quietly resolve a fresh set of packages
    when pyproject.toml and uv.lock disagree, and audit something nobody
    installs. The flag turns that disagreement into a failure."""
    assert "--locked" in _security_audit_command(workflow)


def test_the_audit_never_suppresses_a_finding_permanently(workflow, pyproject):
    """`ignore` hides an advisory forever; `ignore-until-fixed` hides it only
    while no fixed version exists, and turns the check red again the day one
    is released. Only the second is allowed, and only in `[tool.uv.audit]`,
    which every invocation reads -- never on a command line, where it would
    apply to one copy of the command and not the others.

    uv does not validate that table: a misspelled key is silently dropped and
    the finding silently kept, so any key but the allowed one is rejected too.
    """
    command = _security_audit_command(workflow)
    on_command_line = [token for token in command.split() if token.startswith("--ignore")]
    assert not on_command_line, "put exceptions in [tool.uv.audit], not on the command line"

    table = pyproject["tool"].get("uv", {}).get("audit", {})
    assert set(table) <= {"ignore-until-fixed"}, (
        f"[tool.uv.audit] may only contain ignore-until-fixed, found {sorted(table)!r}"
    )


def test_a_release_is_audited_before_anything_publishes():
    """`verify` is the only gate between a merge and PyPI. A lockfile with a
    known vulnerability must fail here, not ship and be found by a user."""
    release = safe_load(RELEASE.read_text(encoding="utf-8"))
    assert _audit_commands_in(release["jobs"]["verify"]["steps"]), (
        "release.yml's verify job no longer runs the dependency audit"
    )


def _pre_push_audit_hook() -> dict:
    config = safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"]]
    matches = [hook for hook in hooks if AUDIT in str(hook.get("entry", ""))]
    assert len(matches) == 1, f"expected exactly one pre-commit audit hook, found {matches!r}"
    return matches[0]


def test_the_audit_runs_before_a_push_not_on_every_commit():
    """The audit needs the network. A commit hook would fail offline and
    teach people to skip it; a push already needs the network, so the check
    costs nothing extra there and cannot be blamed on a bad connection."""
    hook = _pre_push_audit_hook()
    assert hook["stages"] == ["pre-push"]
    assert hook.get("pass_filenames") is False
    assert hook.get("always_run") is True


def test_every_copy_of_the_audit_is_the_same_command(workflow):
    """The command is spelled in three places: the scheduled workflow, the
    release gate, and the push hook. An `--ignore-until-fixed` added to one
    and not the others would let the release ship what the schedule flags,
    or block a push over what CI accepts. Keeping them byte-identical means
    an exception is either everywhere or nowhere."""
    release = safe_load(RELEASE.read_text(encoding="utf-8"))
    commands = {
        _security_audit_command(workflow),
        *_audit_commands_in(release["jobs"]["verify"]["steps"]),
        str(_pre_push_audit_hook()["entry"]).strip(),
    }
    assert len(commands) == 1, f"the audit is spelled differently across files: {commands!r}"


def test_the_security_lint_rules_are_enabled(pyproject):
    """Ruff's `S` family (flake8-bandit) is the static check on our own code.
    It rides on the existing `ruff check`, so removing the letter from
    `select` is the only way to switch it off -- and nothing else would
    notice."""
    assert "S" in pyproject["tool"]["ruff"]["lint"]["select"]


def _locked_versions() -> dict[str, str]:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {package["name"]: package["version"] for package in lock["package"]}


def test_the_build_backend_is_in_the_audited_lockfile(pyproject):
    """uv.lock records what the project installs, not what builds it:
    `[build-system] requires` is resolved fresh at build time and never
    audited. A `build` dependency group that mirrors it puts the backend in
    the lockfile, where every copy of the audit sees it. The two lists must
    stay equal, or the group audits a backend the build does not use."""
    requires = sorted(pyproject["build-system"]["requires"])
    assert sorted(pyproject["dependency-groups"]["build"]) == requires
    for requirement in requires:
        name = re.split(r"[<>=!~\[ ]", requirement, maxsplit=1)[0]
        assert name in _locked_versions(), f"{name} is required to build but not in uv.lock"
