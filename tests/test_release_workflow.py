"""Assert the structure of release.yml that carries the design decisions.

A workflow cannot be executed by the test suite, so what is testable is its
shape. Each assertion below corresponds to a decision that is invisible once
made and expensive when silently removed.

Task 5 adds the `publish` job and its own tests for it; this file covers the
`verify`, `release`, and `publish` jobs.
"""

from __future__ import annotations

import re
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
    assert verify_step["if"] == "steps.bump.outputs.bumped == 'true'"


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
    expected = 'tag="' + prefix + '${{ steps.bump.outputs.version }}"'
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
    assert step["if"] == "steps.bump.outputs.bumped == 'true'"


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
    assert notes["if"] == "steps.bump.outputs.bumped == 'true'"
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
