"""Pin the messages quoted in docs/troubleshooting.md to the code that emits them.

The page is keyed by the message a user sees on screen, so its whole value is
that those strings are the ones src/skill_lens/ actually prints. A reword in
the source would leave the page quietly wrong, and a reader searching for the
text in front of them would find nothing.

Many messages are assembled at runtime, so each entry below is a *fixed
fragment*: text that appears verbatim both on the page and inside one string
literal of the module that emits it. A fragment never spans a runtime value.

The check reads string literals with `ast`, not raw source text, so a message
wrapped across lines by implicit concatenation still reads as one string, and
an f-string's `{...}` fields read as a marker no fragment can match.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from skill_lens.cases.checks import UNFILLED_SENTINEL
from skill_lens.cases.loader import _ASSERTION_FIELDS
from skill_lens.runners import preflight, tools
from skill_lens.runners.tools import BUILTIN_TOOL_NAMES, NO_RESPONSE_SCRIPTED
from skill_lens.skills.baseline import HISTORY_LIMIT
from skill_lens.skills.loader import SKILL_FILENAME

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "skill_lens"
PAGE = REPO_ROOT / "docs" / "troubleshooting.md"

# Stands in for an f-string's `{...}` field. No fragment contains it, so a
# fragment can only match text that is fixed in the source.
FIELD = "\x00"

# (fragment, module that emits it, relative to src/skill_lens/)
QUOTED: list[tuple[str, str]] = [
    # Exit code 2: an unknown assertion kind
    (" assertion #", "cases/loader.py"),
    (" has unknown kind ", "cases/loader.py"),
    (". Known kinds: ", "cases/loader.py"),
    ("unknown assertion kind: ", "evaluators/assertion.py"),
    # A malformed regex
    ("invalid regex pattern ", "evaluators/assertion.py"),
    # An unknown key
    (": case #", "cases/loader.py"),
    (" invalid (", "cases/loader.py"),
    # An unfilled scaffold, in an eval file and in a tool library
    (" still has the scaffold placeholder ", "cases/loader.py"),
    (". Fill it in -- an unfinished eval cannot say anything about the skill.", "cases/loader.py"),
    ("tool library ", "cases/tool_libraries.py"),
    (": tool #", "cases/tool_libraries.py"),
    (" still has the scaffold placeholder ", "cases/tool_libraries.py"),
    (". Fill it in -- an unfinished mock cannot stand in for anything.", "cases/tool_libraries.py"),
    # Other things that exit 2
    (" is not set, and model ", "runners/preflight.py"),
    (
        " needs it. Export it in your environment -- skill-lens never reads secrets "
        "from skill-lens.toml.",
        "runners/preflight.py",
    ),
    ("--min-delta requires --baseline none or --baseline previous", "cli.py"),
    (" is empty; name a model such as openai:gpt-4o-mini", "cli.py"),
    ("unknown judge: ", "cli.py"),
    # "no eval cases ran" and its four causes
    ("Skipped (no eval cases): ", "reporters/console.py"),
    ("Gate FAILED:", "reporters/console.py"),
    ("no eval cases ran: no skills were found", "gating.py"),
    (
        "no eval cases ran: all discovered skill(s) were skipped for having no eval cases: ",
        "gating.py",
    ),
    ("no eval cases ran: the --tag filter excluded every case for skill(s): ", "gating.py"),
    ("no eval cases ran: the --case filter matched no case for skill(s): ", "gating.py"),
    # A baseline that could not be resolved
    ("Baseline notes:", "reporters/console.py"),
    (": baseline unavailable — ", "comparison.py"),
    (" has no resolvable baseline: ", "gating.py"),
    ("git is not installed", "skills/baseline.py"),
    (" is not inside a git repository", "skills/baseline.py"),
    (" is not tracked by git", "skills/baseline.py"),
    ("cannot read ", "skills/baseline.py"),
    ("no earlier version of ", "skills/baseline.py"),
    (" found in the last ", "skills/baseline.py"),
    ("cannot archive commit ", "skills/baseline.py"),
    ("cannot extract the bundle at commit ", "skills/baseline.py"),
    # A token limit that was not evaluated
    ("token budget not evaluated: ", "evaluators/budget.py"),
    ("copilot did not report token usage", "runners/traces.py"),
    ("claude-code did not report token usage", "runners/traces.py"),
    (" product does not report token usage", "runners/product.py"),
    # A model with no price
    ("Total cost: not priced (see per-case cost_note in the JSON report)", "reporters/console.py"),
    ("some costs not priced (see per-case cost_note in the JSON report)", "reporters/console.py"),
    ("cost budget not evaluated: ", "evaluators/budget.py"),
    ("no price data for ", "runners/pricing.py"),
    ("genai-prices is not installed; cost not calculated", "runners/pricing.py"),
    (" product does not report cost", "runners/product.py"),
    ("copilot bills per premium request, not per token; ", "runners/traces.py"),
    (" premium request(s)", "runners/traces.py"),
    # NO_RESPONSE_SCRIPTED
    ("no response is scripted for ", "runners/tools.py"),
    (" with arguments ", "runners/tools.py"),
    # UndeclaredTool
    (" of skill ", "runners/preflight.py"),
    (" trajectory.", "runners/preflight.py"),
    (", which is not declared in this case's tools.", "runners/preflight.py"),
    (
        " Built-in workspace and bundle tools only exist in a case with a 'workspace:' block.",
        "runners/preflight.py",
    ),
    # A product runner that cannot run here
    (" is not on PATH; ", "runners/product.py"),
    ("install the product, or set [runners.", "runners/product.py"),
    ("] command in skill-lens.toml", "runners/product.py"),
    ("set [runners.cli] command in skill-lens.toml to a command on PATH", "runners/product.py"),
    ("exited with code ", "runners/product.py"),
    (" could not run: ", "runners/product.py"),
    (" declares tools:, which the ", "runners/product.py"),
    (" runner cannot provide -- ", "runners/product.py"),
    (
        " declares trajectory:, but the cli runner records no tool calls; "
        "use a preset (copilot, claude-code)",
        "runners/product.py",
    ),
    (
        " is mode: offered, but the cli runner cannot observe whether a skill was loaded; "
        "use a preset or mode: loaded",
        "runners/product.py",
    ),
    # A --model or --judge-model nothing reads
    (
        "--model is read by pydantic-ai and langchain only, and this run names neither; "
        "a product's model is set with "
        '[runners.<name>] args = ["--model", "..."] in skill-lens.toml',
        "cli.py",
    ),
    ('--judge-model is read by judge = "pydantic-ai" or "langchain" only; ', "cli.py"),
    ("this run's judge is ", "cli.py"),
    (
        "--base-url is read by pydantic-ai and langchain only, and this run names neither; "
        "a product reaches its own endpoint",
        "cli.py",
    ),
    # script_sandbox = "required" with no backend
    ('script_sandbox = "required" but no sandbox is available: ', "scripts.py"),
    ("sandbox-exec not found on PATH", "scripts.py"),
    ("bwrap not found on PATH", "scripts.py"),
    ("no sandbox backend on ", "scripts.py"),
    (" probe failed: ", "scripts.py"),
    (
        "temp directory path contains a double quote, which a sandbox-exec profile cannot express",
        "scripts.py",
    ),
    (", which is not on PATH", "scripts.py"),
    ("scripts: on, sandbox: ", "reporters/console.py"),
]


def _page() -> str:
    """The page with every run of whitespace collapsed to one space.

    Prose wraps a quoted message across lines; the message the user sees does
    not wrap. Collapsing whitespace makes both read the same.
    """
    return " ".join(PAGE.read_text(encoding="utf-8").split())


def _string_literals(module: Path) -> list[str]:
    """Every string literal in `module`, with f-string fields as FIELD.

    Implicit concatenation is already joined by the parser, so a message
    written across three source lines is one entry here.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            found.append(
                "".join(
                    part.value if isinstance(part, ast.Constant) else FIELD for part in node.values
                )
            )
    return found


