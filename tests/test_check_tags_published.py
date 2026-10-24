"""Unit tests for scripts/check_tags_published.py.

The script is not an importable package, so it is loaded from its path, the
way tests/test_check_docs_updated.py loads its script. The pure rules are
tested directly; the network and git calls are replaced on the module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_tags_published.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_tags_published", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: a dataclass under `from __future__ import
    # annotations` resolves its field types through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()

V = check.TagFormat("v", "")


def T(name: str, version: str, commit: str = "0" * 40):
    return check.ReleaseTag(name, version, commit)


# --- pyproject -----------------------------------------------------------------


def test_tag_format_derives_the_version_from_the_prefix_and_suffix():
    fmt = check.read_tag_format('[tool.commitizen]\ntag_format = "v$version"\n')
    assert fmt == check.TagFormat("v", "")
    assert fmt.version("v0.21.0") == "0.21.0"
    assert fmt.version("v0.22.0rc1") == "0.22.0rc1"
    assert fmt.version("0.21.0") is None, "no prefix"
    assert fmt.version("v0") is None, "not a version"
    assert fmt.version("v1.0.0-docs") is None, "a suffix PEP 440 does not know"
    assert fmt.version("v1.0.0foo") is None
    assert fmt.version("v0.22.0a0") == "0.22.0a0"
    assert fmt.version("v0.22.0.post1") == "0.22.0.post1"
    assert fmt.version("v0.22.0.dev3") == "0.22.0.dev3"
    assert check.TagFormat("rel-", "-final").version("rel-1.2.3-final") == "1.2.3"
    assert check.TagFormat("rel-", "-final").version("rel-1.2.3") is None, "no suffix"


def test_a_tag_format_without_the_version_placeholder_is_unavailable():
    with pytest.raises(check.CheckUnavailable):
        check.read_tag_format('[tool.commitizen]\ntag_format = "release"\n')


def test_a_pyproject_without_the_keys_is_unavailable():
    with pytest.raises(check.CheckUnavailable):
        check.read_tag_format('[project]\nname = "x"\n')
    with pytest.raises(check.CheckUnavailable):
        check.read_project_name('[tool.commitizen]\ntag_format = "v$version"\n')


def test_the_real_pyproject_parses():
    text = check.PYPROJECT.read_text(encoding="utf-8")
    assert check.read_project_name(text) == "skill-lens"
    assert check.read_tag_format(text).version("v0.21.0") == "0.21.0"


# --- git ls-remote -------------------------------------------------------------


def test_parse_ls_remote_prefers_the_peeled_commit_and_sorts_by_version():
    output = (
        "aaa\trefs/tags/v0.10.0\n"
        "bbb\trefs/tags/v0.10.0^{}\n"
        "ccc\trefs/tags/v0.9.1\n"
        "ddd\trefs/tags/v0\n"
        "eee\trefs/tags/other\n"
        "fff\trefs/heads/main\n"
    )
    tags, ignored = check.parse_ls_remote(output, V)
    assert tags == [T("v0.9.1", "0.9.1", "ccc"), T("v0.10.0", "0.10.0", "bbb")]
    # `other` is outside the prefix and so not a release tag at all; `v0` is
    # under it and not a version, so it is reported rather than dropped.
    assert ignored == ["v0"]


def test_parse_ls_remote_with_no_tags_is_empty():
    assert check.parse_ls_remote("", V) == ([], [])


# --- PyPI ----------------------------------------------------------------------


def test_pypi_versions_separates_releases_with_files_from_empty_ones():
    payload = {"releases": {"0.20.0": [{"filename": "a.whl"}], "0.21.0": [], "0.22.0": [{}]}}
    assert check.pypi_versions(payload) == ({"0.20.0", "0.22.0"}, {"0.21.0"})


def test_pypi_versions_of_a_project_with_no_releases_is_empty():
    assert check.pypi_versions({"releases": {}}) == (set(), set())


def test_pypi_versions_without_the_releases_key_is_unavailable():
    """PyPI documents `releases` as deprecated. The day it goes, every tag
    would read as missing; that must be exit 2, not 24 findings."""
    with pytest.raises(check.CheckUnavailable, match="releases"):
        check.pypi_versions({})
    with pytest.raises(check.CheckUnavailable, match="releases"):
        check.pypi_versions({"releases": None})


# --- spent-tags.txt ------------------------------------------------------------


def test_parse_spent_reads_tags_with_reasons_and_skips_comments():
    text = (
        "# header\n\nv0.3.0  # 2026-01-01: build failed after the tag\n"
        "v0.4.0 # 2026-02-02: pushed by hand\n"
    )
    assert check.parse_spent(text) == {
        "v0.3.0": "2026-01-01: build failed after the tag",
        "v0.4.0": "2026-02-02: pushed by hand",
    }


def test_parse_spent_refuses_an_entry_without_a_reason():
    """The file's header promises a date and a reason for every entry; an
    entry without one has lost the why, and is a malformed exception list."""
    with pytest.raises(check.CheckUnavailable, match="v0.4.0"):
        check.parse_spent("v0.4.0\n")
    with pytest.raises(check.CheckUnavailable, match="v0.4.0"):
        check.parse_spent("v0.4.0  #\n")


def test_the_spent_file_is_well_formed():
    """Whatever the file lists must be a release tag under the project's
    prefix, with a reason. (Today it lists nothing: v0.21.0 is recoverable
    by re-running its run, so it is not spent.)"""
    fmt = check.read_tag_format(check.PYPROJECT.read_text(encoding="utf-8"))
    spent = check.parse_spent(check.SPENT_FILE.read_text(encoding="utf-8"))
    for tag, reason in spent.items():
        assert fmt.version(tag) is not None, f"{tag} is not a release tag"
        assert reason, f"{tag} has no reason"


# --- the verdict ---------------------------------------------------------------


def test_assess_sorts_every_tag_into_one_bucket():
    tags = [
        T("v0.1.0", "0.1.0"),
        T("v0.2.0", "0.2.0"),
        T("v0.3.0", "0.3.0"),
        T("v0.4.0", "0.4.0"),
        T("v0.5.0", "0.5.0"),
    ]
    verdict = check.assess(
        tags,
        with_files={"0.1.0", "0.3.0"},
        without_files={"0.5.0"},
        spent={"v0.2.0": "spent", "v0.3.0": "stale"},
    )
    assert verdict.published == 1
    assert [t.name for t in verdict.accepted] == ["v0.2.0"]
    assert [t.name for t in verdict.stale] == ["v0.3.0"]
    assert [t.name for t in verdict.missing] == ["v0.4.0"]
    assert [t.name for t in verdict.empty] == ["v0.5.0"]


def test_assess_with_everything_published_has_no_findings():
    verdict = check.assess([T("v1.0.0", "1.0.0")], {"1.0.0"}, set(), {})
    assert verdict == check.Verdict([], [], [], [], 1)


# --- main(): the IO replaced on the module -------------------------------------

import json  # noqa: E402
import urllib.error  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

NOW = datetime(2026, 10, 20, tzinfo=UTC)
REPO = "EmadMokhtar/skill-evaluator"
TAG_COMMIT = "781e89079996c242a3faf3e81a26f704f2b62dea"
PARENT = "1d7f2b2a1fcac6bbad82f3fa5e93dfca7692aa86"
RUN_URL = "https://github.com/EmadMokhtar/skill-evaluator/actions/runs/37301857121"
RELEASE_RUN = {
    "id": 37301857121,
    "path": ".github/workflows/release.yml",
    "html_url": RUN_URL,
    "created_at": "2026-10-05T11:16:14Z",
    "conclusion": "failure",
}
CI_RUN = {
    "id": 37301857164,
    "path": ".github/workflows/ci.yml",
    "html_url": "https://github.com/EmadMokhtar/skill-evaluator/actions/runs/37301857164",
    "created_at": "2026-10-05T11:16:14Z",
    "conclusion": "success",
}


def _ls_remote(*names: str) -> str:
    lines = []
    for index, name in enumerate(names):
        commit = TAG_COMMIT if name == "v0.21.0" else f"{index:040x}"
        lines.append(f"{'f' * 40}\trefs/tags/{name}")
        lines.append(f"{commit}\trefs/tags/{name}^{{}}")
    return "\n".join(lines) + "\n"


def _payload(*versions: str, empty: tuple[str, ...] = ()) -> dict:
    releases = {version: [{"filename": f"skill_lens-{version}.whl"}] for version in versions}
    releases.update({version: [] for version in empty})
    return {"releases": releases}


def _github(runs: list[dict] | None = None, parent: str = PARENT):
    def fetch(path: str):
        if "/commits/" in path:
            return {"parents": [{"sha": parent}]}
        if "/actions/runs" in path:
            assert f"head_sha={parent}" in path, path
            return {"workflow_runs": [] if runs is None else runs}
        raise AssertionError(f"unexpected GitHub call {path!r}")

    return fetch


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Every tag published, no spent entries, the lookup finds the run."""
    spent = tmp_path / "spent-tags.txt"
    spent.write_text("# nothing spent\n", encoding="utf-8")
    monkeypatch.setattr(check, "SPENT_FILE", spent)
    monkeypatch.setattr(check, "ls_remote", lambda: _ls_remote("v0.20.0", "v0.21.0"))
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0", "0.21.0"))
    monkeypatch.setattr(check, "fetch_github", _github([CI_RUN, RELEASE_RUN]))
    monkeypatch.setattr(check, "repository", lambda: REPO)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return spent


