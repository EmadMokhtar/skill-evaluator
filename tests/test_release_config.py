"""Guard the release configuration that nothing else would notice breaking.

The action's pinned version and the package version are two places that must
agree; a release that changes one and not the other ships an action installing
somebody else's version.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import skill_lens
from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
ACTION = REPO_ROOT / "action.yml"

# Any extras combination counts: a pin spelled `skill-lens[pydantic-ai,langchain]==`
# goes just as stale as the single-extra one if no pattern rewrites it.
VERSION_IN_SPEC = re.compile(r"skill-lens\[[\w,-]+\]==(?P<version>[\w.]+)")

# docs/superpowers/ is a historical archive of specs and plans (see
# tests/test_naming.py, which excludes it for the same reason): a version
# spelled out there is a record of what was true when it was written, not a
# live file cz bump should keep current.
EXCLUDED_DIRS = ("docs/superpowers/",)

# pyproject.toml is where the version-spelling patterns themselves are
# configured, as the `version_files` regex strings below -- it contains the
# patterns as configuration, not as a version to bump, so it is excluded by
# exact path rather than folded into a broader rule that could hide a real
# offender elsewhere.
EXCLUDED_FILES = ("pyproject.toml",)

# The two ways a tracked file spells a version that a release must keep in
# sync: a reference to the published action, and a pinned install spec.
VERSION_SPELLINGS = {
    "action reference": re.compile(r"EmadMokhtar/skill-evaluator@v(?P<version>[\w.]+)"),
    "pinned install spec": VERSION_IN_SPEC,
}


def _commitizen() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["commitizen"]


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [
        REPO_ROOT / name
        for name in out
        if not name.startswith(EXCLUDED_DIRS) and name not in EXCLUDED_FILES
    ]


def _version_file_patterns() -> dict[str, list[re.Pattern]]:
    """`version_files`, as {file: [compiled pattern, ...]}, in the order cz bump
    applies them.

    Each entry is `path:regex`, and cz bump gates *per line* on that regex: it
    rewrites the version only on lines the pattern matches, leaving every other
    line alone. So coverage is a property of the (file, pattern) pair, not of
    the file -- one file can appear under several patterns, and a line matched
    by none of them is never bumped even though its file is listed.

    The order is Commitizen's, not the config file's: `_resolve_files_and_regexes`
    in `commitizen/bump.py` sorts the `(path, regex)` pairs as strings. The order
    matters because the file is written back after every pair, so a later
    pattern sees the lines an earlier one already rewrote.
    """
    pairs = sorted(entry.partition(":")[::2] for entry in _commitizen()["version_files"])
    patterns: dict[str, list[re.Pattern]] = {}
    for name, regex in pairs:
        patterns.setdefault(name, []).append(re.compile(regex))
    return patterns


def test_the_action_pins_the_current_version():
    default = safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]["install-spec"]["default"]
    match = VERSION_IN_SPEC.fullmatch(default)
    assert match, f"install-spec default {default!r} does not pin a version"
    assert match.group("version") == skill_lens.__version__


def test_every_file_spelling_a_version_is_bumped_with_it():
    """A version written into a file that cz bump does not rewrite goes stale
    at the first release, and nothing else would report it.

    This walks every tracked file (as tests/test_naming.py does) looking for
    the two ways a file spells a version -- an action reference
    (`EmadMokhtar/skill-evaluator@v<version>`) or a pinned install spec
    (`skill-lens[<extras>]==<version>`) -- rather than checking a fixed
    list of filenames, so a new file that starts spelling a version is caught
    the moment it exists instead of being invisible to this test forever.

    The check is per line, and mirrors what cz bump itself does: a spelling is
    covered only when one of *its own file's* `version_files` patterns matches
    the same line. Treating a listed filename as blanket coverage would hide a
    second spelling added to an already-listed file -- exactly the case where
    the file looks protected and is not.
    """
    listed = _version_file_patterns()
    offenders = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        name = str(path.relative_to(REPO_ROOT))
        covered = listed.get(name, [])
        for number, line in enumerate(text.splitlines(), start=1):
            for label, spelling in VERSION_SPELLINGS.items():
                if not spelling.search(line):
                    continue
                if any(pattern.search(line) for pattern in covered):
                    continue
                offenders.append(f"{name}:{number} ({label})")
    assert not offenders, (
        "spells a version on a line no version_files pattern rewrites: " + ", ".join(offenders)
    )


def test_every_version_files_entry_still_matches_a_line_carrying_the_version():
    """The converse of the test above, and the half that fails *late*.

    That test asks "does every spelling have a pattern?". This one asks "does
    every pattern still have a spelling?" -- because a pattern that matches
    nothing is not an error to cz bump by default. It rewrites the file
    unchanged and says nothing, which is why the release job passes
    `--check-consistency`.

    But `--check-consistency` only fires during a release, on `main`, after
    the pull request that broke the pattern has already merged. The job then
    aborts with exit 17 before writing, committing or tagging -- so nothing
    ships broken -- yet the release is lost until someone repairs the pattern
    and merges again. Asserting it here moves that failure onto the pull
    request that causes it, where it costs a line of feedback instead of a
    skipped release.

    The rule is cz bump's own algorithm (`update_version_in_files` in
    `commitizen/bump.py`), replayed in memory rather than approximated: for
    each `(file, pattern)` pair, in Commitizen's sorted order, the current
    version is `str.replace`d on every line the pattern matches, the pair
    counts only if that changed at least one line, and the *rewritten* lines
    are what the next pattern for the same file sees.

    Replaying the rewrite is what makes the last part visible. Checking each
    pattern against the original file passed a line that carried two pinned
    install specs -- the `[pydantic-ai]` one beside the
    `[pydantic-ai,langchain]` one -- because both patterns matched it and it
    held the version. But a line has one version to give: the first pattern
    to run replaced it, the second found the line already bumped and nothing
    else of its own, and the release aborted with exit 17. Every version
    spelling needs a line of its own.
    """
    current = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    # Any value will do as long as it does not contain the current version: a
    # replacement that did would count a line already rewritten as rewritten again.
    bumped = "<bumped>"
    unmatched = []
    for name, patterns in _version_file_patterns().items():
        lines = (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
        for pattern in patterns:
            rewritten = [
                line.replace(current, bumped) if pattern.search(line) else line for line in lines
            ]
            if rewritten == lines:
                unmatched.append(f"{name} :: {pattern.pattern}")
            lines = rewritten
    assert not unmatched, (
        f"version_files patterns that rewrite no line carrying the current version {current!r} "
        "once the patterns before them have run, so cz bump would silently leave the file alone "
        "(and --check-consistency will abort the release): " + ", ".join(unmatched)
    )


def test_every_spelled_version_is_the_current_version():
    """A version spelled in a tracked file is the released version, or it is
    a mistake.

    The two tests above prove every spelling has a pattern and every pattern
    has a line -- and both hold for a line pinning an *older* version, as long
    as some other line matched by the same pattern carries the current one.
    cz bump then rewrites the current lines and leaves the stale one exactly
    as it is, release after release, with nothing to report it: a reader
    copies a pin to a version that predates the feature the example shows.

    That is how a branch cut before a release and merged after it reads: the
    bump on `main` rewrote the lines that existed then, and the lines the
    branch added still spell the version it was cut from. The test cannot
    run on the merged tree before the merge, but it fails the first run on
    `main` afterwards -- in `verify`, before `release` -- naming the line.
    """
    current = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    stale = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        name = str(path.relative_to(REPO_ROOT))
        for number, line in enumerate(text.splitlines(), start=1):
            for label, spelling in VERSION_SPELLINGS.items():
                for match in spelling.finditer(line):
                    if match.group("version") != current:
                        stale.append(f"{name}:{number} ({label} {match.group('version')})")
    assert not stale, f"spells a version other than the current {current!r}: " + ", ".join(stale)


def test_breaking_changes_stay_inside_zero_x():
    """M6 and M7 are still expected to change the eval file format, so a
    breaking change must not promote the project to 1.0."""
    assert _commitizen()["major_version_zero"] is True


def test_the_tag_is_annotated_and_spelled_the_way_the_workflow_expects():
    """Two settings release.yml leans on, neither of which fails visibly.

    `annotated_tag = true`: `git push --follow-tags` pushes only *annotated*
    tags. Commitizen creates a lightweight one by default, which that push
    would skip while still exiting 0 -- the bump commit reaches main and its
    tag never leaves the runner.

    `tag_format = "v$version"`: release.yml builds the ref it verifies as
    `tag="v${{ steps.bump.outputs.version }}"`, hardcoding the `v` this
    setting configures. Nothing links the two at runtime, so this assertion is
    the link: changing the format has to break here and be changed on purpose,
    in both places.
    """
    settings = _commitizen()
    assert settings["annotated_tag"] is True
    assert settings["tag_format"] == "v$version"
