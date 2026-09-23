"""Assert the documentation has not drifted from the code.

Every check here targets real drift -- a flag, field, or assertion kind that
exists in code but appears nowhere in the docs -- never prose style. Stale
wording is not detectable here; scripts/check_docs_updated.py is the (blunter)
backstop for that.

The autouse `isolate_cwd` fixture in conftest.py chdirs every test into a fresh
tmp_path, so everything below anchors on REPO_ROOT rather than Path.cwd().
"""

from __future__ import annotations

import ast
import html
import itertools
import re
import unicodedata
from pathlib import Path

import pytest
import yaml
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


class _TagTolerantLoader(yaml.SafeLoader):
    """A loader for mkdocs.yml only.

    mkdocs.yml carries `!!python/name:` tags, which MkDocs resolves at build
    time and a SafeLoader refuses. These tests only read the nav and the
    extension names, so an unknown tag can safely become None. The project's
    own skill_lens.yaml_loading.safe_load is deliberately NOT changed: it
    guards user-authored YAML, where an unknown tag must still be refused.
    """


_TagTolerantLoader.add_multi_constructor("", lambda loader, suffix, node: None)


def _mkdocs_config() -> dict:
    # S506 cannot see that _TagTolerantLoader subclasses SafeLoader: it only
    # recognises the name `SafeLoader` itself. The loader is safe by
    # construction (see the class above), so the finding is a false positive.
    return yaml.load(MKDOCS_YML.read_text(encoding="utf-8"), Loader=_TagTolerantLoader)  # noqa: S506


# docs/superpowers/ is a historical record of specs and plans, and
# docs/snippets/ holds text other pages include. Both are excluded from the site
# (see mkdocs.yml) and from every check here that reads pages; an included
# snippet is checked as part of each page that includes it.
EXCLUDED_DIRS = {"superpowers", "snippets"}

# Typer/Click add these to every command; they are not project surface area.
IGNORED_FLAGS = {"--help", "--install-completion", "--show-completion"}


def _page(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _site_pages() -> set[str]:
    """Every published Markdown page, as a docs/-relative posix path."""
    return {
        path.relative_to(DOCS).as_posix()
        for path in DOCS.rglob("*.md")
        if not EXCLUDED_DIRS & set(path.relative_to(DOCS).parts)
    }


def _nav_pages() -> set[str]:
    """Every page reachable from the mkdocs.yml nav, flattened."""
    config = _mkdocs_config()
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


def test_the_hidden_data_examples_agree_with_the_check():
    # docs/eval-files.md shows one rubric line the loader refuses and one it
    # accepts. Pinning both to `find_hidden_data_reference` keeps the page
    # from promising a refusal (or a pass) the code no longer makes.
    from skill_lens.cases.checks import find_hidden_data_reference

    text = _page("eval-files.md")
    lines = re.findall(r"^\s+- (The summary [^\n]+)$", text, flags=re.MULTILINE)
    refused = [line for line in lines if "mocked data" in line]
    accepted = [line for line in lines if "threads.json" in line]
    assert refused and accepted, "the refused/accepted rubric examples are missing"
    for line in refused:
        assert find_hidden_data_reference(line) is not None, line
    for line in accepted:
        assert find_hidden_data_reference(line) is None, line


def test_every_page_is_reachable_from_the_nav():
    orphans = _site_pages() - _nav_pages()
    assert not orphans, f"pages not in the mkdocs.yml nav: {sorted(orphans)}"


def test_the_nav_has_no_missing_pages():
    missing = _nav_pages() - _site_pages()
    assert not missing, f"nav entries with no file on disk: {sorted(missing)}"


SITE_URL = "https://emadmokhtar.github.io/skill-evaluator/"
ARCHITECTURE_URL = "https://github.com/EmadMokhtar/skill-evaluator/blob/main/ARCHITECTURE.md"


def _table_after(text: str, heading: str) -> str:
    """The first Markdown table under `heading`, as its raw rows."""
    lines = text[text.index(heading) :].splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("|"))
    return "\n".join(itertools.takewhile(lambda line: line.startswith("|"), lines[start:]))


