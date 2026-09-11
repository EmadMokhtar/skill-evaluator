"""Assert the supply-chain posture of the repository's own automation.

None of this is executable from a test, so what is testable is its shape:
every third-party action is pinned to a commit, no workflow hands a write
token to a job that does not need one, Dependabot is watching both
ecosystems with titles the release automation can parse, and the security
policy points a reporter somewhere private.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
# GitHub runs both spellings, so a guard over one alone has a hole in it.
WORKFLOWS = sorted(
    path
    for ext in ("yml", "yaml")
    for path in (REPO_ROOT / ".github" / "workflows").glob(f"*.{ext}")
)
ACTION = REPO_ROOT / "action.yml"
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"
SECURITY_POLICY = REPO_ROOT / "SECURITY.md"

# `owner/repo@<40 hex>` or `owner/repo/path@<40 hex>`; `./` is this repository.
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
# The version the pin corresponds to, kept in a trailing comment so a human can
# read it and Dependabot can keep it current alongside the SHA.
PIN_LINE = re.compile(r"uses:\s*\S+@[0-9a-f]{40}\s+#\s*v\d+\.\d+\.\d+\s*$")


def _uses(path: Path) -> list[str]:
    """Every `uses:` in a file: each step's, and -- for a workflow -- each
    job's own, which is how a reusable workflow is called. A job-level
    reference runs third-party code just as a step does."""
    document = safe_load(path.read_text(encoding="utf-8"))
    if "jobs" in document:
        jobs = document["jobs"].values()
        holders = [job for job in jobs] + [step for job in jobs for step in job.get("steps", [])]
    else:
        holders = document["runs"]["steps"]
    return [str(holder["uses"]) for holder in holders if "uses" in holder]


@pytest.mark.parametrize("path", [*WORKFLOWS, ACTION], ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit(path: Path):
    """A tag can be moved; a commit cannot. `actions/checkout@v4` runs
    whatever `v4` points at on the day, so a compromised or mistaken
    re-tag runs different code in CI with no change in this repository."""
    unpinned = [uses for uses in _uses(path) if uses != "./" and not PINNED.match(uses)]
    assert not unpinned, f"{path.name}: not pinned to a commit: {unpinned}"


@pytest.mark.parametrize("path", [*WORKFLOWS, ACTION], ids=lambda p: p.name)
def test_every_pin_says_which_version_it_is(path: Path):
    """A bare SHA tells a reviewer nothing. The trailing `# vX.Y.Z` is what
    makes the pin readable, and it is the comment Dependabot rewrites when
    it bumps the SHA, so the two never disagree."""
    lines = path.read_text(encoding="utf-8").splitlines()
    bare = [
        line.strip()
        for line in lines
        if re.search(r"uses:\s*\S+@[0-9a-f]{40}", line) and not PIN_LINE.search(line)
    ]
    assert not bare, f"{path.name}: pinned without a version comment: {bare}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_grants_write_at_the_top_level(path: Path):
    """A workflow-level grant is inherited by every job that does not
    override it. Write access is granted per job, to the job that uses it,
    against a top-level block that is empty or read-only."""
    workflow = safe_load(path.read_text(encoding="utf-8"))
    assert "permissions" in workflow, f"{path.name} declares no top-level permissions"
    top = workflow["permissions"]
    assert top == {} or all(value == "read" for value in top.values()), (
        f"{path.name} grants write access at the workflow level: {top}"
    )


def test_the_docs_build_job_cannot_deploy():
    """Only `deploy` publishes to Pages, so only `deploy` holds the token
    that can. `build` runs third-party tooling on the checkout and needs
    read access only -- including `pages: read`, because
    `actions/configure-pages` calls `GET /repos/{owner}/{repo}/pages` and
    fails the job when that call is refused."""
    docs = safe_load((REPO_ROOT / ".github" / "workflows" / "docs.yml").read_text("utf-8"))
    assert docs["jobs"]["build"]["permissions"] == {"contents": "read", "pages": "read"}
    assert docs["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}


@pytest.fixture
def dependabot() -> dict:
    return safe_load(DEPENDABOT.read_text(encoding="utf-8"))


def test_dependabot_watches_the_lockfile_and_the_actions(dependabot):
    """SHA pins and a lockfile only stay current if something proposes the
    updates. Both ecosystems live at the repository root: `uv` finds
    uv.lock, `github-actions` finds .github/workflows/ and action.yml."""
    watched = {(u["package-ecosystem"], u["directory"]) for u in dependabot["updates"]}
    assert {("uv", "/"), ("github-actions", "/")} <= watched, watched


def test_dependabot_never_groups_a_major_bump(dependabot):
    """A major bump may need code changes and deserves its own pull request;
    grouped with a dozen patch bumps it is invisible until something
    breaks. Every group is limited to minor and patch updates."""
    for update in dependabot["updates"]:
        for name, group in update.get("groups", {}).items():
            assert set(group.get("update-types", [])) == {"minor", "patch"}, (
                f"group {name!r} in {update['package-ecosystem']} would batch major bumps"
            )


def test_dependabot_titles_are_conventional_commits(dependabot):
    """Pull requests are squash-merged, so the title becomes the commit on
    main and `cz bump` parses it. A Dependabot title that fails `cz check`
    would be rejected by the conventional-commits job on every update."""
    for update in dependabot["updates"]:
        prefix = update["commit-message"]["prefix"]
        message = f"{prefix}: bump example from 1.0.0 to 1.0.1"
        result = subprocess.run(
            ["uv", "run", "cz", "check", "--message", message],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        assert result.returncode == 0, (
            f"prefix {prefix!r} yields a non-conventional title: {result.stdout}{result.stderr}"
        )


def test_the_security_policy_points_at_private_reporting():
    """A vulnerability reported as a public issue is disclosed before it
    is fixed. The policy has to name the private channel, and that channel
    is a repository setting that nothing here can turn on -- so the docs
    also have to tell a maintainer to enable it (tests/test_docs.py)."""
    text = SECURITY_POLICY.read_text(encoding="utf-8")
    assert "https://github.com/EmadMokhtar/skill-evaluator/security/advisories/new" in text
    assert "do not open a public issue" in text.lower()
