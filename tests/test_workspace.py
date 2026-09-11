"""The workspace: containment, caps, seeding, cleanup.

Containment is the whole of "sandboxed" in Part 1, so these tests are the
security boundary, not a nicety.
"""

from __future__ import annotations

import pytest

from skill_lens.models import WorkspaceSpec
from skill_lens.workspace import (
    DEFAULT_LIMITS,
    PathRefused,
    Workspace,
    WorkspaceError,
    WorkspaceLimits,
    check_relative_path,
    create_workspace,
    sanitise_label,
)


def _workspace(tmp_path, **limits):
    return Workspace(
        root=tmp_path.resolve(),
        limits=WorkspaceLimits(**limits) if limits else DEFAULT_LIMITS,
    )


def test_a_relative_path_resolves_inside_the_root(tmp_path):
    ws = _workspace(tmp_path)
    assert ws.resolve("report.md") == tmp_path.resolve() / "report.md"
    assert ws.resolve("nested/report.md") == tmp_path.resolve() / "nested" / "report.md"


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "   ",
        "/etc/passwd",
        "../escape.txt",
        "nested/../../escape.txt",
        "..",
    ],
)
def test_paths_that_leave_the_root_are_refused(tmp_path, candidate):
    ws = _workspace(tmp_path)
    with pytest.raises(PathRefused):
        ws.resolve(candidate)


@pytest.mark.parametrize(
    "candidate",
    ["", "   ", "/etc/passwd", "../escape.txt", "a/../../b.txt", ".", "a\x00b.txt"],
)
def test_check_relative_path_refuses_without_needing_a_root(candidate):
    # Root-independent so cases/loader.py can run it at load time, long before
    # any directory exists.
    with pytest.raises(PathRefused):
        check_relative_path(candidate)


def test_check_relative_path_refuses_a_lone_utf16_surrogate():
    # A surrogate is half of a UTF-16 pair; alone it cannot be encoded as
    # UTF-8, so letting it reach the filesystem raises UnicodeEncodeError --
    # a ValueError, not an OSError, that used to sail through this check and
    # surface well past it (see tests/test_assertion_evaluator.py for the
    # choke point this closes). YAML parses '\ud800' happily, so this is
    # exactly the kind of value an author's own file can carry.
    with pytest.raises(PathRefused, match="surrogate"):
        check_relative_path("a\ud800b.txt")


def test_check_relative_path_accepts_a_plain_relative_path():
    assert check_relative_path("nested/report.md") is None


def test_the_root_itself_is_refused(tmp_path):
    # Resolving to the directory rather than a file inside it: every caller
    # wants a file, and `.` would otherwise pass containment.
    ws = _workspace(tmp_path)
    with pytest.raises(PathRefused):
        ws.resolve(".")


def test_a_symlinked_root_still_contains(tmp_path):
    # macOS puts /tmp behind a symlink to /private/tmp. A root stored
    # unresolved would make every containment check compare two spellings of
    # the same directory and refuse legitimate writes.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    ws = Workspace(root=link.resolve())
    assert ws.write("a.txt", "x") == 1
    assert ws.read("a.txt") == "x"


def test_a_symlink_inside_the_workspace_cannot_reach_outside(tmp_path):
    # Guards against a plausible-looking refactor: replacing .resolve() with
    # os.path.normpath to avoid touching the filesystem. normpath only
    # rewrites ".." segments textually -- it does not follow symlinks -- so
    # "root/link/victim.txt" would normalise to a path that IS relative to
    # root even though the symlink actually points outside it. All six
    # existing escape parametrizations are textual (".." or an absolute
    # path); the only other symlink test covers a symlinked root, which is a
    # positive case. Without this test, that refactor would pass all other
    # tests and open a real escape.
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("original", encoding="utf-8")
    (root / "link").symlink_to(outside)
    (root / "victim.txt").symlink_to(outside / "victim.txt")
    ws = Workspace(root=root)
    with pytest.raises(PathRefused):
        ws.read("link/victim.txt")
    with pytest.raises(PathRefused):
        ws.write("victim.txt", "pwned")
    assert (outside / "victim.txt").read_text(encoding="utf-8") == "original"


def test_write_then_read_round_trips_utf8(tmp_path):
    ws = _workspace(tmp_path)
    assert ws.write("nested/report.md", "café") == 5
    assert ws.read("nested/report.md") == "café"


def test_listing_is_relative_sorted_and_recursive(tmp_path):
    ws = _workspace(tmp_path)
    ws.write("b.txt", "b")
    ws.write("nested/a.txt", "a")
    assert ws.listing() == ["b.txt", "nested/a.txt"]


