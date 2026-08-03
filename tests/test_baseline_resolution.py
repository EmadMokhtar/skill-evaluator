"""Resolving a skill's previous version from real git history, offline."""

from __future__ import annotations

import subprocess
from pathlib import Path

from skill_eval.models import Skill
from skill_eval.skills.baseline import (
    HISTORY_LIMIT,
    BaselineUnavailable,
    resolve_previous,
)
from skill_eval.skills.loader import parse_skill_file


def _repo(tmp_path: Path) -> Path:
    """A real git repo with committer identity set, so commits succeed in CI."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    return tmp_path


def _skill_md(version: str, body: str) -> str:
    head = f"---\nname: pdf\ndescription: Handle {body}\n"
    if version:
        head += f"version: {version}\n"
    return head + f"---\n\n{body}\n"


def _commit(repo: Path, text: str, message: str) -> None:
    (repo / "SKILL.md").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "SKILL.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def test_the_previous_version_is_the_newest_commit_with_a_different_version(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "old instructions"), "feat: v1")
    _commit(repo, _skill_md("1.1.0", "new instructions"), "feat: v2")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.version == "1.0.0"
    assert previous.instructions == "old instructions"
    assert previous.variant == "baseline"


def test_a_same_version_commit_is_skipped_in_favour_of_a_real_predecessor(tmp_path):
    # The version identifies the version. A commit that edited the body without
    # bumping it is still *this* version, so `previous` must look further back.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "oldest"), "feat: v1")
    _commit(repo, _skill_md("1.1.0", "middle"), "feat: v2")
    _commit(repo, _skill_md("1.1.0", "tweaked"), "docs: reword")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.version == "1.0.0"


def test_an_unversioned_skill_falls_back_to_the_newest_differing_content(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("", "old instructions"), "feat: v1")
    _commit(repo, _skill_md("", "new instructions"), "feat: v2")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.instructions == "old instructions"


def test_uncommitted_edits_are_compared_against_the_committed_copy(tmp_path):
    # The working copy is what runs as the candidate, so it is what the search
    # compares against -- not HEAD against HEAD~1.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("", "committed instructions"), "feat: v1")
    (repo / "SKILL.md").write_text(_skill_md("", "uncommitted edit"), encoding="utf-8")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.instructions == "committed instructions"


def test_the_candidate_directory_is_kept_so_nothing_downstream_breaks(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "old"), "feat: v1")
    _commit(repo, _skill_md("1.1.0", "new"), "feat: v2")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.path == repo


def test_an_unchanged_skill_has_no_previous_version(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "only ever this"), "feat: v1")

    result = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert str(HISTORY_LIMIT) in result.reason


def test_an_untracked_skill_reports_why(tmp_path):
    repo = _repo(tmp_path)
    (repo / "SKILL.md").write_text(_skill_md("1.0.0", "never committed"), encoding="utf-8")

    result = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "not tracked" in result.reason


def test_a_directory_outside_a_repository_reports_why(tmp_path):
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "no repo here"), encoding="utf-8")

    result = resolve_previous(parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "not inside a git repository" in result.reason


def test_a_missing_git_binary_reports_why_and_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_eval.skills.baseline.shutil.which", lambda _: None)
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "x"), encoding="utf-8")

    result = resolve_previous(parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "git is not installed" in result.reason


def test_the_skill_name_travels_with_the_reason(tmp_path):
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "x"), encoding="utf-8")

    result = resolve_previous(parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert result.skill_name == "pdf"


def test_a_malformed_historical_version_is_skipped_not_fatal(tmp_path):
    # An old commit with broken frontmatter is not an authoring error about the
    # *current* skill, so it must not abort the run.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "good old"), "feat: v1")
    _commit(repo, "---\nname: [unclosed\n---\n\nbroken\n", "feat: broken")
    _commit(repo, _skill_md("1.2.0", "current"), "feat: v3")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.version == "1.0.0"
