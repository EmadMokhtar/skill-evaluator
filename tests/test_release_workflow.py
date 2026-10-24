"""Assert the structure of release.yml that carries the design decisions.

A workflow cannot be executed by the test suite, so what is testable is its
shape. Each assertion below corresponds to a decision that is invisible once
made and expensive when silently removed.

Task 5 adds the `publish` job and its own tests for it; this file covers the
`verify`, `release`, and `publish` jobs.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"


@pytest.fixture
def workflow() -> dict:
    return safe_load(RELEASE.read_text(encoding="utf-8"))


def test_only_the_release_job_may_write_to_the_repository(workflow):
    assert workflow["jobs"]["release"]["permissions"]["contents"] == "write"
    assert workflow["jobs"]["verify"]["permissions"]["contents"] == "read"


def test_no_permission_is_granted_at_the_workflow_level(workflow):
    """Every permission is granted per job, none at the top.

    A workflow-level grant is inherited by every job that does not override it,
    so moving `contents: write` up here would silently hand it to `verify` and
    to `publish` -- and the per-job assertions above would all still pass,
    because they only check the jobs that *do* declare permissions. The empty
    top-level block is what makes those per-job grants exhaustive.
    """
    assert workflow["permissions"] == {}


def test_the_verify_job_actually_runs_the_tests(workflow):
    """`verify` exists to stop a release publishing untested code.

    `needs: verify` on the release job only proves the job ran, not what it
    ran. Delete the test step and every other assertion in this file still
    passes while releases go out with nothing but a lint behind them.
    """
    runs = [str(step.get("run", "")) for step in workflow["jobs"]["verify"]["steps"]]
    assert any("pytest" in run for run in runs), (
        "the verify job no longer runs the test suite: " + repr(runs)
    )


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
    assert verify_step["if"] == "steps.push.outputs.pushed == 'true'"


def test_the_verification_spells_the_tag_the_way_commitizen_does(workflow):
    """The step above reconstructs the tag name to look it up, and hardcodes
    the prefix while `[tool.commitizen] tag_format` configures it. Two copies
    of one string, in different files, with nothing tying them together.

    Change `tag_format` alone and the release still tags correctly, the push
    still succeeds, and only the lookup goes wrong -- reporting a tag missing
    that is in fact on origin, and failing the job *after* the commit and tag
    have been pushed. That is the one unrecoverable state in this pipeline:
    the version is spent, `publish` never became eligible so it cannot be
    re-run, and a fresh run finds nothing to release. It fails closed rather
    than shipping something wrong, but it costs a version and sends whoever
    reads the error to the wrong file.

    So derive the prefix from `tag_format` and require the workflow to use
    the same one. Changing the format now forces changing both.
    """
    tag_format = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["commitizen"][
        "tag_format"
    ]
    prefix, sentinel, suffix = tag_format.partition("$version")
    assert sentinel, f"tag_format {tag_format!r} does not interpolate $version"
    assert not suffix, f"tag_format {tag_format!r} has a suffix this test cannot express"

    steps = workflow["jobs"]["release"]["steps"]
    verify_step = next(step for step in steps if "ls-remote" in str(step.get("run", "")))
    assert verify_step["env"]["VERSION"] == "${{ steps.bump.outputs.version }}"
    expected = 'tag="' + prefix + '$VERSION"'
    assert expected in verify_step["run"], (
        f"tag_format is {tag_format!r}, so the verification step must build {expected!r}"
    )


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


def test_publish_uploads_the_artifact_the_release_job_built(workflow):
    """The bytes published must be the bytes `verify` and `release` saw.

    Swapping the download for a fresh `uv build` here would look harmless and
    publish an artifact nothing in the run had tested -- in the one job whose
    mistakes cannot be undone, since PyPI refuses a re-upload of a version.
    So: the download step exists, it names the artifact `release` uploaded,
    and nothing in this job builds.
    """
    release_steps = workflow["jobs"]["release"]["steps"]
    publish_steps = workflow["jobs"]["publish"]["steps"]

    upload = next(step for step in release_steps if "upload-artifact" in str(step.get("uses", "")))
    download = next(
        (step for step in publish_steps if "download-artifact" in str(step.get("uses", ""))),
        None,
    )
    assert download is not None, "publish no longer downloads the artifact release built"
    assert download["with"]["name"] == upload["with"]["name"]

    builders = ("uv build", "python -m build", "hatch build", "pyproject-build", "pypa/build")
    for step in publish_steps:
        body = f"{step.get('run', '')} {step.get('uses', '')}"
        offender = next((marker for marker in builders if marker in body), None)
        assert offender is None, f"publish builds ({offender!r}) instead of downloading"


def test_the_bump_refuses_a_tree_whose_pins_did_not_move(workflow):
    """`--check-consistency` is what makes a stale pin loud.

    cz bump gates each `version_files` entry on a regex and rewrites a file
    that matched nothing without complaint, so reformatting a pinned line
    would tag a tree still pinning the previous version. Nothing downstream
    would report it: the bump commit carries `[skip ci]`, so the bumped tree is
    never tested. Commitizen reads this flag from the command line only, never
    from `[tool.commitizen]`, which is why the guard lives in the workflow and
    this assertion is here rather than in tests/test_release_config.py.

    The flag is looked for on the invocation line itself, not anywhere in the
    step body: the step's own comment explains the flag, so a substring search
    over the whole script would stay green with the flag removed from the
    command -- which is how this assertion was first written, and how it was
    caught.
    """
    steps = workflow["jobs"]["release"]["steps"]
    bump = next(step for step in steps if step.get("id") == "bump")
    invocations = [
        line.strip()
        for line in bump["run"].splitlines()
        if line.strip().startswith("uv run cz bump")
    ]
    assert len(invocations) == 1, f"expected one cz bump invocation, found {invocations}"
    assert "--check-consistency" in invocations[0], (
        "cz bump no longer checks version_files consistency: " + invocations[0]
    )


def _release_step(workflow: dict, needle: str) -> dict:
    steps = workflow["jobs"]["release"]["steps"]
    return next(step for step in steps if needle in str(step.get("run", "")))


def test_the_sbom_describes_what_an_installer_gets(workflow):
    """The SBOM is exported from the lockfile that was just verified, in
    CycloneDX, for the runtime dependency set including the optional
    `pydantic-ai` extra -- and excluding the dev and docs tooling, which
    nobody who installs the package receives."""
    step = _release_step(workflow, "cyclonedx")
    run = step["run"]
    assert "--format cyclonedx1.5" in run
    assert "--frozen" in run, "the export must read uv.lock as-is, not re-resolve"
    assert "--all-extras" in run
    assert "--no-default-groups" in run
    assert step["if"] == "steps.push.outputs.pushed == 'true'"


def test_the_sbom_never_enters_the_pypi_upload(workflow):
    """`publish` uploads every file in the `dist` artifact. An SBOM in
    there would be sent to PyPI, which rejects it -- and a rejected file
    fails the upload after the tag is already pushed."""
    export = _release_step(workflow, "cyclonedx")["run"]
    match = re.search(r"(?:-o|--output-file)\s+(\S+)", export)
    assert match, f"the export does not write to a file: {export!r}"
    # The path is shell-quoted in the workflow; the quote is not part of it.
    target = match.group(1).strip("\"'")
    assert not target.startswith("dist/"), "the SBOM must not be written under dist/"
    uploads = [
        step
        for step in workflow["jobs"]["release"]["steps"]
        if "upload-artifact" in str(step.get("uses"))
    ]
    names = {upload["with"]["name"]: upload["with"]["path"] for upload in uploads}
    assert names["dist"].rstrip("/") == "dist"
    assert "sbom" in names, "the SBOM is uploaded as its own artifact, apart from dist"


def test_the_github_release_is_created_only_after_pypi_accepted_the_upload(workflow):
    """A GitHub Release is the first outward-facing sign of a version. It
    comes after `publish`, never before, so it cannot advertise a version
    PyPI does not have."""
    job = workflow["jobs"]["github-release"]
    # A list, because the job also reads `release`'s outputs and GitHub only
    # exposes the outputs of direct `needs`.
    assert "publish" in job["needs"]
    assert job["permissions"] == {"contents": "write"}


def test_the_github_release_can_be_re_run(workflow):
    """The documented recovery for a failed step is to re-run the job. That
    only works if creating the release is skipped when it already exists
    and uploading the assets overwrites rather than refuses."""
    runs = " ".join(
        str(step.get("run", "")) for step in workflow["jobs"]["github-release"]["steps"]
    )
    assert "gh release view" in runs, "creation is not guarded by an existence check"
    assert "gh release upload" in runs and "--clobber" in runs


def test_the_release_notes_are_the_changelog_section_for_that_version(workflow):
    """The changelog is generated from the same commits that chose the
    version; nothing else is a source of truth for what a release contains.
    It is written in `release`, which already has the checkout, the
    history and the tools, and handed on as an artifact."""
    notes = _release_step(workflow, "cz changelog")
    assert "--dry-run" in notes["run"]
    assert notes["if"] == "steps.push.outputs.pushed == 'true'"
    uploads = {
        step["with"]["name"]
        for step in workflow["jobs"]["release"]["steps"]
        if "upload-artifact" in str(step.get("uses"))
    }
    assert "release-notes" in uploads


def test_the_privileged_release_job_installs_nothing(workflow):
    """`github-release` holds `contents: write`. Everything it publishes was
    built and verified by earlier jobs, so it downloads artifacts and runs
    `gh` -- no checkout, no dependency install, no build hook that could run
    third-party code under that token."""
    steps = workflow["jobs"]["github-release"]["steps"]
    uses = [str(step.get("uses", "")) for step in steps]
    assert not any("checkout" in u or "setup-uv" in u for u in uses), uses
    runs = " ".join(str(step.get("run", "")) for step in steps)
    assert "uv " not in runs, "the privileged job must not run uv: " + runs


def test_the_release_builds_with_the_audited_backend(workflow):
    """`uv build` resolves the build backend fresh from `[build-system]`
    unless told otherwise, so the artifact could be produced by a hatchling
    the audit never saw. The constraint file is exported from the lockfile
    (`--frozen`, the `build` group only) and handed to the build, so the
    backend and its own dependencies are exactly the audited ones."""
    build = next(
        step for step in workflow["jobs"]["release"]["steps"] if "uv build" in str(step.get("run"))
    )
    run = build["run"]
    export = re.search(r"uv export\s+(.*?)\s+-o\s+(\S+)", run)
    assert export, f"the build step does not export a constraint file: {run!r}"
    flags, constraints = export.group(1), export.group(2)
    assert "--frozen" in flags and "--only-group build" in flags, flags
    assert f"uv build --build-constraint {constraints}" in run, run


# --- A run that main outran -------------------------------------------------
#
# Two merges close together each start a release run. The concurrency group
# keeps the runs from overlapping, but a merge that lands while a run is in
# progress moves main all the same. The earlier run's `cz bump` then rests on
# a commit that is no longer main's tip, and its push is rejected as a
# non-fast-forward. The later push has a run of its own whose `cz bump` reads
# every commit since the last tag -- the earlier commit included -- so the
# earlier run has nothing left to release: the same no-op as cz's exit 21 and
# 3, not a failure. The push step's script decides this, so these tests run
# that script, as GitHub would, against a local bare origin.


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _clone(origin: Path, into: Path) -> Path:
    """A clone with a committer identity, so commits succeed in CI."""
    subprocess.run(["git", "clone", "-q", str(origin), str(into)], check=True)
    _git("config", "user.email", "t@example.com", cwd=into)
    _git("config", "user.name", "Test", cwd=into)
    return into


def _commit(repo: Path, message: str) -> str:
    (repo / "file.txt").write_text(message + "\n", encoding="utf-8")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _checkout(tmp_path: Path) -> tuple[Path, Path, str]:
    """The release job's checkout before `cz bump`: HEAD and main's tip are both
    the pushed commit (`GITHUB_SHA`)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = _clone(origin, tmp_path / "seed")
    _commit(seed, "feat: the merge this run is for")
    _git("push", "-q", "origin", "HEAD:main", cwd=seed)
    work = _clone(origin, tmp_path / "work")
    return origin, work, _git("rev-parse", "HEAD", cwd=work)