def test_a_file_over_the_size_cap_is_refused_and_names_the_cap(tmp_path):
    ws = _workspace(tmp_path, max_file_bytes=10)
    with pytest.raises(PathRefused) as excinfo:
        ws.write("big.txt", "x" * 11)
    message = str(excinfo.value)
    assert "max_file_bytes" in message
    assert "10" in message


def test_too_many_files_is_refused_and_names_the_cap(tmp_path):
    ws = _workspace(tmp_path, max_files=2)
    ws.write("a.txt", "a")
    ws.write("b.txt", "b")
    with pytest.raises(PathRefused) as excinfo:
        ws.write("c.txt", "c")
    assert "max_files" in str(excinfo.value)


def test_overwriting_an_existing_file_does_not_count_as_a_new_one(tmp_path):
    # Otherwise an agent that revises its report hits the file cap for a
    # directory whose file count never changed.
    ws = _workspace(tmp_path, max_files=1)
    ws.write("a.txt", "first")
    ws.write("a.txt", "second")
    assert ws.read("a.txt") == "second"


def test_exceeding_the_total_cap_is_refused_and_names_the_cap(tmp_path):
    ws = _workspace(tmp_path, max_total_bytes=10)
    ws.write("a.txt", "x" * 6)
    with pytest.raises(PathRefused) as excinfo:
        ws.write("b.txt", "y" * 6)
    assert "max_total_bytes" in str(excinfo.value)


def test_the_total_cap_counts_a_replacement_once(tmp_path):
    # The old bytes go away, so a same-size rewrite must not double-count.
    ws = _workspace(tmp_path, max_total_bytes=10)
    ws.write("a.txt", "x" * 9)
    ws.write("a.txt", "y" * 9)
    assert ws.read("a.txt") == "y" * 9


def test_create_workspace_seeds_declared_files():
    ws = create_workspace(WorkspaceSpec(files={"in.csv": "a,b\n"}), label="s-c")
    try:
        assert ws.read("in.csv") == "a,b\n"
        assert ws.root.name.startswith("skill-lens-s-c-")
    finally:
        ws.cleanup()


def test_create_workspace_with_no_files_still_makes_a_directory():
    ws = create_workspace(WorkspaceSpec(), label="empty")
    try:
        assert ws.root.is_dir()
        assert ws.listing() == []
    finally:
        ws.cleanup()


def test_a_seeded_path_that_escapes_is_a_workspace_error():
    # The loader rejects this first; this is the second guard, for an EvalCase
    # built programmatically.
    with pytest.raises(WorkspaceError):
        create_workspace(WorkspaceSpec(files={"../escape.txt": "x"}), label="bad")


def test_a_failed_seed_leaves_no_directory_behind(tmp_path, monkeypatch):
    made: list = []
    import skill_lens.workspace as module

    real_mkdtemp = module.tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        made.append(path)
        return path

    monkeypatch.setattr(module.tempfile, "mkdtemp", recording_mkdtemp)
    with pytest.raises(WorkspaceError):
        create_workspace(WorkspaceSpec(files={"../escape.txt": "x"}), label="bad")
    from pathlib import Path as _Path

    assert made and not _Path(made[0]).exists()


def test_a_nul_byte_seed_key_leaves_no_directory_behind(tmp_path, monkeypatch):
    # check_relative_path now refuses a NUL byte before it ever reaches
    # .resolve(), so this raises PathRefused rather than the ValueError the
    # unpatched code let escape -- but the seed must still be cleaned up and
    # still arrive as WorkspaceError, same as any other refused seed.
    made: list = []
    import skill_lens.workspace as module

    real_mkdtemp = module.tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        made.append(path)
        return path

    monkeypatch.setattr(module.tempfile, "mkdtemp", recording_mkdtemp)
    with pytest.raises(WorkspaceError):
        create_workspace(WorkspaceSpec(files={"a\x00b.txt": "x"}), label="nul")
    from pathlib import Path as _Path

    assert made and not _Path(made[0]).exists()


def test_cleanup_is_idempotent_and_never_raises(tmp_path):
    ws = create_workspace(WorkspaceSpec(files={"a.txt": "x"}), label="c")
    ws.cleanup()
    ws.cleanup()  # deleting a directory is housekeeping, never a verdict
    assert not ws.root.exists()


def test_two_workspaces_never_share_a_directory():
    first = create_workspace(WorkspaceSpec(), label="same")
    second = create_workspace(WorkspaceSpec(), label="same")
    try:
        assert first.root != second.root
    finally:
        first.cleanup()
        second.cleanup()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("order-support", "order-support"),
        ("a b/c", "a-b-c"),
        ("///", "case"),
        ("", "case"),
        ("x" * 100, "x" * 60),
    ],
)
def test_sanitise_label(raw, expected):
    assert sanitise_label(raw) == expected
