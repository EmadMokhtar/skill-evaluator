"""A read-only view of the files a skill ships beside SKILL.md.

The rule these tests protect: the bundle is `scripts/`, `references/` and
`assets/` and nothing else. Eval files sit beside SKILL.md and hold the
expected answers, so "any file beside SKILL.md" would hand the agent its own
answer key.
"""

from __future__ import annotations

import os

import pytest

from skill_lens.bundle import BUNDLE_DIRS, SkillBundle, has_bundle, script_extension
from skill_lens.workspace import PathRefused

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
    return root.resolve()


def test_the_bundle_directories_are_the_agent_skills_three():
    assert BUNDLE_DIRS == ("scripts", "references", "assets")


def test_has_bundle_needs_at_least_one_of_the_three(tmp_path):
    assert has_bundle(_skill_dir(tmp_path)) is True
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "SKILL.md").write_text("x", encoding="utf-8")
    (bare / "evals").mkdir()
    assert has_bundle(bare) is False


def test_listing_covers_the_three_directories_and_nothing_else(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.listing() == [
        "assets/logo.bin",
        "references/style.md",
        "scripts/count.py",
        "scripts/data.json",
        "scripts/nested/deep.sh",
    ]


def test_scripts_lists_only_what_is_under_scripts(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.scripts() == ["scripts/count.py", "scripts/data.json", "scripts/nested/deep.sh"]


def test_read_returns_text(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.read("references/style.md") == "# Style\n"


@pytest.mark.parametrize(
    "candidate",
    ["SKILL.md", "pdf.eval.yaml", "evals/more.yaml", "../pdf/scripts/count.py", "/etc/passwd", ""],
)
def test_read_refuses_everything_outside_the_three_directories(tmp_path, candidate):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused):
        bundle.read(candidate)


def test_read_names_the_three_directories_in_the_refusal(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match="scripts/, references/ or assets/"):
        bundle.read("SKILL.md")


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
