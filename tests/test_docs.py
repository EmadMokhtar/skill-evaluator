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

from skill_lens.cli import app
from skill_lens.config import Config
from skill_lens.evaluators.assertion import ASSERTION_KINDS
from skill_lens.models import EvalCase
from skill_lens.runners.tools import NO_RESPONSE_SCRIPTED
from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"
RELEASING = REPO_ROOT / "docs" / "releasing.md"

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


def test_the_no_match_reply_is_quoted_as_the_code_spells_it():
    """An author reading a transcript searches the docs for the exact line
    the mock handed back; a reworded constant would leave that search empty."""
    text = _page("eval-files.md")
    expected = NO_RESPONSE_SCRIPTED.format(name="get_work_item", arguments='{"id": "C"}')
    assert f"`{expected}`" in text, f"docs/eval-files.md does not quote {expected!r}"


def test_every_page_is_reachable_from_the_nav():
    orphans = _site_pages() - _nav_pages()
    assert not orphans, f"pages not in the mkdocs.yml nav: {sorted(orphans)}"


def test_the_nav_has_no_missing_pages():
    missing = _nav_pages() - _site_pages()
    assert not missing, f"nav entries with no file on disk: {sorted(missing)}"


def test_releasing_documents_every_piece_of_external_setup():
    """The four settings live outside this repository, so nothing in CI can
    check them. The docs are the only place they are recorded.

    Each needle below is text that only occurs when the specific setup item
    it names is actually documented. A loose word like "pypi" or "pending
    publisher" also shows up in unrelated prose elsewhere on the page (PyPI
    the package index, "pending publisher does not reserve the name"), so
    checking for those alone would still pass with the setup item deleted.
    """
    text = RELEASING.read_text(encoding="utf-8")
    for required in (
        "Read and write",
        "`pypi` GitHub Environment",
        "pending publisher on PyPI",
        "OPENAI_API_KEY",
    ):
        assert required in text, f"docs/releasing.md does not mention {required!r}"


def test_releasing_is_in_the_nav():
    nav = safe_load((REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    assert "releasing.md" in str(nav)


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


SECURITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security.yml"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"


def test_security_documents_the_audit_as_ci_runs_it():
    """The page tells a reader how to run the check locally. If it spells the
    command differently from security.yml, the local result can disagree
    with the pipeline -- a flag dropped here, an exception applied there."""
    workflow = safe_load(SECURITY_WORKFLOW.read_text(encoding="utf-8"))
    runs = [str(step.get("run", "")) for step in workflow["jobs"]["audit"]["steps"]]
    command = next(line.strip() for run in runs for line in run.splitlines() if "uv audit" in line)
    assert command in _page("security.md"), f"docs/security.md does not show {command!r}"


def test_contributing_installs_every_pre_commit_stage():
    """A hook that is configured but never installed runs for nobody. The
    install command is derived from .pre-commit-config.yaml, so adding a
    stage there without documenting it here is a failing test, not a hook
    that silently only runs in CI."""
    config = safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    stages = {
        stage for repo in config["repos"] for hook in repo["hooks"] for stage in hook["stages"]
    }
    text = _page("contributing.md")
    for stage in sorted(stages):
        assert f"--hook-type {stage}" in text, (
            f"docs/contributing.md does not install the {stage!r} pre-commit stage"
        )


def test_contributing_documents_every_repository_setting():
    """Settings that live in the GitHub UI cannot be checked by CI, so the
    docs are the only record that they must be on."""
    text = _page("contributing.md")
    for required in ("Private vulnerability reporting", "Dependabot"):
        assert required in text, f"docs/contributing.md does not mention {required!r}"


def test_releasing_documents_the_sbom():
    text = RELEASING.read_text(encoding="utf-8")
    assert ".cdx.json" in text, "docs/releasing.md does not say where the SBOM is published"
