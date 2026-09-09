"""Assert the structure of release.yml that carries the design decisions.

A workflow cannot be executed by the test suite, so what is testable is its
shape. Each assertion below corresponds to a decision that is invisible once
made and expensive when silently removed.

Task 5 adds the `publish` job and its own tests for it; this file covers the
`verify`, `release`, and `publish` jobs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"


@pytest.fixture
def workflow() -> dict:
    return safe_load(RELEASE.read_text(encoding="utf-8"))


def test_only_the_release_job_may_write_to_the_repository(workflow):
    assert workflow["jobs"]["release"]["permissions"]["contents"] == "write"
    assert workflow["jobs"]["verify"]["permissions"]["contents"] == "read"


def test_releases_are_serialised_and_never_cancelled(workflow):
    """Cancelling a release halfway can leave a tag pushed and nothing built."""
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_the_bump_reads_the_whole_history(workflow):
    """cz bump computes the increment from every commit since the last tag."""
    checkout = next(
        step for step in workflow["jobs"]["release"]["steps"] if "checkout" in str(step.get("uses"))
    )
    assert checkout["with"]["fetch-depth"] == 0


def test_the_pushed_tag_is_verified_before_the_build(workflow):
    """`git push --follow-tags` pushes only annotated tags. If the tag never
    reached origin, nothing else would catch it: the `publish` job (added in
    a later task) is reached through `needs:`, not through the tag. So this
    step must exist, and must not run when no version was cut.
    """
    steps = workflow["jobs"]["release"]["steps"]
    verify_step = next(step for step in steps if "ls-remote" in str(step.get("run", "")))
    assert verify_step["if"] == "steps.bump.outputs.bumped == 'true'"


def test_nothing_runs_before_the_tests_pass(workflow):
    """Publishing is irreversible: PyPI refuses a re-upload of a version."""
    assert workflow["jobs"]["release"]["needs"] == "verify"
    assert workflow["jobs"]["publish"]["needs"] == "release"


def test_publish_is_skipped_when_no_version_was_cut(workflow):
    condition = workflow["jobs"]["publish"]["if"]
    assert condition == "needs.release.outputs.bumped == 'true'"


def test_publish_can_mint_an_identity_but_cannot_write_to_the_repository(workflow):
    permissions = workflow["jobs"]["publish"]["permissions"]
    assert permissions == {"id-token": "write", "contents": "read"}


def test_publish_is_gated_by_the_protected_environment(workflow):
    """The pending publisher on PyPI is bound to this environment name."""
    assert workflow["jobs"]["publish"]["environment"]["name"] == "pypi"