def test_every_tag_published_exits_zero_and_writes_the_summary(
    harness, monkeypatch, tmp_path, capsys
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert check.main([], now=NOW) == 0

    verdict = "Every release tag on origin is on PyPI (2 tags)."
    assert verdict in capsys.readouterr().out
    assert verdict in summary.read_text(encoding="utf-8")


def test_a_missing_tag_is_a_finding_that_names_the_run_and_the_window(harness, monkeypatch, capsys):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert "Tag v0.21.0 is on origin, but 0.21.0 is not on PyPI (skill-lens)." in err
    assert f"The run that should have published it: {RUN_URL} (started 2026-10-05, failure)." in err
    assert "Re-run its failed jobs; GitHub allows that until 2026-11-04" in err
    assert "30 days after the run started" in err
    assert "record the tag in scripts/spent-tags.txt with the reason. See docs/releasing.md" in err
    assert "v0.20.0" not in err


def test_a_tag_cut_by_a_manual_run_is_found_by_that_run(harness, monkeypatch, capsys):
    """release.yml also runs on `workflow_dispatch`. A lookup that asked
    GitHub for push runs only would find none for a tag a manual run cut, and
    "no release run pushed it" makes a tag recoverable at once -- rebuilt
    while its own run could still be re-run. The fake filters by `event=` the
    way the API does, so a query that names one event is caught."""
    dispatched = {**RELEASE_RUN, "event": "workflow_dispatch"}

    def fetch(path: str):
        if "/commits/" in path:
            return {"parents": [{"sha": PARENT}]}
        assert f"head_sha={PARENT}" in path, path
        runs = [CI_RUN, dispatched]
        if "event=" in path:
            wanted = path.split("event=", 1)[1].split("&", 1)[0]
            runs = [run for run in runs if run.get("event") == wanted]
        return {"workflow_runs": runs}

    monkeypatch.setattr(check, "fetch_github", fetch)
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert f"The run that should have published it: {RUN_URL}" in err
    assert "Re-run its failed jobs; GitHub allows that until 2026-11-04" in err


LATER = datetime(2026, 12, 1, tzinfo=UTC)  # the 30-day window closed on 2026-11-04
NEXT_MERGE = (
    "The next run of the release workflow on main rebuilds it from its commit and publishes "
    "it, before it releases anything else."
)
THIS_RUN = "This run rebuilds it from its commit and publishes it."


def test_a_run_older_than_thirty_days_says_the_window_closed(harness, monkeypatch, capsys):
    """Without a plan file to hand the tag to (security.yml, a laptop) the
    finding still fails, and says who will recover it."""
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))

    assert check.main([], now=LATER) == 1

    err = capsys.readouterr().err
    assert (
        "Re-running it is no longer possible: GitHub's 30-day window closed on 2026-11-04." in err
    )
    assert NEXT_MERGE in err
    assert "Re-run its failed jobs" not in err
    assert THIS_RUN not in err


