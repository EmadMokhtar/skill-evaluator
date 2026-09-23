"""Pin CLAUDE.md's condensed invariant list to docs/invariants.md.

The duplication is deliberate. CLAUDE.md is loaded into the agent's context
at the start of every session, so an invariant that lives only on the docs
page is one the agent does not know until it reads the file. What is not
acceptable is the two copies drifting apart, which is what this pins.

Matching is exact: a CLAUDE.md bullet's bold lead must equal an
invariants.md heading byte for byte. No normalisation, so no false pass.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INVARIANTS = REPO_ROOT / "docs" / "invariants.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

CLAUDE_SECTION = "## Invariants that are easy to break"

# "### A run executing zero cases fails the gate"
HEADING_RE = re.compile(r"^### (.+)$", re.MULTILINE)
# "- **A run executing zero cases fails the gate.** ..." -- the bold lead only.
# DOTALL so a bold lead that wraps across two lines is still one claim; the
# whitespace is normalised below, so the wrap does not change what it reads.
BULLET_RE = re.compile(r"^- \*\*(.+?)\.?\*\*", re.MULTILINE | re.DOTALL)


def _page_claims() -> list[str]:
    text = INVARIANTS.read_text(encoding="utf-8")
    return [match.strip() for match in HEADING_RE.findall(text)]


def _invariants_section() -> str:
    """The invariant list only.

    CLAUDE.md has bold-led bullets in other sections -- the three protocols
    under Architecture, the rules under Conventions -- that are not invariants
    and have no heading on the page. Reading the whole file would demand they
    be reworded or moved, which is a change to a part of CLAUDE.md this test
    has no opinion about.
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    start = text.index(CLAUDE_SECTION)
    end = text.find("\n## ", start + len(CLAUDE_SECTION))
    return text[start:] if end == -1 else text[start:end]


def _claude_claims() -> list[str]:
    return [" ".join(match.split()) for match in BULLET_RE.findall(_invariants_section())]


def test_every_documented_invariant_is_in_claude_md():
    missing = sorted(set(_page_claims()) - set(_claude_claims()))
    assert not missing, "docs/invariants.md headings with no CLAUDE.md bullet:\n  " + "\n  ".join(
        missing
    )


def test_every_claude_md_invariant_is_documented():
    extra = sorted(set(_claude_claims()) - set(_page_claims()))
    assert not extra, "CLAUDE.md bullets with no docs/invariants.md heading:\n  " + "\n  ".join(
        extra
    )


def test_neither_side_repeats_a_claim():
    for name, claims in (("docs/invariants.md", _page_claims()), ("CLAUDE.md", _claude_claims())):
        duplicates = sorted({claim for claim in claims if claims.count(claim) > 1})
        assert not duplicates, f"{name} states the same claim twice:\n  " + "\n  ".join(duplicates)


# The three tests above all compare set differences, so two *empty* lists
# satisfy every one of them. This floor is what stops that.
#
# It guards the parse, not the count. If a heading level changes, if
# CLAUDE_SECTION is renamed, or if either regex stops matching, both sides
# collapse to nothing, all three differences are empty, and the suite reports
# that the two copies agree -- because neither was read. That is the vacuous
# pass this project rejects everywhere else: a run executing zero cases fails
# the gate, an unscripted judge errors rather than passing, and the test
# guarding those rules must not be the one place that goes green on nothing.
#
# There were 144 invariants when this floor was written and the list only
# grows, so a floor well under that never needs revisiting. Falling below it
# means either a broken parse or someone deleting a third of the project's
# decided behaviours -- and both of those should be looked at.
MINIMUM_INVARIANTS = 100


def test_neither_side_parses_to_nothing():
    for name, claims in (("docs/invariants.md", _page_claims()), ("CLAUDE.md", _claude_claims())):
        assert len(claims) >= MINIMUM_INVARIANTS, (
            f"{name} yielded only {len(claims)} invariant(s), below the floor of "
            f"{MINIMUM_INVARIANTS}. Either the list shrank drastically, or this file's "
            "parse broke and the drift tests above are passing on nothing."
        )
