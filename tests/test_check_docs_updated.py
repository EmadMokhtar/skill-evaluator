"""Unit tests for scripts/check_docs_updated.py.

The script is not an importable package, so it is loaded from its path -- the
same shape scripts/check_commits.py would use. Only the pure functions are
tested; the git call is left to CI.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_docs_updated.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_docs_updated", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


def test_source_changes_finds_package_files():
    paths = ["src/skill_eval/gating.py", "tests/test_gating.py", "README.md"]
    assert check.source_changes(paths) == ["src/skill_eval/gating.py"]


def test_source_changes_ignores_everything_outside_the_package():
    paths = ["tests/test_gating.py", "pyproject.toml", "examples/greeting/SKILL.md"]
    assert check.source_changes(paths) == []


def test_touches_docs_accepts_any_documented_surface():
    for path in ["docs/cli.md", "README.md", "ARCHITECTURE.md", "mkdocs.yml"]:
        assert check.touches_docs([path]), path


def test_touches_docs_rejects_the_historical_record():
    """docs/superpowers/ is a specs archive, not user documentation.

    A PR that only adds a design spec has not documented its code change.
    """
    assert not check.touches_docs(["docs/superpowers/specs/2026-08-02-x.md"])


def test_touches_docs_rejects_unrelated_paths():
    assert not check.touches_docs(["src/skill_eval/cli.py", "tests/test_cli.py"])


def test_main_passes_when_no_source_changed(monkeypatch, capsys):
    monkeypatch.setattr(check, "changed_files", lambda base, head: ["tests/test_cli.py"])
    assert check.main(["BASE", "HEAD"]) == 0


def test_main_passes_when_source_and_docs_both_changed(monkeypatch):
    monkeypatch.setattr(
        check, "changed_files", lambda base, head: ["src/skill_eval/cli.py", "docs/cli.md"]
    )
    assert check.main(["BASE", "HEAD"]) == 0


def test_main_fails_when_source_changed_without_docs(monkeypatch, capsys):
    monkeypatch.setattr(check, "changed_files", lambda base, head: ["src/skill_eval/cli.py"])
    assert check.main(["BASE", "HEAD"]) == 1
    out = capsys.readouterr().out
    assert "src/skill_eval/cli.py" in out
    assert "no-docs-needed" in out
