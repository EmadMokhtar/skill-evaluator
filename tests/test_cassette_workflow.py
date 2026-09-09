"""Assert the structure of refresh-cassettes.yml that carries its safety guarantees.

The workflow re-records HTTP cassettes against a real provider on demand, then
pushes the result for review. A YAML parse check proves the file is valid YAML;
it proves nothing about whether the workflow is safe to run. This file asserts
the three properties that make it safe, so an edit that quietly drops one of
them fails a test instead of shipping unnoticed:

1. The freshly-recorded cassettes are proven to replay *before* anything is
   pushed -- a re-record that cannot replay is worthless, and a human
   reviewing the YAML diff would not notice.
2. It refuses to push if a credential appears in the recordings -- a second
   lock on a door tests/conftest.py already scrubs on both sides of every
   recorded exchange. The recordings are staged first (`git add -A`), since
   a freshly recorded cassette is untracked and `git diff` alone can't see it.
3. It pushes a branch rather than opening a pull request -- a pull request
   opened with GITHUB_TOKEN gets no CI checks, and for re-recorded cassettes
   those checks are the entire point of the review.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
REFRESH = REPO_ROOT / ".github" / "workflows" / "refresh-cassettes.yml"


@pytest.fixture
def workflow() -> dict:
    return safe_load(REFRESH.read_text(encoding="utf-8"))


@pytest.fixture
def steps(workflow) -> list[dict]:
    return workflow["jobs"]["refresh"]["steps"]


def _step_index_by_run_substring(steps: list[dict], needle: str) -> int:
    for index, step in enumerate(steps):
        if needle in str(step.get("run", "")):
            return index
    raise AssertionError(f"no step's `run` contains {needle!r}")


def _step_index_by_name(steps: list[dict], name: str) -> int:
    for index, step in enumerate(steps):
        if step.get("name") == name:
            return index
    raise AssertionError(f"no step is named {name!r}")


def test_is_triggered_manually_only(workflow):
    """Re-recording spends money and needs a real API key, so it must never
    run on a push, a pull request, or a schedule -- only a human asking for it."""
    assert workflow["on"] == "workflow_dispatch"


def test_only_the_refresh_job_can_write_to_the_repository(workflow):
    assert workflow["permissions"] == {}
    assert workflow["jobs"]["refresh"]["permissions"] == {"contents": "write"}


def test_the_re_record_step_uses_rewrite_not_once(steps):
    """--record-mode=once write-protects a cassette the moment it is loaded:
    an existing recording replays instead of refreshing, and a request that
    no longer matches raises CannotOverwriteExistingCassetteException rather
    than being re-recorded. A workflow named "Refresh cassettes" that used
    `once` would be a no-op for every already-recorded test, so the
    re-record step must use `rewrite`, which actually re-records."""
    record_step = steps[_step_index_by_name(steps, "Re-record")]
    run = record_step["run"]
    assert "--record-mode=rewrite" in run
    assert "--record-mode=once" not in run


def test_the_new_recordings_are_proven_to_replay_before_anything_is_pushed(steps):
    """Property 1: a re-record that cannot replay is worthless, and a human
    reviewing the YAML diff would not notice. The workflow must catch this
    itself, and must do so before the push step runs."""
    record = _step_index_by_run_substring(steps, "--record-mode=rewrite")
    replay = _step_index_by_run_substring(steps, "--record-mode=none")
    push = _step_index_by_name(steps, "Push a branch for review")
    assert record < replay < push


def test_the_replay_proof_cannot_be_skipped_or_ignored(steps):
    """If this step could be conditionally skipped or its failure swallowed,
    the ordering guarantee above would be fiction."""
    replay_step = steps[_step_index_by_run_substring(steps, "--record-mode=none")]
    assert "if" not in replay_step
    assert "continue-on-error" not in replay_step


def test_cassettes_are_staged_before_the_checks_that_depend_on_it(steps):
    """git diff does not see untracked files, and a brand-new cassette --
    exactly what a re-record can produce -- is untracked until something
    stages it. Both the secret scan and the push-or-skip check below compare
    against the index (`git diff --cached`), so staging must happen, and
    stay staged, before either of them runs."""
    stage = _step_index_by_name(steps, "Stage the recordings")
    secret = _step_index_by_name(steps, "Refuse to push a secret")
    push = _step_index_by_name(steps, "Push a branch for review")
    assert stage < secret < push
    assert steps[stage]["run"].strip() == "git add -A -- tests/cassettes"


def test_refuses_to_push_when_a_credential_appears_in_the_recordings(steps):
    """Property 2: this is a deliberate second check on an already-locked
    door (tests/conftest.py scrubs both sides of every recorded exchange).
    It must scan the staged cassettes -- so an untracked, freshly recorded
    cassette is not invisible to it -- recognise the shapes real credentials
    take without tripping on ordinary prose, and abort rather than warn."""
    secret_step = steps[_step_index_by_name(steps, "Refuse to push a secret")]
    run = secret_step["run"]
    assert "git diff --cached -- tests/cassettes" in run
    assert "sk-[A-Za-z0-9]{20,}" in run
    assert "Bearer [A-Za-z0-9._-]{20,}" in run
    assert "exit 1" in run
    assert "continue-on-error" not in secret_step
    assert secret_step.get("if") is None


def test_the_secret_scan_checks_gits_own_exit_status_before_grepping(steps):
    """Under `pipefail`, a pipeline's exit status is its LAST command's. A
    `git diff | grep ...` pipeline would mask a `git diff` failure: grep fed
    empty input exits 1 ("no lines selected"), indistinguishable from "no
    secret found". The diff must be captured on its own, with its exit
    status checked explicitly, before the captured text is grepped."""
    secret_step = steps[_step_index_by_name(steps, "Refuse to push a secret")]
    run = secret_step["run"]
    assert "git diff --cached -- tests/cassettes | grep" not in run
    assert "git diff -- tests/cassettes | grep" not in run
    assert "if ! diff_output=" in run


def test_the_secret_patterns_ignore_ordinary_prose_but_catch_a_credential(steps):
    """A bare `sk-[A-Za-z0-9]` matches ordinary English compounds a model
    might generate ("risk-averse", "task-oriented", "desk-based"). The
    pattern must require a boundary before `sk-` and a realistic minimum
    length of key material, and the same for `Bearer`, so it stays quiet on
    prose but still catches a realistic credential shape.

    The boundary must be written in portable ERE, not as `\\b`. Both GNU and
    BSD grep accept `\\b`, but it is a GNU extension rather than a POSIX ERE
    feature, and this step fails OPEN -- a pattern that quietly stops
    matching lets a credential through and nothing downstream notices."""
    secret_step = steps[_step_index_by_name(steps, "Refuse to push a secret")]
    run = secret_step["run"]
    match = re.search(r"grep -nE '([^']*)'", run)
    assert match, "expected a single-quoted grep -nE pattern"
    assert "\\b" not in match.group(1), (
        "the boundary must not rely on \\b, which POSIX ERE does not define"
    )
    pattern = re.compile(match.group(1))
    assert pattern.search("sk-" + "a1B2c3D4e5F6g7H8i9J0") is not None
    assert pattern.search("Bearer " + "a1B2c3D4e5F6g7H8i9J0") is not None
    assert pattern.search("risk-averse") is None
    assert pattern.search("task-oriented") is None
    assert pattern.search("desk-based") is None
    # A credential mid-line, after a space or a YAML key, must still be seen.
    assert pattern.search('  authorization: "Bearer a1B2c3D4e5F6g7H8i9J0"') is not None


def test_the_secret_check_runs_before_the_push(steps):
    """A check that runs after the push has already happened protects nothing."""
    secret = _step_index_by_name(steps, "Refuse to push a secret")
    push = _step_index_by_name(steps, "Push a branch for review")
    assert secret < push


def test_pushes_a_branch_rather_than_opening_a_pull_request(steps):
    """Property 3: a pull request opened with GITHUB_TOKEN gets no CI checks
    (GitHub's own restriction on the default token), and for re-recorded
    cassettes those checks are the whole value of the review. A human
    opening the pull request from the pushed branch is what starts them."""
    push_step = steps[_step_index_by_name(steps, "Push a branch for review")]
    run = push_step["run"]
    assert 'git push origin "$branch"' in run
    assert "git diff --cached --quiet -- tests/cassettes" in run
    assert "pr create" not in run
    assert "create-pull-request" not in run
    for step in steps:
        uses = str(step.get("uses", ""))
        assert "create-pull-request" not in uses
        assert "peter-evans" not in uses


def test_the_push_step_does_not_restage_the_already_staged_cassettes(steps):
    """The cassettes are staged once, by the "Stage the recordings" step.
    Staging them again here would be at best redundant and at worst a sign
    the two checks above ran against a different index than the commit
    ends up using."""
    push_step = steps[_step_index_by_name(steps, "Push a branch for review")]
    run = push_step["run"]
    assert "git add" not in run


def test_the_api_key_is_scoped_to_the_re_record_step_only(steps):
    """The secret should not be exported any wider than the one step that
    needs it to talk to the real provider."""
    for step in steps:
        if step.get("name") == "Re-record":
            assert step["env"]["OPENAI_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
        else:
            assert "OPENAI_API_KEY" not in str(step.get("env", {}))