def test_a_tag_with_no_release_run_says_the_workflow_did_not_push_it(harness, monkeypatch, capsys):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))
    monkeypatch.setattr(check, "fetch_github", _github([CI_RUN]))

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert (
        f"No release run exists for its commit's parent {PARENT[:7]}: "
        "the release workflow did not push this tag." in err
    )
    assert NEXT_MERGE in err
    assert "Re-run" not in err


# --- RECOVERY_PLAN: handing one tag to the release workflow's recover step ------


@pytest.fixture
def plan(monkeypatch, tmp_path):
    path = tmp_path / "recovery-plan.txt"
    monkeypatch.setenv("RECOVERY_PLAN", str(path))
    return path


def test_with_a_plan_a_tag_past_its_window_is_handed_to_recovery_not_failed(
    harness, plan, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert check.main([], now=LATER) == 0

    assert plan.read_text(encoding="utf-8") == (
        f"tag=v0.21.0\nversion=0.21.0\ncommit={TAG_COMMIT}\n"
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Tag v0.21.0 is on origin, but 0.21.0 is not on PyPI (skill-lens)." in captured.out
    assert "GitHub's 30-day window closed on 2026-11-04" in captured.out
    assert THIS_RUN in captured.out
    assert "spent-tags" not in captured.out, "a tag being recovered is not one to record as spent"
    assert "Every release tag on origin is on PyPI" not in captured.out
    assert THIS_RUN in summary.read_text(encoding="utf-8")


def test_with_a_plan_a_tag_with_no_release_run_is_handed_to_recovery(
    harness, plan, monkeypatch, capsys
):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))
    monkeypatch.setattr(check, "fetch_github", _github([CI_RUN]))

    assert check.main([], now=NOW) == 0

    assert plan.read_text(encoding="utf-8").startswith("tag=v0.21.0\n")
    out = capsys.readouterr().out
    assert "the release workflow did not push this tag." in out
    assert THIS_RUN in out


