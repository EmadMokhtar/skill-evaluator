"""A read-only view of the files a skill ships beside SKILL.md.

The rule these tests protect: the bundle is every file in the skill's
directory, as an install copies it (issue #92), except `SKILL.md` itself, the
eval files -- `evals/`, `*.eval.yaml` and `evals.json`, at any depth, because
they hold the expected answers -- and `.git`.
"""

from __future__ import annotations

import os

import pytest

from promptly import promptly
from skill_lens.bundle import SkillBundle, has_bundle, script_extension, withheld
from skill_lens.workspace import FileTooLarge, PathRefused

INTERPRETERS = {"py": ("python3",), "sh": ("bash",)}


def _skill_dir(tmp_path):
    root = tmp_path / "pdf"
    (root / "scripts").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "assets").mkdir()
    (root / "evals").mkdir()
    (root / "SKILL.md").write_text("---\nname: pdf\n---\nbody\n", encoding="utf-8")
    (root / "pdf.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    (root / "evals" / "more.yaml").write_text("cases: []\n", encoding="utf-8")
    (root / "scripts" / "count.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "scripts" / "nested").mkdir()
    (root / "scripts" / "nested" / "deep.sh").write_text("echo hi\n", encoding="utf-8")
    (root / "scripts" / "data.json").write_text("{}", encoding="utf-8")
    (root / "references" / "style.md").write_text("# Style\n", encoding="utf-8")
    (root / "assets" / "logo.bin").write_bytes(b"\xff\xfe\x00binary")
    # Outside the three Agent Skills directories, as planning-with-files ships them.
    (root / "templates").mkdir()
    (root / "templates" / "task_plan.md").write_text("# Plan\n", encoding="utf-8")
    (root / "reference.md").write_text("# Reference\n", encoding="utf-8")
    # Eval files below the top level, and version-control metadata.
    (root / "references" / "nested").mkdir()
    (root / "references" / "nested" / "x.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    (root / "examples" / "evals").mkdir(parents=True)
    (root / "examples" / "evals" / "a.yaml").write_text("cases: []\n", encoding="utf-8")
    (root / "assets" / "evals.json").write_text("{}", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    return root.resolve()


@pytest.mark.parametrize(
    ("path", "kept_back"),
    [
        ("SKILL.md", True),
        ("pdf.eval.yaml", True),
        ("evals", True),
        ("evals/more.yaml", True),
        ("references/nested/x.eval.yaml", True),
        ("examples/evals/a.yaml", True),
        ("assets/evals.json", True),
        (".git/config", True),
        ("references/SKILL.md", False),
        ("templates/task_plan.md", False),
        ("reference.md", False),
        ("scripts/count.py", False),
        ("evalsx/notes.md", False),
    ],
)
def test_withheld_names_skill_md_the_eval_files_and_git(path, kept_back):
    assert withheld(path) is kept_back


def test_has_bundle_needs_one_file_the_skill_ships(tmp_path):
    assert has_bundle(_skill_dir(tmp_path)) is True
    bare = tmp_path / "bare"
    (bare / "evals").mkdir(parents=True)
    (bare / "SKILL.md").write_text("x", encoding="utf-8")
    (bare / "evals" / "cases.yaml").write_text("cases: []\n", encoding="utf-8")
    (bare / "bare.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    (bare / "references").mkdir()  # empty: ships nothing
    assert has_bundle(bare) is False
    (bare / "reference.md").write_text("# R\n", encoding="utf-8")
    assert has_bundle(bare) is True


def test_listing_is_every_shipped_file_and_no_eval_file(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.listing() == [
        "assets/logo.bin",
        "reference.md",
        "references/style.md",
        "scripts/count.py",
        "scripts/data.json",
        "scripts/nested/deep.sh",
        "templates/task_plan.md",
    ]


def test_read_reaches_a_file_outside_the_three_agent_skills_directories(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.read("templates/task_plan.md") == "# Plan\n"
    assert bundle.read("reference.md") == "# Reference\n"


def test_scripts_lists_only_what_is_under_scripts(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.scripts() == ["scripts/count.py", "scripts/data.json", "scripts/nested/deep.sh"]


def test_read_returns_text(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.read("references/style.md") == "# Style\n"


@pytest.mark.parametrize(
    "candidate",
    [
        "SKILL.md",
        "pdf.eval.yaml",
        "evals/more.yaml",
        "references/nested/x.eval.yaml",
        "examples/evals/a.yaml",
        "assets/evals.json",
        ".git/config",
        "../pdf/scripts/count.py",
        "/etc/passwd",
        "",
    ],
)
def test_read_refuses_skill_md_eval_files_git_and_anything_outside(tmp_path, candidate):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused):
        bundle.read(candidate)


def test_read_names_what_is_never_readable_in_the_refusal(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match=r"eval files \(evals/, \*\.eval\.yaml, evals\.json\)"):
        bundle.read("evals/more.yaml")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_onto_an_eval_file_is_neither_listed_nor_readable(tmp_path):
    """The name the model asks for is not the rule; where it resolves is."""
    root = _skill_dir(tmp_path)
    (root / "references" / "answers.md").symlink_to(root / "evals" / "more.yaml")
    bundle = SkillBundle(root)
    assert "references/answers.md" not in bundle.listing()
    with pytest.raises(PathRefused):
        bundle.read("references/answers.md")


def test_read_of_a_binary_file_raises_the_decode_error_for_the_tool_to_catch(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(UnicodeDecodeError):
        bundle.read("assets/logo.bin")


def test_read_of_a_missing_file_raises_oserror(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(OSError):
        bundle.read("references/missing.md")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_out_of_the_root_is_neither_listed_nor_readable(tmp_path):
    root = _skill_dir(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("hidden", encoding="utf-8")
    (root / "references" / "leak.md").symlink_to(secret)
    bundle = SkillBundle(root)
    assert "references/leak.md" not in bundle.listing()
    with pytest.raises(PathRefused):
        bundle.read("references/leak.md")


def test_script_resolves_a_bundled_script(tmp_path):
    root = _skill_dir(tmp_path)
    bundle = SkillBundle(root)
    assert bundle.script("scripts/count.py", INTERPRETERS) == root / "scripts" / "count.py"
    assert bundle.script("scripts/nested/deep.sh", INTERPRETERS) == (
        root / "scripts" / "nested" / "deep.sh"
    )


def test_script_refuses_a_reference_file(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match="not under scripts/"):
        bundle.script("references/style.md", INTERPRETERS)


def test_script_refuses_a_missing_script_and_lists_the_bundled_ones(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match=r"no such script.*scripts/count\.py, scripts/data\.json"):
        bundle.script("scripts/nope.py", INTERPRETERS)


def test_script_refuses_an_unmapped_extension_and_lists_the_allowed_ones(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match="no configured interpreter.*py, sh"):
        bundle.script("scripts/data.json", INTERPRETERS)


def test_script_extension_is_lower_cased_without_the_dot():
    assert script_extension("scripts/Count.PY") == "py"
    assert script_extension("scripts/noext") == ""


# --- regular files only ----------------------------------------------------
#
# A bundle is committed content, but a checkout can still carry a FIFO, a
# symlink loop or a sparse file, and the tools on top must never block or
# swallow memory on one.

posix_only = pytest.mark.skipif(os.name == "nt", reason="FIFOs and symlinks are POSIX features")


@posix_only
def test_a_fifo_in_the_bundle_is_refused_before_it_is_opened(tmp_path):
    root = _skill_dir(tmp_path)
    os.mkfifo(root / "references" / "pipe.md")
    os.mkfifo(root / "scripts" / "pipe.py")
    bundle = SkillBundle(root)
    with pytest.raises(PathRefused, match="not a regular file"):
        promptly(lambda: bundle.read("references/pipe.md"))
    with pytest.raises(PathRefused, match="not a regular file"):
        bundle.script("scripts/pipe.py", {"py": ("python3",)})
    assert "references/pipe.md" not in bundle.listing()


@posix_only
def test_a_symlink_loop_in_the_bundle_is_a_refusal_on_every_python(tmp_path):
    root = _skill_dir(tmp_path)
    os.symlink("loop", root / "references" / "loop")
    bundle = SkillBundle(root)
    for candidate in ("references/loop", "references/loop/style.md"):
        with pytest.raises(PathRefused):
            bundle.read(candidate)
    assert "references/loop" not in bundle.listing()


def test_a_bundled_file_over_max_file_bytes_is_refused_and_names_the_cap(tmp_path):
    root = _skill_dir(tmp_path)
    with (root / "references" / "huge.md").open("wb") as handle:
        handle.seek(2_000_000)
        handle.write(b"x")
    expected = r"huge.md is 2,000,001 bytes; max_file_bytes is 1,000,000"
    with pytest.raises(FileTooLarge, match=expected):
        SkillBundle(root).read("references/huge.md")


def test_the_eval_file_names_match_the_case_loader():
    """bundle.py cannot import them (the loader imports the tools, which import
    the bundle), so this keeps the two spellings from drifting: an eval file
    the loader finds must be one the bundle withholds."""
    from skill_lens import bundle
    from skill_lens.cases import evals_json, loader

    assert bundle.EVALS_DIRNAME == loader.EVALS_DIRNAME
    assert bundle.EVAL_SUFFIX == loader.EVAL_SUFFIX
    assert bundle.EVALS_JSON_FILENAME == evals_json.EVALS_JSON_FILENAME
