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


def test_the_previous_version_carries_its_own_file_text(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "v1"), "first")
    _commit(repo, _skill_md("1.1.0", "v2"), "second")
    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))
    assert previous.markdown == _skill_md("1.0.0", "v1")


# A skill's history across a move of its directory. `git log -- SKILL.md` stops
# at the commit that created the new path; the resolver continues from the old
# path only when that commit moved SKILL.md byte for byte, so a copy of another
# skill's file never borrows that skill's history.


def _move(repo: Path, source: str, destination: str, message: str = "refactor: move") -> None:
    (repo / destination).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "mv", source, destination], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def test_a_skill_moved_with_git_mv_resolves_its_pre_move_version_and_bundle(tmp_path):
    repo = _repo(tmp_path)
    _commit_bundle(
        repo,
        {"skills/pdf/SKILL.md": _skill_md("1.0.0", "old"), "skills/pdf/scripts/count.py": "old"},
        "feat: v1",
    )
    _commit_bundle(
        repo,
        {"skills/pdf/SKILL.md": _skill_md("1.1.0", "new"), "skills/pdf/scripts/count.py": "new"},
        "feat: v2",
    )
    _move(repo, "skills/pdf", "plugins/kit/skills/pdf")
    skill = parse_skill_file(repo / "plugins" / "kit" / "skills" / "pdf" / "SKILL.md")

    previous = _resolve(tmp_path, skill)

    assert isinstance(previous, Skill), previous
    assert previous.version == "1.0.0"
    assert previous.markdown == _skill_md("1.0.0", "old")
    # The candidate's directory, exactly as for a skill that never moved.
    assert previous.path == skill.path
    assert previous.bundle_root is not None
    assert (previous.bundle_root / "scripts" / "count.py").read_text(encoding="utf-8") == "old"
    assert not (previous.bundle_root / "SKILL.md").exists()


def test_a_skill_moved_twice_is_followed_through_both_moves(tmp_path):
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"a/pdf/SKILL.md": _skill_md("1.0.0", "old")}, "feat: v1")
    _commit_bundle(repo, {"a/pdf/SKILL.md": _skill_md("1.1.0", "new")}, "feat: v2")
    _move(repo, "a/pdf", "b/pdf")
    _move(repo, "b/pdf", "c/pdf")

    previous = _resolve(tmp_path, parse_skill_file(repo / "c" / "pdf" / "SKILL.md"))

    assert isinstance(previous, Skill), previous
    assert previous.version == "1.0.0"


def test_a_skill_moved_out_of_the_repository_root_keeps_its_history_and_bundle(tmp_path):
    repo = _repo(tmp_path)
    _commit_bundle(
        repo, {"SKILL.md": _skill_md("1.0.0", "old"), "scripts/count.py": "old"}, "feat: v1"
    )
    _commit_bundle(
        repo, {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/count.py": "new"}, "feat: v2"
    )
    (repo / "pdf").mkdir()
    subprocess.run(["git", "mv", "SKILL.md", "scripts", "pdf/"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "refactor: move"], cwd=repo, check=True)

    previous = _resolve(tmp_path, parse_skill_file(repo / "pdf" / "SKILL.md"))

    assert isinstance(previous, Skill), previous
    assert previous.version == "1.0.0"
    assert (previous.bundle_root / "scripts" / "count.py").read_text(encoding="utf-8") == "old"


def test_an_old_directory_name_that_needs_quoting_is_followed(tmp_path):
    # Brackets are pathspec glob syntax and a space splits an unquoted argv:
    # the old path must reach git as one literal path.
    repo = _repo(tmp_path)
    old = "skills/p[df] x"
    _commit_bundle(
        repo, {f"{old}/SKILL.md": _skill_md("1.0.0", "old"), f"{old}/scripts/a.py": "old"}, "v1"
    )
    _commit_bundle(repo, {f"{old}/SKILL.md": _skill_md("1.1.0", "new")}, "v2")
    _move(repo, old, "pdf")

    previous = _resolve(tmp_path, parse_skill_file(repo / "pdf" / "SKILL.md"))

    assert isinstance(previous, Skill), previous
    assert previous.version == "1.0.0"
    assert (previous.bundle_root / "scripts" / "a.py").read_text(encoding="utf-8") == "old"


def test_a_moved_skill_reached_through_a_symlink_is_followed(tmp_path):
    # This repository links .claude/skills/<name> to the skill it ships.
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"skills/pdf/SKILL.md": _skill_md("1.0.0", "old")}, "feat: v1")
    _commit_bundle(repo, {"skills/pdf/SKILL.md": _skill_md("1.1.0", "new")}, "feat: v2")
    _move(repo, "skills/pdf", "plugins/kit/skills/pdf")
    link = repo / "linked-pdf"
    link.symlink_to(repo / "plugins" / "kit" / "skills" / "pdf", target_is_directory=True)

    previous = _resolve(tmp_path, parse_skill_file(link / "SKILL.md"))

    assert isinstance(previous, Skill), previous
    assert previous.version == "1.0.0"