def test_with_a_plan_a_tag_still_within_its_window_is_reported_for_a_re_run(
    harness, plan, monkeypatch, capsys
):
    """The re-run uploads the artifact that run itself built and verified;
    while that is possible it is the better artifact, and the human has to
    answer the approval either way."""
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))

    assert check.main([], now=NOW) == 1

    assert not plan.exists()
    err = capsys.readouterr().err
    assert "Re-run its failed jobs; GitHub allows that until 2026-11-04" in err
    assert THIS_RUN not in err


def test_with_a_plan_a_tag_whose_parent_run_succeeded_is_spent_not_recovered(
    harness, plan, monkeypatch, capsys
):
    """A tag pushed by hand on top of a released tip, or a release PyPI no
    longer has: the detection design's "spent", and a hand-pushed tag in
    the release's namespace is the thing to stop, not to publish."""
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))
    monkeypatch.setattr(check, "fetch_github", _github([{**RELEASE_RUN, "conclusion": "success"}]))

    assert check.main([], now=LATER) == 1

    assert not plan.exists()
    err = capsys.readouterr().err
    assert "the version is spent" in err
    assert THIS_RUN not in err


def test_with_a_plan_an_unknown_repository_recovers_nothing(harness, plan, monkeypatch, capsys):
    """No lookup, so no positive knowledge that the run cannot be re-run."""
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))
    monkeypatch.setattr(check, "repository", lambda: None)
    monkeypatch.setattr(check, "fetch_github", _github(None))

    assert check.main([], now=LATER) == 1

    assert not plan.exists()
    assert THIS_RUN not in capsys.readouterr().err


