"""Resolving a skill's previous version from real git history, offline."""

from __future__ import annotations

import subprocess
from pathlib import Path

from skill_lens.models import Skill
from skill_lens.skills.baseline import (
    HISTORY_LIMIT,
    BaselineUnavailable,
    resolve_previous,
)
from skill_lens.skills.loader import parse_skill_file


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


def _resolve(tmp_path: Path, skill):
    """Resolve with a baseline store under tmp_path, so pytest cleans it up."""
    into = tmp_path / "baselines"
    into.mkdir(exist_ok=True)
    return resolve_previous(skill, into=into)


def test_the_previous_version_is_the_newest_commit_with_a_different_version(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "old instructions"), "feat: v1")
    _commit(repo, _skill_md("1.1.0", "new instructions"), "feat: v2")

    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

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

    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.version == "1.0.0"


def test_an_unversioned_skill_falls_back_to_the_newest_differing_content(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("", "old instructions"), "feat: v1")
    _commit(repo, _skill_md("", "new instructions"), "feat: v2")

    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.instructions == "old instructions"


def test_uncommitted_edits_are_compared_against_the_committed_copy(tmp_path):
    # The working copy is what runs as the candidate, so it is what the search
    # compares against -- not HEAD against HEAD~1.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("", "committed instructions"), "feat: v1")
    (repo / "SKILL.md").write_text(_skill_md("", "uncommitted edit"), encoding="utf-8")

    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.instructions == "committed instructions"


def test_the_candidate_directory_is_kept_so_nothing_downstream_breaks(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "old"), "feat: v1")
    _commit(repo, _skill_md("1.1.0", "new"), "feat: v2")

    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.path == repo


def test_an_unchanged_skill_has_no_previous_version(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "only ever this"), "feat: v1")

    result = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert str(HISTORY_LIMIT) in result.reason


def test_an_untracked_skill_reports_why(tmp_path):
    repo = _repo(tmp_path)
    (repo / "SKILL.md").write_text(_skill_md("1.0.0", "never committed"), encoding="utf-8")

    result = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "not tracked" in result.reason


def test_a_directory_outside_a_repository_reports_why(tmp_path):
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "no repo here"), encoding="utf-8")

    result = _resolve(tmp_path, parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "not inside a git repository" in result.reason


def test_a_missing_git_binary_reports_why_and_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_lens.skills.baseline.shutil.which", lambda _: None)
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "x"), encoding="utf-8")

    result = _resolve(tmp_path, parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "git is not installed" in result.reason


def test_the_skill_name_travels_with_the_reason(tmp_path):
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "x"), encoding="utf-8")

    result = _resolve(tmp_path, parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert result.skill_name == "pdf"


def test_a_malformed_historical_version_is_skipped_not_fatal(tmp_path):
    # An old commit with broken frontmatter is not an authoring error about the
    # *current* skill, so it must not abort the run.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "good old"), "feat: v1")
    _commit(repo, "---\nname: [unclosed\n---\n\nbroken\n", "feat: broken")
    _commit(repo, _skill_md("1.2.0", "current"), "feat: v3")

    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.version == "1.0.0"


def _commit_bundle(repo: Path, files: dict[str, str], message: str) -> None:
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def test_the_previous_bundle_comes_from_the_same_commit_as_its_skill_md(tmp_path):
    repo = _repo(tmp_path)
    _commit_bundle(
        repo,
        {
            "SKILL.md": _skill_md("1.0.0", "old"),
            "scripts/count.py": "print('old')",
            "references/style.md": "old style",
            "pdf.eval.yaml": "cases: []\n",
        },
        "feat: v1",
    )
    _commit_bundle(
        repo,
        {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/count.py": "print('new')"},
        "feat: v2",
    )

    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.bundle_root is not None
    assert previous.bundle_root.is_relative_to((tmp_path / "baselines").resolve())
    assert (previous.bundle_root / "scripts" / "count.py").read_text(
        encoding="utf-8"
    ) == "print('old')"
    assert (previous.bundle_root / "references" / "style.md").read_text(
        encoding="utf-8"
    ) == "old style"
    # Only the three bundle directories are materialised: never the eval file.
    assert not (previous.bundle_root / "pdf.eval.yaml").exists()
    assert not (previous.bundle_root / "SKILL.md").exists()


def test_a_previous_commit_with_no_bundle_yields_no_bundle_root(tmp_path):
    # Never the candidate's: a baseline with bundle_root=None gets no bundle tools.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "old"), "feat: v1")
    _commit_bundle(
        repo, {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/new.py": "print(1)"}, "feat: v2"
    )
    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))
    assert isinstance(previous, Skill)
    assert previous.bundle_root is None
    assert list((tmp_path / "baselines").iterdir()) == []


def test_two_resolutions_never_share_a_directory(tmp_path):
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"SKILL.md": _skill_md("1.0.0", "old"), "scripts/a.py": "1"}, "feat: v1")
    _commit_bundle(repo, {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/a.py": "2"}, "feat: v2")
    skill = parse_skill_file(repo / "SKILL.md")
    first = _resolve(tmp_path, skill)
    second = _resolve(tmp_path, skill)
    assert first.bundle_root != second.bundle_root


def test_an_archive_failure_is_unavailable_not_raised(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"SKILL.md": _skill_md("1.0.0", "old"), "scripts/a.py": "1"}, "feat: v1")
    _commit_bundle(repo, {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/a.py": "2"}, "feat: v2")
    import skill_lens.skills.baseline as baseline_module

    real = baseline_module._git_bytes

    def failing(args, cwd):
        return None if args[0] == "archive" else real(args, cwd)

    monkeypatch.setattr(baseline_module, "_git_bytes", failing)
    result = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))
    assert isinstance(result, BaselineUnavailable)
    assert "archive" in result.reason