@pytest.mark.parametrize(("fragment", "module"), QUOTED, ids=[f"{m}:{f!r}" for f, m in QUOTED])
def test_the_quoted_fragment_is_on_the_page(fragment: str, module: str):
    assert fragment in _page(), (
        f"docs/troubleshooting.md no longer quotes {fragment!r}. Update the page and this "
        "table together: the table is what keeps the page honest."
    )


@pytest.mark.parametrize(("fragment", "module"), QUOTED, ids=[f"{m}:{f!r}" for f, m in QUOTED])
def test_the_quoted_fragment_is_emitted_by_its_module(fragment: str, module: str):
    literals = _string_literals(SRC / module)
    assert any(fragment in literal for literal in literals), (
        f"src/skill_lens/{module} no longer emits {fragment!r}, but "
        "docs/troubleshooting.md still quotes it. A reader searching the page for the "
        "message on their screen will not find it."
    )


def test_the_values_the_code_fills_in_are_the_ones_the_page_shows():
    """Some parts of a message are constants rather than fixed text, so no
    string literal holds them. Each is checked against the constant itself."""
    page = _page()
    known = ", ".join(sorted(_ASSERTION_FIELDS))
    assert f"Known kinds: {known}." in page
    assert f"scaffold placeholder {UNFILLED_SENTINEL} at" in page
    assert f"no earlier version of {SKILL_FILENAME} found in the last {HISTORY_LIMIT} commits" in (
        page
    )
    for name in sorted(BUILTIN_TOOL_NAMES):
        assert f"`{name}`" in page, f"the UndeclaredTool section does not name `{name}`"