def _hub_pages() -> set[str]:
    """The pages linked from the two directories a reader starts from:
    README.md's Documentation table and docs/index.md's "Where to go next"."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    found: set[str] = set()
    for target in LINK_RE.findall(_table_after(readme, "## Documentation")):
        if target == ARCHITECTURE_URL:
            found.add("architecture.md")
        elif target.startswith(SITE_URL):
            found.add(target.removeprefix(SITE_URL).strip("/") + ".md")
    found.update(LINK_RE.findall(_table_after(_page("index.md"), "## Where to go next")))
    return found


def test_every_page_is_in_a_hub_table():
    """The sidebar lists every page, but a reader landing on README.md or the
    home page scans its table instead. A page in neither is one they will
    not find from where they start."""
    missing = sorted(_nav_pages() - _hub_pages() - {"index.md"})
    assert not missing, (
        "pages linked from neither README.md's Documentation table nor docs/index.md's "
        f"'Where to go next' table: {missing}"
    )


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
    nav = _mkdocs_config()["nav"]
    assert "releasing.md" in str(nav)


def test_the_mkdocs_config_parses_with_a_python_name_tag():
    """mkdocs.yml carries `!!python/name:` tags (Mermaid's custom fence needs
    one). The project's own safe_load refuses unknown tags, which is right for
    user YAML and wrong here, so the tests parse mkdocs.yml with a loader that
    ignores them. This test fails if that loader is swapped back.
    """
    config = _mkdocs_config()
    fences = config["markdown_extensions"]
    superfences = next(
        entry["pymdownx.superfences"]
        for entry in fences
        if isinstance(entry, dict) and "pymdownx.superfences" in entry
    )
    names = [fence["name"] for fence in superfences["custom_fences"]]
    assert "mermaid" in names, f"no mermaid custom fence in mkdocs.yml: {names}"

    # The parsed `format` is None, because the tag-tolerant loader maps every
    # unknown tag to None -- so the tag itself has to be asserted against the
    # raw text. Without this, replacing it with a plain string would leave the
    # test green while every mermaid block silently stopped rendering, and
    # would remove the only unknown tag in the file, so nothing would notice
    # _mkdocs_config being swapped back to the strict safe_load.
    tag = "!!python/name:pymdownx.superfences.fence_code_format"
    raw = MKDOCS_YML.read_text(encoding="utf-8")
    assert tag in raw, f"mkdocs.yml no longer carries {tag}"


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _markdown_files() -> list[Path]:
    files = [
        path for path in DOCS.rglob("*.md") if not EXCLUDED_DIRS & set(path.relative_to(DOCS).parts)
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


# `--8<-- "path"` on a line of its own, as pymdownx.snippets reads it. The
# path is relative to the repository root (`base_path: ["."]` in mkdocs.yml).
SNIPPET_RE = re.compile(r'^--8<-- "([^"]+)"$', re.MULTILINE)
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
INLINE_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _expanded(path: Path) -> str:
    """The page as MkDocs renders it: every snippet include replaced by the
    file it names. docs/architecture.md is one line until this runs."""
    text = path.read_text(encoding="utf-8")
    return SNIPPET_RE.sub(lambda m: (REPO_ROOT / m.group(1)).read_text(encoding="utf-8"), text)


def _heading_texts(text: str) -> list[str]:
    """The Markdown source of every ATX heading outside a fenced code block."""
    headings: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        opened = FENCE_RE.match(line)
        if opened:
            marker = opened.group(1)
            if fence is None:
                fence = marker
            elif set(line.strip()) == {fence[0]} and len(line.strip()) >= len(fence):
                fence = None
            continue
        if fence is None:
            heading = HEADING_RE.match(line)
            if heading:
                headings.append(heading.group(2))
    return headings


def _heading_plain_text(source: str) -> str:
    """What the heading reads as once rendered: a code span keeps its text,
    a link keeps its label, and a tag outside code disappears."""
    parts: list[str] = []
    position = 0
    for span in CODE_SPAN_RE.finditer(source):
        before = source[position : span.start()]
        parts.append(HTML_TAG_RE.sub("", INLINE_LINK_RE.sub(r"\1", before)))
        parts.append(span.group(2).strip())
        position = span.end()
    parts.append(HTML_TAG_RE.sub("", INLINE_LINK_RE.sub(r"\1", source[position:])))
    return html.unescape("".join(parts))


def _slugify(text: str) -> str:
    """Python-Markdown's `toc` slugify, the one MkDocs uses for heading ids.

    Accented letters become ASCII and anything else outside ASCII is dropped;
    then every character that is not a word character, whitespace or a hyphen
    is removed, the rest is lowercased, and each run of hyphens or whitespace
    becomes one hyphen. `test_heading_ids_match_python_markdown` compares this
    with the real renderer whenever the docs group is installed.
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def _heading_ids(path: Path) -> list[str]:
    """Every heading id on the rendered page, in page order.

    A repeated id is made unique as Python-Markdown's `unique` does it: an id
    already ending in `_N` becomes `_N+1`, any other id gets `_1`. So a link to
    the second of two equal headings is checked as well.
    """
    ids: list[str] = []
    for source in _heading_texts(_expanded(path)):
        unique = _slugify(_heading_plain_text(source))
        while unique in ids or not unique:
            numbered = re.match(r"^(.*)_([0-9]+)$", unique)
            unique = f"{numbered[1]}_{int(numbered[2]) + 1}" if numbered else f"{unique}_1"
        ids.append(unique)
    return ids