def _runner(tmp_path: Path) -> tuple[Path, Path, str]:
    """The release job's checkout after `cz bump`: main at the pushed commit
    (`GITHUB_SHA`), the bump commit on top of it, and an annotated tag."""
    origin, work, github_sha = _checkout(tmp_path)
    _commit(work, "bump: version 0.0.0 → 9.9.9 [skip ci]")
    _git("tag", "-a", "v9.9.9", "-m", "9.9.9", cwd=work)
    return origin, work, github_sha


def _run_push_step(
    workflow: dict, work: Path, github_sha: str, tmp_path: Path
) -> tuple[subprocess.CompletedProcess, str, str]:
    """Run the push step's script the way `shell: bash` does."""
    step = _release_step(workflow, "git push")
    assert step.get("id") == "push", "the push step must be addressable as steps.push"
    assert step.get("shell") == "bash"
    output = tmp_path / "github_output"
    summary = tmp_path / "github_step_summary"
    output.touch()
    summary.touch()
    env = {
        **os.environ,
        "GITHUB_SHA": github_sha,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "VERSION": "9.9.9",
    }
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8")


def _origin_tags(origin: Path) -> str:
    return _git("tag", "--list", cwd=origin)


def test_a_push_that_lands_reports_it(workflow, tmp_path):
    origin, work, github_sha = _runner(tmp_path)

    proc, output, summary = _run_push_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "pushed=true" in output
    assert "9.9.9" in summary
    assert _git("rev-parse", "main", cwd=origin) == _git("rev-parse", "HEAD", cwd=work)
    assert _origin_tags(origin) == "v9.9.9"