def test_with_a_plan_a_failed_lookup_recovers_nothing(harness, plan, monkeypatch, capsys):
    """Whether the run can be re-run is unknown, so the conservative answer
    -- report, do not rebuild -- is the only one the step may act on."""
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))

    def refused(path):
        raise urllib.error.HTTPError(path, 403, "rate limited", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr(check, "fetch_github", refused)

    assert check.main([], now=LATER) == 1

    assert not plan.exists()
    assert THIS_RUN not in capsys.readouterr().err


def test_with_a_plan_a_release_pypi_lists_with_no_files_is_never_rebuilt(
    harness, plan, monkeypatch, capsys
):
    """PyPI refuses a filename it has seen before, deleted or not."""
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0", empty=("0.21.0",)))

    assert check.main([], now=LATER) == 1

    assert not plan.exists()
    err = capsys.readouterr().err
    assert "PyPI lists 0.21.0 with no files" in err
    assert "never rebuilt" in err
    assert THIS_RUN not in err


def test_with_a_plan_the_oldest_recoverable_tag_is_planned_and_the_rest_wait(
    harness, plan, monkeypatch, capsys
):
    monkeypatch.setattr(check, "ls_remote", lambda: _ls_remote("v0.20.0", "v0.21.0", "v0.22.0"))
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))

    assert check.main([], now=LATER) == 0

    assert plan.read_text(encoding="utf-8").startswith("tag=v0.21.0\n")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Tag v0.22.0 is on origin, but 0.22.0 is not on PyPI" in captured.out
    assert (
        "A later run rebuilds it: one tag is recovered per run, and this run recovers v0.21.0."
        in captured.out
    )


def test_with_a_plan_a_recoverable_tag_beside_another_finding_is_not_recovered(
    harness, plan, monkeypatch, capsys
):
    """A red check is never followed by a publish. The plan file is left
    absent -- and a stale one from an earlier step is removed -- so the
    recover step cannot act on a plan the check did not stand behind."""
    plan.write_text("tag=v0.21.0\nversion=0.21.0\ncommit=stale\n", encoding="utf-8")
    monkeypatch.setattr(check, "ls_remote", lambda: _ls_remote("v0.20.0", "v0.21.0", "v0.22.0"))
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0", empty=("0.22.0",)))

    assert check.main([], now=LATER) == 1

    assert not plan.exists()
    err = capsys.readouterr().err
    assert "Tag v0.21.0 is on origin, but 0.21.0 is not on PyPI" in err
    assert "Tag v0.22.0 is on origin, but PyPI lists 0.22.0 with no files" in err
    assert THIS_RUN not in err


def test_with_a_plan_and_nothing_to_find_no_file_is_written(harness, plan):
    plan.write_text("stale\n", encoding="utf-8")

    assert check.main([], now=NOW) == 0

    assert not plan.exists()


def test_a_tag_whose_parent_run_succeeded_is_not_told_to_re_run(harness, monkeypatch, capsys):
    """A tag pushed by hand after a local `cz bump` sits on top of main's
    previous tip, which has a release run of its own -- a successful one.
    That run stranded nothing, and has no failed jobs to re-run."""
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))
    succeeded = {**RELEASE_RUN, "conclusion": "success"}
    monkeypatch.setattr(check, "fetch_github", _github([succeeded]))

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert (
        f"The release run on its commit's parent, {RUN_URL}, succeeded, so it did not strand "
        "this tag: either the tag was pushed outside the workflow, or PyPI no longer has a "
        "version that run published. There are no failed jobs to re-run; the version is spent."
    ) in err
    assert "Re-run its failed jobs" not in err
    assert "The run that should have published it" not in err