def test_the_section_headings_name_things_that_exist():
    """Two sections are headed by a code name a user may search the source for."""
    page = _page()
    for heading, owner in (
        ("## NO_RESPONSE_SCRIPTED", tools),
        ("## UndeclaredTool", preflight),
    ):
        assert heading in page, f"docs/troubleshooting.md has no {heading!r} section"
        name = heading.removeprefix("## ")
        assert hasattr(owner, name), f"{owner.__name__} no longer defines {name}"
    # The heading names the constant; the fixed text inside it is pinned above.
    assert NO_RESPONSE_SCRIPTED.startswith("no response is scripted for ")


FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
# The header row of a table whose first column holds messages, and its rows.
MESSAGE_TABLE_RE = re.compile(
    r"^\| (?:Message|Reason|Note) \|[^\n]*\n\|[- |]+\|\n((?:\|[^\n]*\n)+)", re.MULTILINE
)


def _quoted_messages() -> list[str]:
    """Every place the page shows a message: fenced blocks, and the first
    cell of each row in a Message / Reason / Note table."""
    text = PAGE.read_text(encoding="utf-8")
    blocks = [" ".join(block.split()) for block in FENCE_RE.findall(text)]
    cells = [
        row.split("|")[1].strip()
        for rows in MESSAGE_TABLE_RE.findall(text)
        for row in rows.splitlines()
    ]
    return blocks + cells


# Guards the parse, not the page. If the fence or table pattern stopped
# matching, the completeness test below would read no messages and pass on
# nothing. There were 18 blocks and 35 rows when this was written.
MINIMUM_QUOTED_MESSAGES = 40


def test_every_quoted_message_is_pinned():
    """A message added to the page without a row in QUOTED would be the one
    string on the page nothing keeps honest."""
    messages = _quoted_messages()
    assert len(messages) >= MINIMUM_QUOTED_MESSAGES, (
        f"read only {len(messages)} quoted message(s) from docs/troubleshooting.md, below "
        f"the floor of {MINIMUM_QUOTED_MESSAGES}; the parse in this file has probably broken"
    )
    fragments = [fragment.strip() for fragment, _ in QUOTED]
    unpinned = [message for message in messages if not any(f in message for f in fragments)]
    assert not unpinned, (
        "docs/troubleshooting.md quotes messages no entry in QUOTED covers:\n  "
        + "\n  ".join(unpinned)
    )