def test_a_run_that_main_outran_publishes_nothing_and_fails_nothing(workflow, tmp_path):
    """The failure this guards against: a `fix:` merged, and a `feat:` merged
    three minutes later released both as one minor version while the first
    run's `release` job was still queued. That job's push was rejected and
    the run turned red over a release that had already happened."""
    origin, work, github_sha = _runner(tmp_path)
    other = _clone(origin, tmp_path / "other")
    later = _commit(other, "feat: the merge that overtook this run")
    _git("push", "-q", "origin", "HEAD:main", cwd=other)

    proc, output, summary = _run_push_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "pushed=false" in output
    assert "pushed=true" not in output
    assert later[:7] in summary, summary
    assert "since the last tag" in summary, summary
    # Nothing landed: --atomic held the commit and the tag back together.
    assert _git("rev-parse", "main", cwd=origin) == later
    assert _origin_tags(origin) == ""


def test_a_rejected_push_that_main_did_not_outrun_still_fails(workflow, tmp_path):
    """main is where this run left it, yet the push is rejected -- a branch
    protection rule, say, played here by a pre-receive hook. No later run
    carries this commit, so this is not the documented no-op, and reporting
    it as one would leave a releasable commit unreleased in silence."""
    origin, work, github_sha = _runner(tmp_path)
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'refused by the origin' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    proc, output, _ = _run_push_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode != 0
    assert "pushed=true" not in output
    assert "pushed=false" not in output
    assert github_sha[:7] in proc.stderr, proc.stderr


def test_a_rewritten_main_is_not_a_run_that_was_outrun(workflow, tmp_path):
    """main moved, but not past this run's commit: it was force-pushed to a
    history that does not contain it. No later run carries this commit, so
    silence here would be the vacuous pass the workflow refuses elsewhere."""
    origin, work, github_sha = _runner(tmp_path)
    other = _clone(origin, tmp_path / "other")
    _git("checkout", "-q", "--orphan", "rewritten", cwd=other)
    _commit(other, "chore: a history without this run's commit")
    _git("push", "-q", "--force", "origin", "HEAD:main", cwd=other)

    proc, output, _ = _run_push_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode != 0
    assert "pushed=" not in output


# --- A run that starts behind main --------------------------------------------
#
# The push step catches a merge that lands while `release` runs. A run can also
# reach `release` already behind main. GitHub keeps one run waiting per
# concurrency group and cancels it when a newer merge queues, so a release
# waiting hours for its `pypi` approval leaves a row of cancelled runs behind
# it; the newest one releases all their commits. Re-running one of those runs
# checks out its old commit again. Its `cz bump` reads that commit's
# pyproject.toml, computes the version the newer run already tagged, and fails
# on `tag 'v0.25.0' already exists` (exit 7) before the push step can call it a
# no-op. So the job asks, before the bump, whether main has moved past this
# run's commit -- the push step's test, made earlier.


def _tip_step(workflow: dict) -> dict:
    return next(step for step in workflow["jobs"]["release"]["steps"] if step.get("id") == "tip")