def test_no_release_tags_while_pypi_has_releases_is_unknown_not_a_clean_bill(
    harness, monkeypatch, capsys
):
    """`git ls-remote` against the wrong or an empty origin lists nothing and
    exits 0. With PyPI holding releases, that is never a healthy state, and
    "0 tags, all published" would be the vacuous pass this project refuses."""
    monkeypatch.setattr(check, "ls_remote", lambda: "")

    assert check.main([], now=NOW) == 2

    captured = capsys.readouterr()
    assert "Could not check: PyPI lists 2 releases but origin has no release tag" in captured.err
    assert "prefix 'v'" in captured.err
    assert "Every release tag" not in captured.out


def test_a_lookup_that_cannot_run_is_a_note_not_a_crash(harness, monkeypatch, capsys):
    """`_get` refuses a non-https API URL with CheckUnavailable (a GitHub
    Enterprise Server set to http). Inside the lookup that is a failed
    lookup like any other, never an uncaught exception."""
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))

    def refused(path):
        raise check.CheckUnavailable("refusing to open a non-https URL: http://ghes/x")

    monkeypatch.setattr(check, "fetch_github", refused)

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert "Looking up the run that should have published it failed: refusing to open" in err


def test_a_failed_lookup_changes_the_message_not_the_verdict(harness, monkeypatch, capsys):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))

    def refused(path):
        raise urllib.error.HTTPError(path, 403, "rate limited", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr(check, "fetch_github", refused)

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert "Tag v0.21.0 is on origin, but 0.21.0 is not on PyPI" in err
    assert "Looking up the run that should have published it failed: " in err
    assert "403" in err


def test_an_unknown_repository_skips_the_lookup(harness, monkeypatch, capsys):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))
    monkeypatch.setattr(check, "repository", lambda: None)
    monkeypatch.setattr(check, "fetch_github", _github(None))

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert (
        "Looking up the run that should have published it was skipped: "
        "the repository is unknown (set GITHUB_REPOSITORY)." in err
    )


def test_a_release_listed_with_no_files_is_a_finding(harness, monkeypatch, capsys):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0", empty=("0.21.0",)))

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert "Tag v0.21.0 is on origin, but PyPI lists 0.21.0 with no files (skill-lens)." in err
    assert f"The run that should have published it: {RUN_URL}" in err


def test_a_spent_tag_is_accepted(harness, monkeypatch, capsys):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: _payload("0.20.0"))
    harness.write_text("v0.21.0  # 2026-10-20: approval never given\n", encoding="utf-8")

    assert check.main([], now=NOW) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Every release tag on origin is on PyPI (1 tag)." in captured.out
    assert (
        "Spent, accepted by scripts/spent-tags.txt: v0.21.0 (2026-10-20: approval never given)"
        in (captured.out)
    )


def test_a_spent_tag_that_pypi_has_is_stale(harness, capsys):
    harness.write_text("v0.21.0  # recorded too early\n", encoding="utf-8")

    assert check.main([], now=NOW) == 1

    err = capsys.readouterr().err
    assert (
        "Tag v0.21.0 is listed in scripts/spent-tags.txt, but 0.21.0 is on PyPI. "
        "Remove the entry." in err
    )


def test_a_tag_under_the_prefix_that_is_not_a_version_is_listed_not_flagged(
    harness, monkeypatch, capsys
):
    monkeypatch.setattr(
        check, "ls_remote", lambda: _ls_remote("v0.20.0", "v0.21.0", "v0", "v1.0.0-docs")
    )

    assert check.main([], now=NOW) == 0

    out = capsys.readouterr().out
    assert "Ignored under the tag prefix (not a version): v0, v1.0.0-docs" in out
    assert "Every release tag on origin is on PyPI (2 tags)." in out


def test_an_unreadable_pypi_is_unknown_not_missing(harness, monkeypatch, capsys):
    def down(name):
        raise check.CheckUnavailable("PyPI could not be read after 3 attempts: timed out")

    monkeypatch.setattr(check, "fetch_pypi", down)

    assert check.main([], now=NOW) == 2

    err = capsys.readouterr().err
    assert (
        "Could not check: PyPI could not be read after 3 attempts: timed out. Nothing was verified."
        in err
    )
    assert "not on PyPI" not in err