def _published_pages() -> list[Path]:
    return sorted(DOCS / name for name in _site_pages())


def _anchor_links(path: Path) -> list[tuple[str, Path, str]]:
    """Every relative link on `path` that names a heading on a Markdown page,
    as (the link as written, the page it points at, the heading id)."""
    links: list[tuple[str, Path, str]] = []
    for target in LINK_RE.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "<")) or "#" not in target:
            continue
        relative, anchor = target.split("#", 1)
        destination = (path.parent / relative).resolve() if relative else path
        if destination.suffix == ".md":
            links.append((target, destination, anchor))
    return links


@pytest.mark.parametrize("path", _published_pages(), ids=lambda p: p.name)
def test_link_anchors_resolve(path: Path):
    """test_relative_links_resolve checks the file a link names; this checks
    the heading after its `#`. Renaming a heading otherwise breaks every link
    to it silently, and docs/invariants.md alone has well over a hundred.

    Only published pages are read. README.md and ARCHITECTURE.md are rendered
    by GitHub, whose heading ids follow different rules, and neither carries a
    relative link with an anchor.
    """
    broken = [
        target
        for target, destination, anchor in _anchor_links(path)
        if anchor not in _heading_ids(destination)
    ]
    assert not broken, f"{path.name}: links to headings that do not exist: {broken}"


# The anchor test passes vacuously if it finds no links to check: a change to
# LINK_RE, or to how an anchor is split off, would turn every page green
# without reading a single heading. There were 177 such links when this was
# written, and the number only grows as pages link into docs/invariants.md.
MINIMUM_ANCHOR_LINKS = 150


def test_the_anchor_check_reads_the_site():
    total = sum(len(_anchor_links(path)) for path in _published_pages())
    assert total >= MINIMUM_ANCHOR_LINKS, (
        f"found only {total} link(s) with an anchor across the site, below the floor of "
        f"{MINIMUM_ANCHOR_LINKS}; the link parse in this file has probably broken"
    )


@pytest.mark.parametrize("path", _published_pages(), ids=lambda p: p.name)
def test_heading_ids_match_python_markdown(path: Path):
    """The anchor test is only as good as `_heading_ids`. Where Python-Markdown
    is installed (`uv sync --group docs`, as the CI docs job does), compare it
    with the real renderer, configured with the extensions mkdocs.yml uses."""
    markdown = pytest.importorskip("markdown")
    renderer = markdown.Markdown(
        extensions=["admonition", "tables", "toc", "pymdownx.superfences", "pymdownx.snippets"],
        extension_configs={
            "pymdownx.snippets": {"base_path": [str(REPO_ROOT)], "check_paths": True}
        },
    )
    renderer.convert(path.read_text(encoding="utf-8"))

    real: list[str] = []

    def walk(tokens: list[dict]) -> None:
        for token in tokens:
            real.append(token["id"])
            walk(token["children"])

    walk(renderer.toc_tokens)
    assert _heading_ids(path) == real


INSTALL_SNIPPET = DOCS / "snippets" / "install.md"
# How a page tells a reader to install the package, or to install it from a
# checkout with an extra. Not `uv tool install` alone: docs/ci.md names that
# command to explain what the action's `install-spec` input is passed to.
INSTALL_RE = re.compile(r'(?:uv tool install|pip install) "?skill-lens\b|uv sync --extra')


def test_the_install_instructions_are_written_once():
    """A new extra, a renamed one, or a different recommended installer is
    one edit, not five. Every page that shows how to install includes
    docs/snippets/install.md; README.md cannot include a file, and is pinned
    to it by the next test instead."""
    pages = [DOCS / name for name in sorted(_site_pages())] + [REPO_ROOT / "ARCHITECTURE.md"]
    copies = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}"
        for path in pages
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if INSTALL_RE.search(line)
    ]
    assert not copies, (
        "install instructions written out instead of included from "
        '`--8<-- "docs/snippets/install.md"`:\n  ' + "\n  ".join(copies)
    )