def _run_tip_step(
    workflow: dict, work: Path, github_sha: str, tmp_path: Path, ref: str = "refs/heads/main"
) -> tuple[subprocess.CompletedProcess, str, str]:
    """Run the tip step's script the way `shell: bash` does."""
    step = _tip_step(workflow)
    assert step.get("shell") == "bash"
    output = tmp_path / "tip_output"
    summary = tmp_path / "tip_summary"
    output.touch()
    summary.touch()
    env = {
        **os.environ,
        "GITHUB_REF": ref,
        "GITHUB_SHA": github_sha,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8")


def test_the_bump_runs_only_while_this_runs_commit_is_mains_tip(workflow):
    """The tag check and the bump both wait for the tip step. A skipped bump
    writes no `bumped`, and the push and the recover step compare `bumped`
    with a value, so neither runs on that path either: a run behind main ends
    with nothing pushed and nothing published, exactly as the push step's
    no-op does."""
    steps = workflow["jobs"]["release"]["steps"]
    tip = _tip_step(workflow)
    assert steps.index(tip) < _step_index(steps, id="check") < _step_index(steps, id="bump")
    assert "${{" not in tip["run"], tip["run"]
    for step_id in ("check", "bump"):
        assert steps[_step_index(steps, id=step_id)]["if"] == "steps.tip.outputs.current == 'true'"
    for step_id in ("push", "recover"):
        assert "steps.bump.outputs.bumped ==" in steps[_step_index(steps, id=step_id)]["if"]


def test_a_run_on_mains_tip_goes_on_to_the_bump(workflow, tmp_path):
    _, work, github_sha = _checkout(tmp_path)

    proc, output, summary = _run_tip_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert output == "current=true\n"
    assert summary == ""


def test_a_re_run_behind_a_release_main_already_made_is_a_no_op(workflow, tmp_path):
    """The failure this guards against: v0.24.3's `publish` waited four hours
    for approval, six merges queued behind it and each cancelled the one before,
    and the last released all of them as v0.25.0. Re-running the cancelled runs
    turned each one red on `tag 'v0.25.0' already exists`."""
    origin, work, github_sha = _checkout(tmp_path)
    other = _clone(origin, tmp_path / "other")
    _commit(other, "feat: the merge whose run released this one")
    released = _commit(other, "bump: version 0.0.0 → 9.9.9 [skip ci]")
    _git("tag", "-a", "v9.9.9", "-m", "9.9.9", cwd=other)
    _git("push", "-q", "--follow-tags", "origin", "HEAD:main", cwd=other)

    proc, output, summary = _run_tip_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert output == "current=false\n"
    assert released[:7] in summary, summary
    assert github_sha[:7] in summary, summary
    assert "since the last tag" in summary, summary
    assert _git("rev-parse", "main", cwd=origin) == released


def test_a_re_run_of_the_run_that_cut_the_tag_stays_failed(workflow, tmp_path):
    """The one run behind main that must not turn green: the run whose own
    bump commit sits right after its commit. Its release is already tagged,
    and a green re-run would change the run's conclusion to `success` -- which
    the tag check reads as "this run published the version", so a tag PyPI
    lacks would be called spent instead of re-run or recovered, and every
    later release would stop at the check."""
    origin, work, github_sha = _checkout(tmp_path)
    other = _clone(origin, tmp_path / "other")
    bump = _commit(other, "bump: version 0.0.0 → 9.9.9 [skip ci]")
    _git("tag", "-a", "v9.9.9", "-m", "9.9.9", cwd=other)
    _commit(other, "feat: a later merge")
    _git("push", "-q", "--follow-tags", "origin", "HEAD:main", cwd=other)
    # The re-run's checkout has every tag (`fetch-depth: 0`).
    _git("fetch", "-q", "--tags", "origin", cwd=work)

    proc, output, summary = _run_tip_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode != 0
    assert "current=" not in output
    assert summary == ""
    assert "v9.9.9" in proc.stderr, proc.stderr
    assert bump[:7] in proc.stderr, proc.stderr
    assert "re-run only" in proc.stderr, proc.stderr


def test_a_rewritten_main_still_goes_on_to_the_bump(workflow, tmp_path):
    """main moved, but not past this run's commit. No later run is known to
    carry it, so this is not the no-op; the run goes on, and the push step
    fails it (`test_a_rewritten_main_is_not_a_run_that_was_outrun`)."""
    origin, work, github_sha = _checkout(tmp_path)
    other = _clone(origin, tmp_path / "other")
    _git("checkout", "-q", "--orphan", "rewritten", cwd=other)
    _commit(other, "chore: a history without this run's commit")
    _git("push", "-q", "--force", "origin", "HEAD:main", cwd=other)

    proc, output, _ = _run_tip_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert output == "current=true\n"


def test_a_manual_run_on_another_branch_is_refused(workflow, tmp_path):
    """`workflow_dispatch` runs on whatever branch the button names, and the
    push step pushes `HEAD:main`: a branch that contains main would
    fast-forward main onto commits no pull request reviewed. `on.push` is
    limited to main; a manual run has to be refused by hand."""
    _, work, github_sha = _checkout(tmp_path)

    proc, output, summary = _run_tip_step(
        workflow, work, github_sha, tmp_path, ref="refs/heads/feature"
    )

    assert proc.returncode != 0
    assert output == ""
    assert summary == ""
    assert "refs/heads/feature" in proc.stderr, proc.stderr
    assert "main only" in proc.stderr, proc.stderr


def test_a_fetch_that_fails_is_not_read_as_a_no_op(workflow, tmp_path):
    """Whether main moved is unknown, so neither answer is written: a green
    no-op here could leave a releasable commit unreleased in silence."""
    _, work, github_sha = _checkout(tmp_path)
    _git("remote", "set-url", "origin", str(tmp_path / "gone.git"), cwd=work)

    proc, output, summary = _run_tip_step(workflow, work, github_sha, tmp_path)

    assert proc.returncode != 0
    assert "current=" not in output
    assert "unknown" in proc.stderr, proc.stderr
    assert summary == ""


PUSHED = "steps.push.outputs.pushed == 'true'"
RECOVERED = "steps.recover.outputs.recovered == 'true'"
EITHER_PATH = f"{PUSHED} || {RECOVERED}"


def test_everything_after_the_push_waits_for_it_to_land(workflow):
    """A run that main outran has cut a version it never pushed, so `bumped`
    alone would send the ls-remote check, the build and `publish` after a
    tag that is not on origin. Every later step, and the job output the
    `publish` job reads, gate on the push step instead -- or, for the three
    uploads and the outputs, on the recover step, which is the one other
    way an artifact comes to exist and gates on nothing the bump wrote."""
    steps = workflow["jobs"]["release"]["steps"]
    push_index = next(i for i, step in enumerate(steps) if step.get("id") == "push")
    assert steps[push_index]["if"] == "steps.bump.outputs.bumped == 'true'"
    for step in steps[push_index + 1 :]:
        if step.get("id") == "recover":
            assert step["if"] == "steps.bump.outputs.bumped == 'false'"
        elif "upload-artifact" in str(step.get("uses", "")):
            assert step["if"] == EITHER_PATH, step.get("name")
        else:
            assert step.get("if") == PUSHED, step.get("name")
    outputs = workflow["jobs"]["release"]["outputs"]
    assert outputs["bumped"] == "${{ " + EITHER_PATH + " }}"
    assert outputs["version"] == (
        "${{ steps.bump.outputs.version || steps.recover.outputs.version }}"
    )
    assert "steps.bump.outputs.bumped" not in outputs["bumped"], (
        "a version cut and never pushed must never reach publish"
    )


def test_the_push_step_reads_the_version_from_its_environment(workflow):
    """The step's script is run verbatim by the tests above. A `${{ }}`
    expression inside it would be a bash syntax error there and, more to the
    point, an injection surface on the runner; the version arrives as an
    environment variable instead."""
    step = _release_step(workflow, "git push")
    assert "${{" not in step["run"], step["run"]
    assert step["env"]["VERSION"] == "${{ steps.bump.outputs.version }}"


# --- The tag on origin must be this run's tag ---------------------------------
#
# `--follow-tags` sends only the annotated tags origin does not already have.
# A same-named tag already on origin -- pushed by hand, pointing at another
# commit -- is therefore not rejected: it is silently skipped, and the bump
# commit lands on main under a tag that means something else. A check that
# only asks whether the tag exists passes that. The step must compare what
# the tag points at on origin with the commit it just pushed.


def _run_verify_tag_step(
    workflow: dict, work: Path, tmp_path: Path
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the ls-remote step's script the way `shell: bash` does."""
    step = _release_step(workflow, "ls-remote")
    assert step.get("shell") == "bash"
    summary = tmp_path / "verify_step_summary"
    summary.touch()
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary), "VERSION": "9.9.9"}
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc, summary.read_text(encoding="utf-8")