def test_a_pypi_payload_without_releases_is_unknown_not_missing(harness, monkeypatch, capsys):
    monkeypatch.setattr(check, "fetch_pypi", lambda name: {"info": {"name": "skill-lens"}})

    assert check.main([], now=NOW) == 2

    err = capsys.readouterr().err
    assert "Could not check: " in err
    assert "not on PyPI" not in err


def test_a_failed_ls_remote_is_unknown_not_a_clean_bill(harness, monkeypatch, capsys):
    def gone():
        raise check.CheckUnavailable("git ls-remote --tags origin failed (exit 128): fatal: gone")

    monkeypatch.setattr(check, "ls_remote", gone)

    assert check.main([], now=NOW) == 2

    captured = capsys.readouterr()
    assert "Could not check: git ls-remote --tags origin failed" in captured.err
    assert "Every release tag" not in captured.out


def test_a_missing_spent_file_is_unavailable(harness, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(check, "SPENT_FILE", tmp_path / "absent.txt")

    assert check.main([], now=NOW) == 2

    assert "Could not check: " in capsys.readouterr().err


# --- the HTTP helpers ----------------------------------------------------------


def test_fetch_pypi_retries_then_gives_up(monkeypatch):
    calls: list[str] = []
    naps: list[float] = []

    def get(url, headers):
        calls.append(url)
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(check, "_get", get)

    with pytest.raises(check.CheckUnavailable, match="after 3 attempts"):
        check.fetch_pypi("skill-lens", sleep=naps.append)

    assert calls == ["https://pypi.org/pypi/skill-lens/json"] * 3
    assert len(naps) == 2, "no sleep after the last attempt"


def test_fetch_pypi_treats_a_404_as_no_releases(monkeypatch):
    def get(url, headers):
        raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr(check, "_get", get)

    assert check.fetch_pypi("skill-lens", sleep=lambda s: None) == {"releases": {}}


def test_fetch_pypi_returns_the_payload_on_a_later_attempt(monkeypatch):
    attempts = iter([urllib.error.URLError("blip"), json.dumps(_payload("1.0.0")).encode()])

    def get(url, headers):
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(check, "_get", get)

    assert check.fetch_pypi("skill-lens", sleep=lambda s: None) == _payload("1.0.0")


def test_fetch_github_sends_the_token_and_honours_the_api_url(monkeypatch):
    seen: dict = {}

    def get(url, headers):
        seen["url"], seen["headers"] = url, headers
        return b'{"ok": true}'

    monkeypatch.setattr(check, "_get", get)
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_example")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.example.test")

    assert check.fetch_github(f"/repos/{REPO}/commits/{TAG_COMMIT}") == {"ok": True}

    assert seen["url"] == f"https://api.example.test/repos/{REPO}/commits/{TAG_COMMIT}"
    assert seen["headers"]["Authorization"] == "Bearer ghs_example"
    assert seen["headers"]["Accept"] == "application/vnd.github+json"


def test_fetch_github_without_a_token_sends_no_authorization(monkeypatch):
    seen: dict = {}

    def get(url, headers):
        seen["headers"] = headers
        return b"{}"

    monkeypatch.setattr(check, "_get", get)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_API_URL", raising=False)

    check.fetch_github("/x")

    assert "Authorization" not in seen["headers"]


def test_parse_repository_reads_both_remote_spellings():
    assert check.parse_repository("https://github.com/EmadMokhtar/skill-evaluator.git") == REPO
    assert check.parse_repository("https://github.com/EmadMokhtar/skill-evaluator") == REPO
    assert check.parse_repository("git@github.com:EmadMokhtar/skill-evaluator.git") == REPO
    assert check.parse_repository("ssh://git@github.com/EmadMokhtar/skill-evaluator.git") == REPO
    assert check.parse_repository("/tmp/origin.git") is None


def test_repository_prefers_the_actions_variable(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/fork")
    assert check.repository() == "someone/fork"