def test_a_skill_md_copied_from_another_skill_never_borrows_its_history(tmp_path):
    # `git log --follow` takes this copy for the file's origin -- it follows
    # copies as well as renames -- and would hand skill b the old instructions
    # of skill a. The source still exists, so it is a copy, never a move.
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"skills/a/SKILL.md": _skill_md("1.0.0", "a old")}, "feat: a v1")
    _commit_bundle(repo, {"skills/a/SKILL.md": _skill_md("1.1.0", "a new")}, "feat: a v2")
    _commit_bundle(repo, {"skills/b/SKILL.md": _skill_md("1.1.0", "a new")}, "feat: copy a to b")

    result = _resolve(tmp_path, parse_skill_file(repo / "skills" / "b" / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable), result


def test_a_copy_whose_source_is_deleted_later_is_still_not_followed(tmp_path):
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"skills/a/SKILL.md": _skill_md("1.0.0", "a old")}, "feat: a v1")
    _commit_bundle(repo, {"skills/a/SKILL.md": _skill_md("1.1.0", "a new")}, "feat: a v2")
    _commit_bundle(repo, {"skills/b/SKILL.md": _skill_md("1.1.0", "a new")}, "feat: copy a to b")
    subprocess.run(["git", "rm", "-rq", "skills/a"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "chore: drop a"], cwd=repo, check=True)

    result = _resolve(tmp_path, parse_skill_file(repo / "skills" / "b" / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable), result


def _long_skill_md(version: str) -> str:
    """A realistic SKILL.md: one edited line leaves it well over 90% similar."""
    body = "\n".join(f"{n}. Step {n} of the procedure, spelled out in full." for n in range(40))
    return f"---\nname: pdf\ndescription: Handle PDFs\nversion: {version}\n---\n\n{body}\n"


def test_a_move_that_also_edits_skill_md_is_not_followed(tmp_path):
    # Only a byte-for-byte move is certain to be the same file. A move and an
    # edit in one commit -- here a one-line version bump git itself would call
    # a rename -- is reported as unavailable, never guessed at.
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"skills/pdf/SKILL.md": _long_skill_md("1.0.0")}, "feat: v1")
    (repo / "plugins").mkdir()
    subprocess.run(["git", "mv", "skills/pdf", "plugins/pdf"], cwd=repo, check=True)
    (repo / "plugins" / "pdf" / "SKILL.md").write_text(_long_skill_md("1.1.0"), encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "feat: move and edit"], cwd=repo, check=True)

    result = _resolve(tmp_path, parse_skill_file(repo / "plugins" / "pdf" / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable), result


def test_a_move_elsewhere_in_the_creating_commit_is_never_borrowed(tmp_path):
    # The commit that creates skill b also moves an unrelated skill. That
    # move's source is not b's past.
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"skills/other/SKILL.md": _skill_md("1.0.0", "other old")}, "v1")
    _commit_bundle(repo, {"skills/other/SKILL.md": _skill_md("2.0.0", "other new")}, "v2")
    (repo / "moved").mkdir()
    subprocess.run(["git", "mv", "skills/other", "moved/other"], cwd=repo, check=True)
    _commit_bundle(repo, {"skills/b/SKILL.md": _skill_md("2.0.0", "b")}, "feat: add b, move other")

    result = _resolve(tmp_path, parse_skill_file(repo / "skills" / "b" / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable), result


def test_a_sibling_a_glob_would_match_does_not_use_up_the_history(tmp_path, monkeypatch):
    # `skills/p[df]` is a glob for `skills/pd` too; read as one, the sibling's
    # commits would fill the history limit before the moved skill's own did.
    import skill_lens.skills.baseline as baseline_module

    repo = _repo(tmp_path)
    _commit_bundle(repo, {"skills/p[df]/SKILL.md": _skill_md("1.0.0", "old")}, "v1")
    _commit_bundle(repo, {"skills/p[df]/SKILL.md": _skill_md("1.1.0", "new")}, "v2")
    for n in range(3):
        _commit_bundle(repo, {"skills/pd/SKILL.md": _skill_md(f"0.{n}.0", "sibling")}, f"pd {n}")
    _move(repo, "skills/p[df]", "plugins/pdf")
    monkeypatch.setattr(baseline_module, "HISTORY_LIMIT", 3)

    previous = _resolve(tmp_path, parse_skill_file(repo / "plugins" / "pdf" / "SKILL.md"))

    assert isinstance(previous, Skill), previous
    assert previous.version == "1.0.0"


def test_the_history_limit_counts_commits_on_both_sides_of_a_move(tmp_path, monkeypatch):
    import skill_lens.skills.baseline as baseline_module

    repo = _repo(tmp_path)
    _commit_bundle(repo, {"a/pdf/SKILL.md": _skill_md("1.0.0", "oldest")}, "feat: v1")
    _commit_bundle(repo, {"a/pdf/SKILL.md": _skill_md("1.1.0", "middle")}, "feat: v2")
    _commit_bundle(repo, {"a/pdf/SKILL.md": _skill_md("1.1.0", "reworded")}, "docs: v2")
    _move(repo, "a/pdf", "b/pdf")
    skill = parse_skill_file(repo / "b" / "pdf" / "SKILL.md")

    # The move, the reword and v2 all carry 1.1.0; v1 is the fourth commit back.
    monkeypatch.setattr(baseline_module, "HISTORY_LIMIT", 3)
    assert isinstance(_resolve(tmp_path, skill), BaselineUnavailable)

    monkeypatch.setattr(baseline_module, "HISTORY_LIMIT", 4)
    previous = _resolve(tmp_path, skill)
    assert isinstance(previous, Skill), previous
    assert previous.version == "1.0.0"