def test_a_tag_already_on_origin_pointing_elsewhere_fails_the_check(workflow, tmp_path):
    origin, work, github_sha = _runner(tmp_path)
    other = _clone(origin, tmp_path / "other")
    _git("tag", "-a", "v9.9.9", "-m", "pushed by hand", cwd=other)
    _git("push", "-q", "origin", "v9.9.9", cwd=other)

    proc, output, _ = _run_push_step(workflow, work, github_sha, tmp_path)

    # What git does, pinned so the reason for the check below stays visible:
    # the push is not rejected, the commit lands, the tag is left as it was.
    assert proc.returncode == 0, proc.stderr
    assert "pushed=true" in output
    assert _git("rev-parse", "main", cwd=origin) == _git("rev-parse", "HEAD", cwd=work)
    assert _git("rev-parse", "v9.9.9^{commit}", cwd=origin) == github_sha

    proc, summary = _run_verify_tag_step(workflow, work, tmp_path)

    assert proc.returncode != 0
    assert "points at" in proc.stderr, proc.stderr
    assert github_sha[:7] in proc.stderr, proc.stderr
    assert "Confirmed" not in summary


def test_a_landed_tag_that_points_at_the_bump_commit_passes_the_check(workflow, tmp_path):
    origin, work, github_sha = _runner(tmp_path)
    proc, _, _ = _run_push_step(workflow, work, github_sha, tmp_path)
    assert proc.returncode == 0, proc.stderr

    proc, summary = _run_verify_tag_step(workflow, work, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "Confirmed tag v9.9.9" in summary


def test_a_tag_that_never_reached_origin_fails_the_check(workflow, tmp_path):
    """The case the step was written for: the bump commit is on main and the
    tag stayed on the runner (a lightweight tag `--follow-tags` never sends)."""
    origin, work, _ = _runner(tmp_path)
    _git("push", "-q", "origin", "HEAD:main", cwd=work)
    assert _origin_tags(origin) == ""

    proc, summary = _run_verify_tag_step(workflow, work, tmp_path)

    assert proc.returncode != 0
    assert "is not on origin" in proc.stderr, proc.stderr
    assert "Confirmed" not in summary


def test_a_lookup_that_fails_is_reported_as_unknown_not_as_missing(workflow, tmp_path):
    """A failed `git ls-remote` prints nothing, exactly like a missing tag.
    The two must not be confused: one sends a maintainer to re-tag by hand
    over a network fault."""
    _, work, github_sha = _runner(tmp_path)
    _git("remote", "set-url", "origin", str(tmp_path / "gone.git"), cwd=work)

    proc, _ = _run_verify_tag_step(workflow, work, tmp_path)

    assert proc.returncode != 0
    assert "unknown" in proc.stderr, proc.stderr
    assert "is not on origin" not in proc.stderr


# --- A tagged version that never reached PyPI ---------------------------------
#
# `publish` waits for a reviewer inside the `pypi` environment. If that approval
# never comes, the run ends with the tag on main and nothing on PyPI, and every
# later run reads only the commits since that tag (issue #103, v0.21.0). A
# release cut after it would leave a gap users can see: 0.21.0 missing between
# 0.20.0 and 0.22.0. So the release job checks every release tag against PyPI
# before the bump, on every run, and a missing tag fails the job before a
# version is cut. It runs before this run's own tag exists, and the concurrency
# group keeps any other run's release from being in flight, so it never fails
# over a release that is still on its way to PyPI.


def _step_index(steps: list[dict], **match: str) -> int:
    key, value = next(iter(match.items()))
    return next(i for i, step in enumerate(steps) if step.get(key) == value)


def test_every_run_checks_every_tag_against_pypi_before_the_bump(workflow):
    steps = workflow["jobs"]["release"]["steps"]
    check = next(step for step in steps if "check_tags_published" in str(step.get("run", "")))
    assert check.get("id") == "check", "the recover step reads this step's plan file"
    assert check["if"] == "steps.tip.outputs.current == 'true'"
    assert _step_index(steps, id="tip") < steps.index(check) < _step_index(steps, id="bump")
    assert check["run"].strip() == "python3 scripts/check_tags_published.py"
    assert "${{" not in check["run"]
    # The expression that resolves to the token on the runner, not a token (S105).
    assert check["env"]["GITHUB_TOKEN"] == "${{ github.token }}"  # noqa: S105
    assert (REPO_ROOT / "scripts" / "check_tags_published.py").is_file()


# --- Recovering a stranded tag ------------------------------------------------
#
# When the check above finds a tag whose run can no longer be re-run -- or that
# no release run pushed -- it hands that one tag to the recover step through a
# plan file instead of failing. The step verifies the tag's commit with the
# same commands `verify` runs, builds from it, and sets the outputs `publish`
# reads. The design and the reasons for verifying inside `release` rather
# than in a job ahead of `verify` are in
# docs/superpowers/specs/2026-10-20-skill-lens-stranded-tag-recovery-design.md.


def _recover_step(workflow: dict) -> dict:
    steps = workflow["jobs"]["release"]["steps"]
    return next(step for step in steps if step.get("id") == "recover")


def _script_lines(step: dict) -> list[str]:
    return [line.strip() for line in str(step["run"]).splitlines() if line.strip()]


def test_a_stranded_tag_is_recovered_on_the_path_that_found_it(workflow):
    """The bump reads the plan file the check wrote and stands aside for it;
    the recover step runs whenever the bump released nothing, reads the same
    file, and sits right before the uploads, which is where its artifacts are
    read from. Nothing reaches it through `${{ }}`: the tests below run its
    script verbatim."""
    steps = workflow["jobs"]["release"]["steps"]
    check = steps[_step_index(steps, id="check")]
    bump = steps[_step_index(steps, id="bump")]
    recover = _recover_step(workflow)
    assert recover["if"] == "steps.bump.outputs.bumped == 'false'"
    assert recover["shell"] == "bash"
    assert "${{" not in recover["run"]
    plan = check["env"]["RECOVERY_PLAN"]
    assert plan and "/" not in plan, "the plan lives in the workspace, under a bare name"
    assert recover["env"]["RECOVERY_PLAN"] == bump["env"]["RECOVERY_PLAN"] == plan
    uploads = [i for i, step in enumerate(steps) if "upload-artifact" in str(step.get("uses", ""))]
    assert steps.index(check) < steps.index(recover) < min(uploads)


def test_the_recover_step_verifies_the_tag_exactly_as_verify_does(workflow):
    """Every `run:` command of `verify`, spelled byte for byte, in `verify`'s
    order, and all of them before the build. Two spellings of the
    verification would drift the first time one of them changed -- the same
    reason the audit command is pinned across its copies."""
    verify_commands = [
        str(step["run"]).strip() for step in workflow["jobs"]["verify"]["steps"] if "run" in step
    ]
    assert len(verify_commands) >= 5, verify_commands
    lines = _script_lines(_recover_step(workflow))
    positions = []
    for command in verify_commands:
        assert command in lines, f"the recover step does not run {command!r} as verify spells it"
        positions.append(lines.index(command))
    assert positions == sorted(positions), "the recover step runs verify's commands out of order"
    build_at = next(i for i, line in enumerate(lines) if line.startswith("uv build"))
    assert max(positions) < build_at, "the recover step builds before it has verified"


def test_the_recover_step_builds_and_exports_as_the_bump_path_does(workflow):
    """Same audited backend, same SBOM shape, same notes command -- and
    every output written outside the worktree, where the shared uploads read."""
    lines = _script_lines(_recover_step(workflow))
    script = "\n".join(lines)
    assert "uv export --frozen --only-group build --no-hashes -o build-constraints.txt" in lines
    assert "uv build --build-constraint build-constraints.txt --out-dir ../dist" in lines
    sbom = next(line for line in lines if "cyclonedx" in line)
    assert "--format cyclonedx1.5" in sbom and "--frozen" in sbom
    assert "--all-extras" in sbom and "--no-default-groups" in sbom
    assert re.search(r'-o "\.\./sbom/skill-lens-\$\{?version\}?\.cdx\.json"', script), script
    notes = next(line for line in lines if "cz changelog" in line)
    assert "--dry-run" in notes and notes.endswith("> ../notes.md"), notes
    assert 'uv run cz changelog "$version" --dry-run' in notes


def _stranded(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """The release job's checkout on a later merge that releases nothing:
    origin holds a bump commit and its annotated tag v9.9.9, nothing was
    ever published, and main has moved on by one docs commit."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = _clone(origin, tmp_path / "seed")
    _commit(seed, "feat: the release that stranded")
    tag_commit = _commit(seed, "bump: version 0.0.0 → 9.9.9 [skip ci]")
    _git("tag", "-a", "v9.9.9", "-m", "9.9.9", cwd=seed)
    _git("push", "-q", "--follow-tags", "origin", "HEAD:main", cwd=seed)
    _commit(seed, "docs: a later merge that releases nothing")
    _git("push", "-q", "origin", "HEAD:main", cwd=seed)
    work = _clone(origin, tmp_path / "work")
    return origin, work, _git("rev-parse", "HEAD", cwd=work), tag_commit


FAKE_UV = r"""#!/bin/sh
# A stand-in for uv: logs every invocation and writes the files the recover
# step expects, so the step's own logic can be run without a network, a
# virtualenv or the real test suite.
printf '%s\n' "$*" >> "$FAKE_UV_LOG"
if [ "${FAKE_UV_FAIL:-}" = "$1 $2" ]; then
  echo "fake uv: $1 $2 failed on purpose" >&2
  exit 1
fi
case "$*" in
  "run cz version --project") printf '%s\n' "${FAKE_UV_VERSION:-9.9.9}"; exit 0 ;;
  "run cz changelog "*) printf '## v%s\n' "$4"; exit 0 ;;