def _install_command(path: Path) -> str:
    """The first `uv tool install` line in `path`, without its comment."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("uv tool install "):
            return line.split("#", 1)[0].strip()
    raise AssertionError(f"{path.relative_to(REPO_ROOT)} shows no `uv tool install` command")


def test_the_readme_installs_what_the_docs_install():
    assert _install_command(REPO_ROOT / "README.md") == _install_command(INSTALL_SNIPPET)


# The three protocols, as (class, method, module under src/skill_lens/).
PROTOCOLS = (
    ("Runner", "run", "runners/base.py"),
    ("Evaluator", "evaluate", "evaluators/base.py"),
    ("Judge", "judge", "judges/base.py"),
)


def _method(tree: ast.AST, class_name: str, method: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method:
                    return item
    raise AssertionError(f"no {class_name}.{method} found")


def _protocol_method(class_name: str, method: str, module: str) -> ast.FunctionDef:
    source = (REPO_ROOT / "src" / "skill_lens" / module).read_text(encoding="utf-8")
    return _method(ast.parse(source), class_name, method)


def _short_signature(function: ast.FunctionDef) -> str:
    """`run(skill, case) -> RunResult`: the parameters a caller must pass,
    without `self` and without the ones that have a default."""
    positional = function.args.args[1:]
    required = positional[: len(positional) - len(function.args.defaults)]
    names = ", ".join(argument.arg for argument in required)
    return f"{function.name}({names}) -> {ast.unparse(function.returns)}"


def _full_signature(function: ast.FunctionDef) -> str:
    return f"def {function.name}({ast.unparse(function.args)}) -> {ast.unparse(function.returns)}"


@pytest.mark.parametrize(("class_name", "method", "module"), PROTOCOLS)
def test_claude_md_states_each_protocol_as_the_code_defines_it(
    class_name: str, method: str, module: str
):
    """CLAUDE.md's Architecture section is loaded into every agent session;
    a signature there that the code no longer has misleads every one."""
    short = _short_signature(_protocol_method(class_name, method, module))
    expected = f"**`{class_name}`** (`{module}`) — `{short}`"
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert expected in text, f"CLAUDE.md does not state {expected!r}"


@pytest.mark.parametrize(("class_name", "method", "module"), PROTOCOLS)
def test_architecture_states_each_protocol_as_the_code_defines_it(
    class_name: str, method: str, module: str
):
    """ARCHITECTURE.md shows each protocol twice: as the Python it is, and
    as a one-line label in the diagram. Both are compared with the source."""
    function = _protocol_method(class_name, method, module)
    text = (REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    assert f"{class_name}<br/>{_short_signature(function)}" in text, (
        f"the ARCHITECTURE.md diagram does not label {class_name} as {_short_signature(function)!r}"
    )

    blocks = re.findall(r"^```python\n(.*?)^```", text, flags=re.MULTILINE | re.DOTALL)
    shown = [
        _method(tree, class_name, method)
        for tree in map(ast.parse, blocks)
        if any(isinstance(n, ast.ClassDef) and n.name == class_name for n in ast.walk(tree))
    ]
    assert len(shown) == 1, f"ARCHITECTURE.md shows class {class_name} {len(shown)} times"
    assert _full_signature(shown[0]) == _full_signature(function)


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


# A milestone label ("M4", "M6 part 2") dates a change rather than describing
# it, and it goes stale: README.md said "Milestone 7" while the tool was at M9.
# docs/roadmap.md is the one page where the labels are the subject, and
# CHANGELOG.md is generated by `cz bump` from pre-rename commit subjects.
# CLAUDE.md is an agent instruction file, not documentation, and is out of scope.
# `[0-9]+`, not `[0-9]`: a word boundary after a single digit lets a two-digit
# label through, so the pattern would stop catching the labels exactly when the
# project reached M10 -- the next one it exists to catch.
MILESTONE_RE = re.compile(r"\bM[0-9]+\b|milestone", re.IGNORECASE)

MILESTONE_ALLOWED = {"roadmap.md"}


def _milestone_hits(path: Path) -> list[str]:
    """Every line in `path` that carries a milestone label."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        f"{path.name}:{number}: {line.strip()}"
        for number, line in enumerate(lines, start=1)
        if MILESTONE_RE.search(line)
    ]


def test_no_milestone_labels_outside_the_roadmap():
    paths = [REPO_ROOT / "ARCHITECTURE.md", REPO_ROOT / "README.md"]
    paths += [DOCS / name for name in sorted(_site_pages()) if name not in MILESTONE_ALLOWED]
    hits = [hit for path in paths for hit in _milestone_hits(path)]
    assert not hits, "milestone labels found:\n" + "\n".join(hits)
