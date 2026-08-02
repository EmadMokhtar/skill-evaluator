"""Assert the documentation has not drifted from the code.

Every check here targets real drift -- a flag, field, or assertion kind that
exists in code but appears nowhere in the docs -- never prose style. Stale
wording is not detectable here; scripts/check_docs_updated.py is the (blunter)
backstop for that.

The autouse `isolate_cwd` fixture in conftest.py chdirs every test into a fresh
tmp_path, so everything below anchors on REPO_ROOT rather than Path.cwd().
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.main import get_command

from skill_eval.cli import app
from skill_eval.config import Config
from skill_eval.evaluators.assertion import ASSERTION_KINDS
from skill_eval.models import EvalCase
from skill_eval.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"

# docs/superpowers/ is a historical record of specs and plans, excluded from the
# site (see mkdocs.yml) and from every check here.
EXCLUDED_DIR = "superpowers"

# Typer/Click add these to every command; they are not project surface area.
IGNORED_FLAGS = {"--help", "--install-completion", "--show-completion"}


def _page(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _site_pages() -> set[str]:
    """Every published Markdown page, as a docs/-relative posix path."""
    return {
        path.relative_to(DOCS).as_posix()
        for path in DOCS.rglob("*.md")
        if EXCLUDED_DIR not in path.relative_to(DOCS).parts
    }


def _nav_pages() -> set[str]:
    """Every page reachable from the mkdocs.yml nav, flattened."""
    config = safe_load(MKDOCS_YML.read_text(encoding="utf-8"))
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            found.add(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(config["nav"])
    return found


def test_every_cli_command_is_documented():
    text = _page("cli.md")
    for name in get_command(app).commands:
        assert f"`{name}`" in text, f"command {name!r} is not documented in docs/cli.md"


def test_every_cli_option_is_documented():
    text = _page("cli.md")
    command = get_command(app)
    # The group's own options (e.g. --version) plus every subcommand's.
    all_params = list(command.params)
    for subcommand in command.commands.values():
        all_params.extend(subcommand.params)

    for param in all_params:
        if param.param_type_name != "option":
            continue
        for flag in param.opts:
            if flag in IGNORED_FLAGS or not flag.startswith("--"):
                continue
            assert flag in text, f"flag {flag} is not documented in docs/cli.md"


def test_every_config_field_is_documented():
    text = _page("configuration.md")
    for field in Config.model_fields:
        assert f"`{field}`" in text, f"config key {field!r} is not in docs/configuration.md"


def test_every_eval_case_field_is_documented():
    text = _page("eval-files.md")
    for field in EvalCase.model_fields:
        assert f"`{field}`" in text, f"case field {field!r} is not in docs/eval-files.md"


def test_every_assertion_kind_is_documented():
    text = _page("eval-files.md")
    for kind in ASSERTION_KINDS:
        assert f"`{kind}`" in text, f"assertion kind {kind!r} is not in docs/eval-files.md"


def test_every_page_is_reachable_from_the_nav():
    orphans = _site_pages() - _nav_pages()
    assert not orphans, f"pages not in the mkdocs.yml nav: {sorted(orphans)}"


def test_the_nav_has_no_missing_pages():
    missing = _nav_pages() - _site_pages()
    assert not missing, f"nav entries with no file on disk: {sorted(missing)}"


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _markdown_files() -> list[Path]:
    files = [
        path for path in DOCS.rglob("*.md") if EXCLUDED_DIR not in path.relative_to(DOCS).parts
    ]
    files.append(REPO_ROOT / "README.md")
    files.append(REPO_ROOT / "ARCHITECTURE.md")
    return files


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.name)
def test_relative_links_resolve(path: Path):
    for target in LINK_RE.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#", "<")):
            continue
        # Strip any anchor; only the file part is checked.
        relative = target.split("#", 1)[0]
        if not relative:
            continue
        resolved = (path.parent / relative).resolve()
        assert resolved.exists(), f"{path.name}: dead link to {target!r}"