esac
target=""
while [ $# -gt 0 ]; do
  case "$1" in -o|--out-dir) target="$2"; shift ;; esac
  shift
done
case "$target" in
  "") ;;
  *.txt|*.json) mkdir -p "$(dirname "$target")"; echo fake > "$target" ;;
  *) mkdir -p "$target"; echo fake > "$target/skill_lens-9.9.9-py3-none-any.whl"
     echo fake > "$target/skill_lens-9.9.9.tar.gz" ;;
esac
exit 0
"""


def _run_recover_step(
    workflow: dict,
    work: Path,
    github_sha: str,
    tmp_path: Path,
    plan: str | None,
    **fake_env: str,
) -> tuple[subprocess.CompletedProcess, str, str, list[str]]:
    """Run the recover step's script the way `shell: bash` does, with a fake
    `uv` first on PATH. Returns the process, GITHUB_OUTPUT, the step summary
    and every `uv` invocation in order."""
    step = _recover_step(workflow)
    plan_path = work / step["env"]["RECOVERY_PLAN"]
    if plan is not None:
        plan_path.write_text(plan, encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    uv = fake_bin / "uv"
    uv.write_text(FAKE_UV, encoding="utf-8")
    uv.chmod(0o755)
    log = tmp_path / "uv.log"
    log.touch()
    output = tmp_path / "recover_output"
    summary = tmp_path / "recover_summary"
    output.touch()
    summary.touch()
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_UV_LOG": str(log),
        "GITHUB_SHA": github_sha,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "RECOVERY_PLAN": step["env"]["RECOVERY_PLAN"],
        **fake_env,
    }
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
    return proc, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8"), calls


def _plan(tag_commit: str, tag: str = "v9.9.9", version: str = "9.9.9") -> str:
    return f"tag={tag}\nversion={version}\ncommit={tag_commit}\n"


def test_no_plan_is_a_no_op_that_runs_nothing(workflow, tmp_path):
    _, work, github_sha, _ = _stranded(tmp_path)

    proc, output, summary, calls = _run_recover_step(workflow, work, github_sha, tmp_path, None)

    assert proc.returncode == 0, proc.stderr
    assert output == ""
    assert calls == [], "uv was run with no tag to recover"
    # The log says so; the summary does not, since the check's own line
    # already covers the ordinary case and this is the ordinary case.
    assert "No stranded tag to recover" in proc.stdout
    assert summary == ""
    assert not (work / "recover").exists()


def test_a_planned_tag_is_verified_then_built_from_its_own_commit(workflow, tmp_path):
    origin, work, github_sha, tag_commit = _stranded(tmp_path)
    verify_commands = [
        str(step["run"]).strip() for step in workflow["jobs"]["verify"]["steps"] if "run" in step
    ]

    proc, output, summary, calls = _run_recover_step(
        workflow, work, github_sha, tmp_path, _plan(tag_commit)
    )

    assert proc.returncode == 0, proc.stderr
    assert "recovered=true" in output and "version=9.9.9" in output, output
    # The worktree is the tag's commit, not main's tip.
    assert _git("rev-parse", "HEAD", cwd=work / "recover") == tag_commit
    # verify's commands, in verify's order, all before anything builds.
    assert ["uv " + call for call in calls[: len(verify_commands)]] == verify_commands, calls
    build_at = next(i for i, call in enumerate(calls) if call.startswith("build "))
    assert build_at >= len(verify_commands)
    assert any(call.startswith("run cz version --project") for call in calls)
    # The artifacts, where the uploads read them.
    assert sorted(p.name for p in (work / "dist").iterdir()) == [
        "skill_lens-9.9.9-py3-none-any.whl",
        "skill_lens-9.9.9.tar.gz",
    ]
    assert (work / "sbom" / "skill-lens-9.9.9.cdx.json").is_file()
    assert (work / "notes.md").read_text(encoding="utf-8").startswith("## v9.9.9")
    assert "v9.9.9" in summary and tag_commit[:7] in summary
    # The tag was read, never written.
    assert _git("rev-parse", "v9.9.9^{commit}", cwd=origin) == tag_commit
    assert _git("rev-parse", "main", cwd=origin) == github_sha


def _assert_nothing_recovered(proc, output, work: Path, calls: list[str]) -> None:
    assert proc.returncode != 0
    assert "recovered=" not in output, output
    assert not any(call.startswith("build ") for call in calls), calls
    assert not (work / "dist").exists()
    assert not (work / "sbom").exists()


def test_a_plan_whose_commit_the_tag_no_longer_points_at_is_refused(workflow, tmp_path):
    """The plan carries the commit the check saw on origin. A tag that points
    elsewhere by the time the step runs -- in the clone, or on origin -- is
    not the tag the check stood behind, and nothing is built from it."""
    _, work, github_sha, tag_commit = _stranded(tmp_path)

    proc, output, _, calls = _run_recover_step(
        workflow, work, github_sha, tmp_path, _plan(github_sha)
    )

    _assert_nothing_recovered(proc, output, work, calls)
    assert calls == [], "uv was run before the tag was checked"
    assert "points at" in proc.stderr and tag_commit[:7] in proc.stderr, proc.stderr


def test_a_tag_moved_on_origin_after_the_clone_is_refused(workflow, tmp_path):
    origin, work, github_sha, tag_commit = _stranded(tmp_path)
    other = _clone(origin, tmp_path / "other")
    moved = _commit(other, "chore: where the tag was moved to")
    _git("tag", "-f", "-a", "v9.9.9", "-m", "moved", cwd=other)
    _git("push", "-q", "--force", "origin", "refs/tags/v9.9.9", cwd=other)
    assert _git("rev-parse", "v9.9.9^{commit}", cwd=origin) == moved

    proc, output, _, calls = _run_recover_step(
        workflow, work, github_sha, tmp_path, _plan(tag_commit)
    )

    _assert_nothing_recovered(proc, output, work, calls)
    assert calls == []
    # The clone's tag is left as it was, too: the step only reads tags.
    assert _git("rev-parse", "v9.9.9^{commit}", cwd=work) == tag_commit


def test_a_tag_whose_commit_is_not_on_main_is_refused(workflow, tmp_path):
    """A tag in the release's namespace pointing at a branch, or at a commit
    main was rewritten away from: code main does not carry is never
    published, whatever version it spells."""
    origin, work, github_sha, _ = _stranded(tmp_path)
    other = _clone(origin, tmp_path / "other")
    _git("checkout", "-q", "-b", "side", cwd=other)
    side = _commit(other, "feat: on a branch, not on main")
    _git("tag", "-f", "-a", "v9.9.9", "-m", "on a branch", cwd=other)
    _git("push", "-q", "--force", "origin", "refs/tags/v9.9.9", cwd=other)
    _git("fetch", "-q", "--force", "origin", "refs/tags/v9.9.9:refs/tags/v9.9.9", cwd=work)

    proc, output, _, calls = _run_recover_step(workflow, work, github_sha, tmp_path, _plan(side))

    _assert_nothing_recovered(proc, output, work, calls)
    assert calls == []
    assert "not on main" in proc.stderr, proc.stderr


def test_a_tree_that_spells_another_version_is_refused(workflow, tmp_path):
    """The version about to be published must be the one the tree spells,
    or a tag at the wrong commit would build -- and upload -- a version
    that is not the stranded one, possibly one PyPI already lists."""
    _, work, github_sha, tag_commit = _stranded(tmp_path)

    proc, output, _, calls = _run_recover_step(
        workflow, work, github_sha, tmp_path, _plan(tag_commit), FAKE_UV_VERSION="1.2.3"
    )

    _assert_nothing_recovered(proc, output, work, calls)
    assert "1.2.3" in proc.stderr and "9.9.9" in proc.stderr, proc.stderr
    assert not (work / "notes.md").exists()


def test_a_red_suite_on_the_tag_builds_nothing(workflow, tmp_path):
    """Nothing publishes that has not been verified in the same run: the
    tag's own run passed these tests once, but this run has to pass them
    again, on this tree, before a byte of it is built."""
    _, work, github_sha, tag_commit = _stranded(tmp_path)

    proc, output, _, calls = _run_recover_step(
        workflow, work, github_sha, tmp_path, _plan(tag_commit), FAKE_UV_FAIL="run pytest"
    )

    _assert_nothing_recovered(proc, output, work, calls)
    assert calls[-1] == "run pytest", calls
    assert not any("cz" in call for call in calls)


def test_a_vulnerable_lockfile_on_the_tag_builds_nothing(workflow, tmp_path):
    """An advisory published since the tag was cut fails the audit of the
    tag's lockfile, and the version stays unpublished -- the same rule
    `verify` applies to a fresh release."""
    _, work, github_sha, tag_commit = _stranded(tmp_path)

    proc, output, _, calls = _run_recover_step(
        workflow,
        work,
        github_sha,
        tmp_path,
        _plan(tag_commit),
        FAKE_UV_FAIL="audit --preview-features",
    )

    _assert_nothing_recovered(proc, output, work, calls)
    assert calls[-1].startswith("audit "), calls


def test_a_malformed_plan_is_refused(workflow, tmp_path):
    _, work, github_sha, tag_commit = _stranded(tmp_path)

    proc, output, _, calls = _run_recover_step(
        workflow, work, github_sha, tmp_path, f"tag=v9.9.9\ncommit={tag_commit}\n"
    )

    _assert_nothing_recovered(proc, output, work, calls)
    assert calls == []
    assert "version" in proc.stderr, proc.stderr


def test_the_release_job_may_read_workflow_runs(workflow):
    """The check names the run that should have published a stranded tag.
    Listing runs with the job's token needs `actions: read`; a token without
    it is refused on that endpoint, and an unauthenticated call from a shared
    runner address is rate-limited."""
    assert workflow["jobs"]["release"]["permissions"] == {"contents": "write", "actions": "read"}


# --- The bump: a planned recovery, and a release started by hand ---------------
#
# The tag check runs before the bump, so a stranded tag it hands to recovery is
# known before a version is cut. One run publishes one version, so the bump
# stands aside: it writes `bumped=false`, the recover step rebuilds the old tag,
# and this run's own commits wait for the next run, which finds every tag on
# PyPI and releases them with no gap. A run started by hand passes the
# increment it was given to `cz bump`; on a push it is empty and the commits
# decide, as before. These tests run the bump step's
# script verbatim, with the fake `uv` above standing in for Commitizen.


def _bump_step(workflow: dict) -> dict:
    steps = workflow["jobs"]["release"]["steps"]
    return steps[_step_index(steps, id="bump")]


def _run_bump_step(
    workflow: dict, tmp_path: Path, plan: str | None = None, **inputs: str
) -> tuple[subprocess.CompletedProcess, str, str, list[str]]:
    """Run the bump step's script the way `shell: bash` does. `inputs` stands
    in for the environment the step maps from `inputs.*`; on a push it is an
    empty string, which is what GitHub sets."""
    step = _bump_step(workflow)
    assert step.get("shell") == "bash"
    work = tmp_path / "work"
    work.mkdir()
    if plan is not None:
        (work / step["env"]["RECOVERY_PLAN"]).write_text(plan, encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text(FAKE_UV, encoding="utf-8")
    uv.chmod(0o755)
    log = tmp_path / "uv.log"
    log.touch()
    output = tmp_path / "bump_output"
    summary = tmp_path / "bump_summary"
    output.touch()
    summary.touch()
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_UV_LOG": str(log),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "RECOVERY_PLAN": step["env"]["RECOVERY_PLAN"],
        "INCREMENT": "",
        **inputs,
    }
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
    return proc, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8"), calls


BUMP = "run cz bump --yes --check-consistency"


def test_a_push_lets_the_commits_choose_the_version(workflow, tmp_path):
    proc, output, _, calls = _run_bump_step(workflow, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert calls[0] == BUMP
    assert "bumped=true" in output
    assert "version=9.9.9" in output


def test_a_planned_recovery_cuts_no_version(workflow, tmp_path):
    """Two versions in one run would reach `publish` as one upload and
    `github-release` as one tag. The stranded one goes first; the gap closes
    before anything newer is cut."""
    proc, output, summary, calls = _run_bump_step(
        workflow, tmp_path, plan="tag=v9.9.8\nversion=9.9.8\ncommit=abc\n"
    )

    assert proc.returncode == 0, proc.stderr
    assert calls == []
    assert output == "bumped=false\n"
    assert "v9.9.8" in summary, summary
    assert "next run" in summary, summary


def test_a_manual_run_passes_its_increment_to_the_bump(workflow, tmp_path):
    proc, output, _, calls = _run_bump_step(workflow, tmp_path, INCREMENT="PATCH")

    assert proc.returncode == 0, proc.stderr
    assert calls[0] == f"{BUMP} --increment PATCH"
    assert "bumped=true" in output


def test_a_manual_run_on_the_default_bumps_as_a_push_does(workflow, tmp_path):
    proc, _, _, calls = _run_bump_step(workflow, tmp_path, INCREMENT="auto")

    assert proc.returncode == 0, proc.stderr
    assert calls[0] == BUMP


@pytest.mark.parametrize("value", ["MAJOR", "PATCH --yes", "MINOR --prerelease rc", "patch"])
def test_an_increment_the_form_does_not_offer_is_refused(workflow, tmp_path, value):
    """GitHub checks a `choice` input against its options, but the step does
    not lean on that: a value it does not know never reaches the command
    line, and nothing is bumped."""
    proc, output, _, calls = _run_bump_step(workflow, tmp_path, INCREMENT=value)

    assert proc.returncode != 0
    assert calls == []
    assert "bumped=" not in output
    assert value in proc.stderr, proc.stderr


def test_a_release_can_be_started_by_hand(workflow):
    """MAJOR is not offered: `major_version_zero = true` keeps the project in
    0.x on purpose, and leaving it is a change to that setting in a pull
    request of its own, not a button. Nor is a pre-release: Commitizen
    finishes one on the next bump of any kind, so the next Dependabot merge
    would publish the final version before anyone tried the rc."""
    triggers = workflow["on"]
    assert triggers["push"] == {"branches": ["main"]}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"increment"}
    assert inputs["increment"]["type"] == "choice"
    assert inputs["increment"]["options"] == ["auto", "PATCH", "MINOR"]
    assert inputs["increment"]["default"] == "auto"


def test_the_increment_reaches_the_bump_through_its_environment(workflow):
    """An input in a `${{ }}` expression inside the script would be pasted
    into it before bash parses it: an injection surface. The step maps it to
    a variable instead and checks its value itself."""
    step = _bump_step(workflow)
    assert "${{" not in step["run"], step["run"]
    assert step["env"]["INCREMENT"] == "${{ inputs.increment }}"
    assert "inputs." not in str(workflow["jobs"]["release"]["steps"]).replace(
        "${{ inputs.increment }}", ""
    )
