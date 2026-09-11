"""`skill-lens init` writes a starter suite, and refuses rather than clobbers."""

from __future__ import annotations

from typer.testing import CliRunner

from skill_lens.cases.loader import UNFILLED_SENTINEL
from skill_lens.cli import app

runner = CliRunner()

SKILL_MD = """---
name: order-support
description: Handle customer refund requests against the 30-day return policy
---

Always call lookup_order before deciding anything.
"""


def _skill_dir(tmp_path, text: str = SKILL_MD):
    path = tmp_path / "order-support"
    path.mkdir()
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    return path


def test_init_writes_the_suite_into_an_evals_directory(tmp_path):
    path = _skill_dir(tmp_path)
    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 0, result.output
    target = path / "evals" / "order-support.eval.yaml"
    assert target.is_file()
    assert UNFILLED_SENTINEL in target.read_text(encoding="utf-8")


def test_init_refuses_to_overwrite_without_force(tmp_path):
    path = _skill_dir(tmp_path)
    runner.invoke(app, ["init", str(path)])
    target = path / "evals" / "order-support.eval.yaml"
    target.write_text("cases: []\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 2
    assert "--force" in result.output
    assert target.read_text(encoding="utf-8") == "cases: []\n"


def test_force_overwrites(tmp_path):
    path = _skill_dir(tmp_path)
    target = path / "evals" / "order-support.eval.yaml"
    target.parent.mkdir()
    target.write_text("cases: []\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path), "--force"])
    assert result.exit_code == 0, result.output
    assert UNFILLED_SENTINEL in target.read_text(encoding="utf-8")


def test_a_path_with_no_skill_md_is_a_user_error(tmp_path):
    empty = tmp_path / "not-a-skill"
    empty.mkdir()
    result = runner.invoke(app, ["init", str(empty)])
    assert result.exit_code == 2
    assert "SKILL.md" in result.output


def test_a_skill_name_with_a_separator_cannot_escape_the_evals_directory(tmp_path):
    path = _skill_dir(
        tmp_path,
        "---\nname: ../../etc/passwd\ndescription: hostile\n---\n\nbody\n",
    )
    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 0, result.output
    written = list((path / "evals").iterdir())
    assert len(written) == 1
    assert written[0].parent == path / "evals"


def test_an_unwritable_target_is_a_user_error(tmp_path):
    # `evals` already exists as a *file*, so creating the directory fails.
    path = _skill_dir(tmp_path)
    (path / "evals").write_text("not a directory\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 2
    assert "cannot write" in result.output


def test_an_unfilled_scaffold_fails_a_run_as_an_authoring_error(tmp_path):
    # The end-to-end contract: init, then run, exits 2 with the field named --
    # not a green gate, and not a failure reported against the skill.
    path = _skill_dir(tmp_path)
    assert runner.invoke(app, ["init", str(path)]).exit_code == 0

    result = runner.invoke(app, ["run", str(path)])
    assert result.exit_code == 2
    assert UNFILLED_SENTINEL in result.output


def test_a_skill_md_with_non_mapping_frontmatter_is_a_user_error(tmp_path):
    # Frontmatter that parses as YAML but isn't a mapping (e.g. a bare list)
    # reaches SkillParseError, which init must surface as a clean exit 2 --
    # not a traceback, and not the "no SKILL.md" message, which is a
    # different cause.
    path = tmp_path / "order-support"
    path.mkdir()
    (path / "SKILL.md").write_text("---\n- just a list\n---\n\nbody\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 2
    assert "frontmatter" in result.output


def test_a_skill_md_with_unparseable_yaml_frontmatter_is_a_user_error(tmp_path):
    path = tmp_path / "order-support"
    path.mkdir()
    (path / "SKILL.md").write_text("---\nname: [unclosed\n---\n\nbody\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 2
    assert "frontmatter" in result.output


def test_init_writes_beside_skill_md_when_evals_already_live_there(tmp_path):
    path = _skill_dir(tmp_path)
    (path / "other.eval.yaml").write_text("cases: []\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 0, result.output
    assert (path / "order-support.eval.yaml").is_file()
    assert not (path / "evals").exists()
    # Discovery still sees the file that was already there.
    assert sorted(p.name for p in path.glob("*.eval.yaml")) == [
        "order-support.eval.yaml",
        "other.eval.yaml",
    ]


def _skills_root(tmp_path):
    root = tmp_path / "skills"
    for name in ("refund", "triage"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} things\n---\n\nbody\n", encoding="utf-8"
        )
    covered = root / "covered"
    covered.mkdir()
    (covered / "SKILL.md").write_text(
        "---\nname: covered\ndescription: already has a suite\n---\n\nbody\n", encoding="utf-8"
    )
    (covered / "covered.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    return root


def test_batch_init_scaffolds_missing_suites_and_skips_covered_skills(tmp_path):
    root = _skills_root(tmp_path)
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert f"Wrote {root / 'refund' / 'evals' / 'refund.eval.yaml'}" in lines
    assert f"Wrote {root / 'triage' / 'evals' / 'triage.eval.yaml'}" in lines
    assert "Skipped covered: already has 1 eval file(s)" in lines
    assert lines[-1] == f"Fill in every {UNFILLED_SENTINEL}, then run: skill-lens list {root}"
    assert (root / "covered" / "covered.eval.yaml").read_text(encoding="utf-8") == "cases: []\n"
    assert not (root / "covered" / "evals").exists()


def test_batch_init_rejects_force(tmp_path):
    root = _skills_root(tmp_path)
    result = runner.invoke(app, ["init", str(root), "--force"])
    assert result.exit_code == 2
    assert "--force applies to one skill" in result.output
    assert not (root / "refund" / "evals").exists()


def test_batch_init_with_nothing_to_scaffold_exits_zero_and_says_so(tmp_path):
    root = tmp_path / "skills"
    (root / "covered").mkdir(parents=True)
    (root / "covered" / "SKILL.md").write_text(
        "---\nname: covered\n---\n\nbody\n", encoding="utf-8"
    )
    (root / "covered" / "covered.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output
    assert f"Nothing to do: every skill under {root} already has an eval suite" in result.output


def test_batch_init_over_a_directory_with_no_skills_is_a_user_error(tmp_path):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    result = runner.invoke(app, ["init", str(empty)])
    assert result.exit_code == 2
    assert "SKILL.md" in result.output


def test_batch_init_surfaces_a_malformed_skill_as_a_user_error(tmp_path):
    root = tmp_path / "skills"
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "SKILL.md").write_text("---\nname: [unclosed\n---\n\nbody\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 2
    assert "frontmatter" in result.output
