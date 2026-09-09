# skill-eval M5 Part 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the project to `skill-lens`, then ship the automated release pipeline — a merge to `main` verifies, bumps, tags and publishes to PyPI with no stored credential — plus a manual cassette-refresh workflow.

**Architecture:** Two pull requests. **A** is a mechanical rename of every user-facing surface, guarded by a new test that fails if the old name survives outside the historical archive. **B** adds `release.yml` (three jobs: verify → release → publish, chained by `needs:` because a `GITHUB_TOKEN` tag push cannot start a second workflow) and `refresh-cassettes.yml`. Version pinning is delegated to Commitizen's `version_files` rather than a workflow step, so the tree at tag `vX.Y.Z` already installs `X.Y.Z`.

**Tech Stack:** Python 3.11+, uv, Commitizen, pytest, ruff, GitHub Actions, PyPI Trusted Publishing (OIDC), MkDocs Material.

**Spec:** `docs/superpowers/specs/2026-09-09-skill-eval-m5-part2-design.md`. Section references below (§N) point at it.

## Global Constraints

- **The historical archive is never renamed.** `docs/superpowers/` records what was decided when. Rewriting the name inside it would falsify the record. Every rename command below excludes it.
- **Three token families must survive the rename**, because they are not this project's name:
  - `skill-evaluator` (43 occurrences) — the GitHub repository, which is **not** being renamed.
  - `skill-evals` (12) — part of the shipped skill `writing-skill-evals`.
  - `skill-eval-m<digit>` and `skill-eval-design` — archive filenames referenced from `CLAUDE.md`.
- **`skill-eval-summary`, `skill-eval-report`, `skill-eval-junit`, `skill-eval-reports`, `skill-eval-cli` DO get renamed** — they are output filenames and example-workflow filenames, not other people's names.
- **Only two case forms exist**: `skill-eval` (273) and `skill_eval` (219). No capitalised or upper-case variant. Verified, not assumed.
- **Cassettes contain zero occurrences of the name**, so the rename cannot invalidate recorded provider traffic. Verified.
- **Exit codes are the CI contract:** gate pass `0`, gate fail `1`, user/authoring error `2`.
- **All file IO pins `encoding="utf-8"`.**
- **YAML is read through `skill_lens.yaml_loading.safe_load`**, never `yaml.safe_load`.
- **The pipeline test tier is offline, deterministic and free.** Every test in this plan passes with no network and no API key.
- **Conventional Commits are enforced** by a `commit-msg` hook (`cz check`). Every commit message below is already conventional — use it verbatim.
- **Line length is 100** (`ruff`, `line-length = 100`). Lint select is `E, F, I, UP, B`.
- **Docs ship with the change.** CI has `docs` and `docs-freshness` jobs and `tests/test_docs.py`.
- **The repository setup is already done** and verified: workflow permissions are `write`, the `pypi` environment exists, and the pending publisher for `skill-lens` is registered against `EmadMokhtar/skill-evaluator`, workflow `release.yml`, environment `pypi`. Only `OPENAI_API_KEY` is still unset, which blocks Task 7 alone.

---

# Part A — the rename (pull request A)

### Task 1: Rename every surface to `skill-lens`

**Files:**
- Rename: `src/skill_eval/` → `src/skill_lens/`
- Rename: `examples/ci/skill-eval.yml` → `examples/ci/skill-lens.yml`
- Rename: `examples/ci/skill-eval-cli.yml` → `examples/ci/skill-lens-cli.yml`
- Modify: every tracked file outside `docs/superpowers/` containing `skill-eval` or `skill_eval`
- Create: `tests/test_naming.py`
- Create: `scripts/rename_to_skill_lens.py` (deleted again in Task 3)

**Interfaces:**
- Consumes: nothing.
- Produces: the importable package `skill_lens`, the console command `skill-lens`, the config filename `skill-lens.toml` (`skill_lens.config.CONFIG_FILENAME`), and the distribution name `skill-lens` resolved by `skill_lens.__version__`.

- [ ] **Step 1: Write the failing guard test**

Create `tests/test_naming.py`:

```python
"""The old name must not survive the rename.

A rename done with a text substitution is only safe if something fails when it
misses a file. This test is that something. It deliberately reads the working
tree rather than the package, because most occurrences were in prose.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# docs/superpowers/ is a historical record of what was decided when. Renaming
# inside it would make the archive lie about the past.
EXCLUDED_DIRS = ("docs/superpowers/",)

# Tokens that merely start with the old name and are not this project's name.
ALLOWED = re.compile(r"skill-eval(?:uator|s\b|-m\d|-design)")

OLD_NAME = re.compile(r"skill[-_]eval")


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
        if not name.startswith(EXCLUDED_DIRS)
    ]


def test_the_old_name_survives_nowhere_outside_the_archive():
    offenders: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            remainder = ALLOWED.sub("", line)
            if OLD_NAME.search(remainder):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not offenders, "the old name survives in:\n" + "\n".join(offenders[:40])


def test_no_path_still_carries_the_old_name():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _tracked_files()
        if OLD_NAME.search(str(path.relative_to(REPO_ROOT)))
        and not ALLOWED.search(str(path.relative_to(REPO_ROOT)))
    ]
    assert not offenders, f"paths still carrying the old name: {offenders}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_naming.py -v`
Expected: both tests FAIL, listing hundreds of occurrences.

- [ ] **Step 3: Write the rename script**

Create `scripts/rename_to_skill_lens.py`. It protects the survivors with placeholders **before** substituting, which is what makes the order safe — a plain substitution would turn `skill-evaluator` into `skill-lensuator`.

```python
"""One-shot rename of skill-eval to skill-lens. Deleted once it has run.

Placeholders are substituted in first so that names which merely *start* with
the old name -- the GitHub repository skill-evaluator, the shipped skill
writing-skill-evals, and archive filenames -- come through untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Order matters: longest first, so skill-evaluator is claimed before skill-evals.
PROTECTED = [
    ("skill-evaluator", "\x00REPO\x00"),
    ("skill-evals", "\x00SKILLNAME\x00"),
    ("skill-eval-design", "\x00ARCHDESIGN\x00"),
    ("skill-eval-m0", "\x00ARCHM0\x00"),
    ("skill-eval-m1", "\x00ARCHM1\x00"),
    ("skill-eval-m2", "\x00ARCHM2\x00"),
    ("skill-eval-m3", "\x00ARCHM3\x00"),
    ("skill-eval-m4", "\x00ARCHM4\x00"),
    ("skill-eval-m5", "\x00ARCHM5\x00"),
]

RENAMES = [("skill-eval", "skill-lens"), ("skill_eval", "skill_lens")]

EXCLUDED_PREFIX = "docs/superpowers/"


def rewrite(text: str) -> str:
    for original, placeholder in PROTECTED:
        text = text.replace(original, placeholder)
    for old, new in RENAMES:
        text = text.replace(old, new)
    for original, placeholder in PROTECTED:
        text = text.replace(placeholder, original)
    return text


def main() -> None:
    listing = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    changed = 0
    for name in listing:
        if name.startswith(EXCLUDED_PREFIX):
            continue
        path = REPO_ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        rewritten = rewrite(text)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")
            changed += 1
    print(f"rewrote {changed} files")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Rename the paths, then the contents**

Paths move first so that `git ls-files` in the script already reports the new locations.

```bash
git mv src/skill_eval src/skill_lens
git mv examples/ci/skill-eval.yml examples/ci/skill-lens.yml
git mv examples/ci/skill-eval-cli.yml examples/ci/skill-lens-cli.yml
uv run python scripts/rename_to_skill_lens.py
```

- [ ] **Step 5: Regenerate the lock file**

`uv.lock` records the distribution name, which just changed in `pyproject.toml`.

```bash
uv lock && uv sync --all-extras --dev
```

- [ ] **Step 6: Verify the survivors really survived**

Run: `git diff -U0 | grep -E "^\+.*skill-(lensuator|lenss)" || echo "no mangled names"`
Expected: `no mangled names`. A hit here means the placeholder pass was skipped.

Run: `grep -rc "skill-evaluator" README.md docs/ci.md action.yml`
Expected: non-zero counts — the repository name is untouched.

- [ ] **Step 7: Run the guard test and the full suite**

```bash
uv run pytest tests/test_naming.py -v
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Expected: `tests/test_naming.py` PASSES, all 564 tests pass (562 existing plus the two new), lint and format clean.

- [ ] **Step 8: Verify the command and the version lookup resolve**

The distribution lookup in `src/skill_lens/__init__.py` fails *silently* — `PackageNotFoundError` is caught and `__version__` becomes `"0.0.0"` (§10). Check it directly rather than trusting the substitution.

```bash
uv run skill-lens --help
uv run python -c "import skill_lens; print(skill_lens.__version__)"
```

Expected: the help text renders, and the version prints `0.1.0` — **not** `0.0.0`.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor!: rename the project to skill-lens

PyPI refuses skill-eval: its typosquatting guard strips separators and
folds look-alike characters, which collapses the name onto the published
project skilleval. skills-eval is taken too, by a tool in the same
category.

Every surface moves together -- distribution, command, config file and
Python package -- so the invariant that the user-facing name is the same
everywhere stays true. The GitHub repository keeps its name.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Keep the breaking change inside 0.x, and pin the action to the release

**Files:**
- Modify: `pyproject.toml` (`[tool.commitizen]`)
- Modify: `action.yml` (the `install-spec` default)
- Create: `tests/test_release_config.py`

**Interfaces:**
- Consumes: `skill_lens.__version__` from Task 1.
- Produces: `[tool.commitizen] version_files` and `major_version_zero`, which Task 4's release job depends on; `action.yml`'s `install-spec` default pinned to the current version.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_release_config.py`:

```python
"""Guard the release configuration that nothing else would notice breaking.

The action's pinned version and the package version are two places that must
agree; a release that changes one and not the other ships an action installing
somebody else's version.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import skill_lens
from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
ACTION = REPO_ROOT / "action.yml"

VERSION_IN_SPEC = re.compile(r"skill-lens\[pydantic-ai\]==(?P<version>[\w.]+)")


def _commitizen() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["commitizen"]


def test_the_action_pins_the_current_version():
    default = safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]["install-spec"]["default"]
    match = VERSION_IN_SPEC.fullmatch(default)
    assert match, f"install-spec default {default!r} does not pin a version"
    assert match.group("version") == skill_lens.__version__


def test_every_file_spelling_a_version_is_bumped_with_it():
    """A version written into a file that cz bump does not rewrite goes stale
    at the first release, and nothing else would report it."""
    listed = {entry.split(":", 1)[0] for entry in _commitizen()["version_files"]}
    spellings = {
        "action.yml": VERSION_IN_SPEC,
        "README.md": re.compile(r"skill-evaluator@v[\w.]+"),
        "docs/ci.md": re.compile(r"skill-evaluator@v[\w.]+"),
    }
    for name, pattern in spellings.items():
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        if pattern.search(text):
            assert name in listed, f"{name} spells a version but is not in version_files"


def test_breaking_changes_stay_inside_zero_x():
    """M6 and M7 are still expected to change the eval file format, so a
    breaking change must not promote the project to 1.0."""
    assert _commitizen()["major_version_zero"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_release_config.py -v`
Expected: all three FAIL — `install-spec` has no `==`, and neither `version_files` nor `major_version_zero` exists.

- [ ] **Step 3: Add the Commitizen configuration**

In `pyproject.toml`, replace the `[tool.commitizen]` block with:

```toml
[tool.commitizen]
name = "cz_conventional_commits"
version_provider = "uv"
tag_format = "v$version"
update_changelog_on_bump = true
# Keep breaking changes inside 0.x. M6 and M7 still expect to change the eval
# file format, so a "!" commit must not declare the CLI surface stable.
major_version_zero = true
# Files that spell the version. cz bump rewrites them inside the bump commit
# itself, so the tree at tag vX.Y.Z already installs and documents X.Y.Z.
version_files = [
    "action.yml:skill-lens\\[pydantic-ai\\]==",
    "docs/ci.md:skill-evaluator@v",
    "README.md:skill-evaluator@v",
]
bump_message = "bump: version $current_version → $new_version [skip ci]"
```

- [ ] **Step 4: Pin the action's install-spec**

In `action.yml`, change the `install-spec` default:

```yaml
  install-spec:
    description: >-
      Passed verbatim to `uv tool install`. A PyPI name, a pinned version, a git ref
      (git+https://github.com/EmadMokhtar/skill-evaluator@main) or a local path.
      Pinned by default so the action and the CLI it installs cannot drift apart;
      cz bump rewrites this line on every release.
    default: skill-lens[pydantic-ai]==0.1.0
```

- [ ] **Step 5: Replace the `@v1` references with a real tag**

`v1` never existed. In both `README.md:148` and `docs/ci.md:35`, change `uses: EmadMokhtar/skill-evaluator@v1` to `uses: EmadMokhtar/skill-evaluator@v0.1.0`, and add this line beneath each block:

```markdown
Pin an exact tag. Until 1.0 a minor release may change behaviour, so there is no
floating `v0` tag to follow.
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_release_config.py tests/test_action.py tests/test_docs.py -v`
Expected: PASS.

- [ ] **Step 7: Verify a bump would do the right thing, without doing it**

`--dry-run` computes and prints the next version, changing nothing.

Run: `uv run cz bump --dry-run`
Expected: it prints a `0.x` version — **not** `1.0.0` — proving `major_version_zero` took effect.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml action.yml README.md docs/ci.md tests/test_release_config.py
git commit -m "build: pin the action to the released version and keep 0.x

version_files makes cz bump rewrite the action's install-spec and the
documented tag inside the bump commit, so the tree at a tag installs the
version that tag names. major_version_zero stops the breaking rename
from promoting the project to 1.0 while M6 and M7 still expect to change
the eval file format.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Document the rename and remove the scaffolding

**Files:**
- Delete: `scripts/rename_to_skill_lens.py`
- Modify: `CLAUDE.md`, `docs/roadmap.md`, `docs/index.md`
- Modify: `docs/superpowers/specs/2026-08-05-skill-eval-m5-design.md` (pointer only)

- [ ] **Step 1: Delete the one-shot script**

```bash
git rm scripts/rename_to_skill_lens.py
```

It has run once and can never be correct again. `tests/test_naming.py` is what keeps the rename true from here.

- [ ] **Step 2: Update the invariant in `CLAUDE.md`**

Replace the naming invariant bullet with:

```markdown
- **`skill_lens` (underscore) never appears in user-facing output.** The user-facing name is
  `skill-lens` everywhere: command, config file, distribution. The GitHub repository keeps its
  older name, `skill-evaluator`, so `uses: EmadMokhtar/skill-evaluator@vX.Y.Z` installing
  `skill-lens` is expected, not a mistake. `tests/test_naming.py` fails if the pre-rename name
  reappears outside `docs/superpowers/`, which is a historical archive and is never rewritten.
```

- [ ] **Step 3: Add a pointer to the superseded section**

At the top of §10 in `docs/superpowers/specs/2026-08-05-skill-eval-m5-design.md`, insert:

```markdown
> **Superseded by `2026-09-09-skill-eval-m5-part2-design.md`.** This section assumed a
> credential able to push to a protected `main`. The real constraint is different, and this
> milestone also renamed the project to `skill-lens`. Kept unedited as a record of what was
> believed on 2026-08-05.
```

- [ ] **Step 4: Note the rename in `docs/roadmap.md` and `docs/index.md`**

In `docs/roadmap.md`, under "What M5 part 1 shipped", add a new section:

```markdown
## The rename to skill-lens

`skill-eval` could not be registered on PyPI — the registry folds separators and look-alike
characters before comparing, which collapses it onto the existing project `skilleval`. The
distribution, the command, the config file and the Python package all moved to `skill-lens`
together. The GitHub repository keeps its name.
```

In `docs/index.md`, correct the install line to name `skill-lens`.

- [ ] **Step 5: Verify the docs build and the suite is green**

```bash
uv sync --group docs
uv run mkdocs build --strict
uv run pytest
```

Expected: strict build clean, all tests pass.

- [ ] **Step 6: Commit and open pull request A**

```bash
git add -A
git commit -m "docs: record the rename to skill-lens

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

Open the pull request with the title `refactor!: rename the project to skill-lens`. The title becomes the commit on `main` under squash-merge, so it is what `cz bump` will read.

---

# Part B — release automation (pull request B)

### Task 4: The verify and release jobs

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: `[tool.commitizen]` from Task 2.
- Produces: the `release` job's outputs `bumped` (`"true"` / `"false"`) and `version`, which Task 5's `publish` job reads; the `dist` artifact.

- [ ] **Step 1: Write the failing workflow-shape test**

Create `tests/test_release_workflow.py`:

```python
"""Assert the structure of release.yml that carries the design decisions.

A workflow cannot be executed by the test suite, so what is testable is its
shape. Each assertion below corresponds to a decision that is invisible once
made and expensive when silently removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_lens.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"


@pytest.fixture
def workflow() -> dict:
    return safe_load(RELEASE.read_text(encoding="utf-8"))


def test_nothing_runs_before_the_tests_pass(workflow):
    """Publishing is irreversible: PyPI refuses a re-upload of a version."""
    assert workflow["jobs"]["release"]["needs"] == "verify"
    assert workflow["jobs"]["publish"]["needs"] == "release"


def test_publish_is_skipped_when_no_version_was_cut(workflow):
    condition = workflow["jobs"]["publish"]["if"]
    assert "bumped" in condition and "true" in condition


def test_publish_can_mint_an_identity_but_cannot_write_to_the_repository(workflow):
    permissions = workflow["jobs"]["publish"]["permissions"]
    assert permissions["id-token"] == "write"
    assert permissions.get("contents", "read") == "read"


def test_publish_is_gated_by_the_protected_environment(workflow):
    """The pending publisher on PyPI is bound to this environment name."""
    assert workflow["jobs"]["publish"]["environment"]["name"] == "pypi"


def test_only_the_release_job_may_write_to_the_repository(workflow):
    assert workflow["jobs"]["release"]["permissions"]["contents"] == "write"
    assert workflow["jobs"]["verify"]["permissions"]["contents"] == "read"


def test_releases_are_serialised_and_never_cancelled(workflow):
    """Cancelling a release halfway can leave a tag pushed and nothing built."""
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_the_bump_reads_the_whole_history(workflow):
    """cz bump computes the increment from every commit since the last tag."""
    checkout = next(
        step for step in workflow["jobs"]["release"]["steps"] if "checkout" in str(step.get("uses"))
    )
    assert checkout["with"]["fetch-depth"] == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_release_workflow.py -v`
Expected: every test FAILS with `FileNotFoundError` — the workflow does not exist.

- [ ] **Step 3: Write the verify and release jobs**

Create `.github/workflows/release.yml`. The `publish` job is added in Task 5; the tests referencing it still fail after this step, which is expected.

```yaml
name: Release

on:
  push:
    branches: [main]

# A release must never be cancelled halfway: the tag may already be pushed
# while the build is not. Queue instead.
concurrency:
  group: release
  cancel-in-progress: false

# Nothing by default; each job asks for exactly what it uses.
permissions: {}

jobs:
  verify:
    # ci.yml runs on this same push, so without this job the release would race
    # it and could tag a commit whose tests are still running, or already red.
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --all-extras --dev

      - name: Lint
        run: uv run ruff check .

      - name: Check formatting
        run: uv run ruff format --check .

      - name: Test
        run: uv run pytest

  release:
    needs: verify
    runs-on: ubuntu-latest
    permissions:
      contents: write
    outputs:
      bumped: ${{ steps.bump.outputs.bumped }}
      version: ${{ steps.bump.outputs.version }}
    steps:
      - uses: actions/checkout@v4
        with:
          # cz bump reads every commit since the last tag to compute the
          # increment, so a shallow clone would under-report the change.
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --all-extras --dev

      - name: Configure the committer
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

      - name: Bump the version
        id: bump
        shell: bash
        run: |
          set -uo pipefail
          # `set -e` is deliberately not used: cz signals "nothing to release"
          # through its exit code, and -e would discard it before we can read it.
          code=0
          uv run cz bump --yes || code=$?
          # 21 = NoneIncrementExit (commits exist, none warrant a release).
          # 3  = NoCommitsFoundError (no commits since the last tag at all).
          if [ "$code" = "21" ] || [ "$code" = "3" ]; then
            echo "bumped=false" >> "$GITHUB_OUTPUT"
            echo "No commit since the last tag warrants a release." >> "$GITHUB_STEP_SUMMARY"
            exit 0
          fi
          if [ "$code" != "0" ]; then
            echo "cz bump failed with exit code $code" >&2
            exit "$code"
          fi
          version="$(uv run cz version --project)"
          echo "bumped=true" >> "$GITHUB_OUTPUT"
          echo "version=$version" >> "$GITHUB_OUTPUT"
          echo "Released version $version." >> "$GITHUB_STEP_SUMMARY"

      - name: Push the bump commit and its tag
        if: steps.bump.outputs.bumped == 'true'
        run: git push --follow-tags origin HEAD:main

      - name: Build the distributions
        if: steps.bump.outputs.bumped == 'true'
        run: uv build

      - name: Upload the distributions
        if: steps.bump.outputs.bumped == 'true'
        uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/
          if-no-files-found: error
```

- [ ] **Step 4: Run the tests that can pass now**

Run: `uv run pytest tests/test_release_workflow.py -v`
Expected: `test_only_the_release_job_may_write_to_the_repository`, `test_releases_are_serialised_and_never_cancelled` and `test_the_bump_reads_the_whole_history` PASS. The four `publish` tests still FAIL with `KeyError: 'publish'`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml tests/test_release_workflow.py
git commit -m "ci: verify and bump the version on every merge to main

The release runs the suite itself rather than trusting the parallel CI
run on the same push, because publishing is irreversible. cz bump
signals 'nothing to release' with exit code 21 or 3, so the step reads
the code instead of letting set -e discard it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The publish job

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `needs.release.outputs.bumped` and the `dist` artifact from Task 4.
- Produces: nothing another task reads.

- [ ] **Step 1: Confirm the four publish tests still fail**

Run: `uv run pytest tests/test_release_workflow.py -v -k publish`
Expected: FAIL with `KeyError: 'publish'`.

- [ ] **Step 2: Append the publish job**

Add to `.github/workflows/release.yml`:

```yaml
  publish:
    needs: release
    if: needs.release.outputs.bumped == 'true'
    runs-on: ubuntu-latest
    permissions:
      # Trusted Publishing proves this job's identity to PyPI with a
      # short-lived signed token. No stored API token exists anywhere.
      id-token: write
      contents: read
    environment:
      name: pypi
      url: https://pypi.org/p/skill-lens
    steps:
      - name: Download the distributions
        # The artifact the release job built and verified. Rebuilding here
        # would publish bytes that nothing tested.
        uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 3: Run the whole workflow test file**

Run: `uv run pytest tests/test_release_workflow.py -v`
Expected: all seven tests PASS.

- [ ] **Step 4: Run the full suite and lint**

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: publish to PyPI with trusted publishing

Runs in the same workflow rather than on a tag push: GitHub does not
start new workflow runs from GITHUB_TOKEN pushes, so a tag-triggered
publish would never fire and the release would silently ship nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The cassette-refresh workflow

**Files:**
- Create: `.github/workflows/refresh-cassettes.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Refresh cassettes

# Manual only. Re-recording spends money and needs a real API key, so it is
# never part of the normal CI path.
on: workflow_dispatch

permissions: {}

jobs:
  refresh:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --all-extras --dev

      - name: Re-record
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: uv run pytest tests/test_cassettes.py --record-mode=once

      - name: Prove the new recordings replay
        # A recording that cannot be replayed is worthless, and a reviewer
        # reading YAML would not notice. Catch it here.
        run: uv run pytest tests/test_cassettes.py --record-mode=none

      - name: Refuse to push a secret
        # Scrubbing already runs on both sides of the exchange in
        # tests/conftest.py. This is a second lock on a locked door: the
        # replay tier's whole value rests on cassettes being secret-free.
        run: |
          set -euo pipefail
          if git diff -- tests/cassettes | grep -nE "sk-[A-Za-z0-9]|Bearer [A-Za-z0-9]"; then
            echo "A credential appears in the re-recorded cassettes. Refusing to push." >&2
            exit 1
          fi

      - name: Push a branch for review
        shell: bash
        run: |
          set -euo pipefail
          if git diff --quiet -- tests/cassettes; then
            echo "The cassettes already match the provider. Nothing to review." \
              >> "$GITHUB_STEP_SUMMARY"
            exit 0
          fi
          branch="chore/refresh-cassettes-${{ github.run_id }}"
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git checkout -b "$branch"
          git add tests/cassettes
          git commit -m "test: refresh the recorded provider traffic"
          git push origin "$branch"
          # A pull request opened with GITHUB_TOKEN gets no CI checks, and for
          # re-recorded cassettes those checks are the point. A human opening
          # it is what starts them.
          {
            echo "### Cassettes refreshed"
            echo
            echo "Open the pull request to run CI on the new recordings:"
            echo
            echo "${{ github.server_url }}/${{ github.repository }}/compare/${branch}?expand=1"
          } >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 2: Check the YAML parses**

Run: `uv run python -c "from skill_lens.yaml_loading import safe_load; from pathlib import Path; safe_load(Path('.github/workflows/refresh-cassettes.yml').read_text(encoding='utf-8')); print('parsed')"`
Expected: `parsed`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/refresh-cassettes.yml
git commit -m "ci: add a manual cassette refresh workflow

Pushes a branch rather than opening a pull request: a pull request
opened with GITHUB_TOKEN gets no CI checks, and for re-recorded
cassettes those checks are the whole value of the review.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Document releasing

**Files:**
- Create: `docs/releasing.md`
- Modify: `mkdocs.yml` (`nav:`), `docs/roadmap.md`, `CLAUDE.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing docs test**

Add to `tests/test_docs.py`:

```python
RELEASING = REPO_ROOT / "docs" / "releasing.md"


def test_releasing_documents_every_piece_of_external_setup():
    """The four settings live outside this repository, so nothing in CI can
    check them. The docs are the only place they are recorded."""
    text = RELEASING.read_text(encoding="utf-8")
    for required in ("Read and write", "pending publisher", "pypi", "OPENAI_API_KEY"):
        assert required in text, f"docs/releasing.md does not mention {required!r}"


def test_releasing_is_in_the_nav():
    nav = safe_load((REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    assert "releasing.md" in str(nav)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_docs.py -k releasing -v`
Expected: FAIL — the file does not exist.

- [ ] **Step 3: Write `docs/releasing.md`**

```markdown
# Releasing

Releases are automatic. Merging to `main` runs `.github/workflows/release.yml`, which
verifies the commit, works out the next version from the commit history, tags it, and
publishes to PyPI. Nobody types a release command.

## What happens on a merge to main

| Job | Does | Runs when |
| --- | --- | --- |
| `verify` | ruff, format check, the full offline suite | every push to `main` |
| `release` | `cz bump`, push the commit and tag, `uv build`, upload the artifact | `verify` passed |
| `publish` | download the artifact, upload to PyPI | a version was actually cut |

A merge whose commits do not warrant a release is a no-op: `cz bump` exits 21 (or 3), the
job records "nothing to release" in its summary, and `publish` is skipped.

The version comes from the commit messages, so a Conventional Commit title is not a style
rule here — it is the input to versioning. `fix:` gives a patch, `feat:` a minor, and a `!`
or a `BREAKING CHANGE:` footer a breaking change. Because `major_version_zero = true`, a
breaking change stays inside `0.x` rather than promoting the project to `1.0.0`.

## One-time setup

None of this can live in a workflow file. All four are already in place; they are recorded
here so they can be restored, and so a fork knows what to configure.

1. **Settings → Actions → General → Workflow permissions → Read and write.** This setting is
   a ceiling: with it at read-only, a job requesting `contents: write` silently gets
   read-only and only the final `git push` fails.
2. **A `pypi` GitHub Environment.** The publish job runs inside it, and the PyPI publisher is
   bound to its name. Add required reviewers to make every publish wait for approval.
3. **A pending publisher on PyPI** (Publishing → Add a new pending publisher → GitHub):
   project `skill-lens`, owner `EmadMokhtar`, repository `skill-evaluator`, workflow
   `release.yml`, environment `pypi`. This is Trusted Publishing: PyPI accepts the upload
   because the job proves its identity with a short-lived signed token, so no API token
   exists to store, leak or rotate.
4. **An `OPENAI_API_KEY` secret**, used only by the manual cassette refresh below.

A pending publisher does **not** reserve the name. Until the first release creates the
project, anyone else may register `skill-lens`.

## Pinning the action

`cz bump` rewrites the version wherever it is spelled — `action.yml`'s `install-spec` default
and the documented `uses:` tag — inside the bump commit itself. The tree at tag `vX.Y.Z`
therefore installs exactly `X.Y.Z`, and the action reference cannot drift from the tool.

Pin an exact tag. There is no floating `v0` tag: under SemVer a `0.x` minor release may
change behaviour, so a moving tag would carry you across a breaking change without warning.
A moving major tag starts at 1.0, when the promise behind it becomes true.

## Refreshing the cassettes

The replay test tier runs against recorded provider traffic. When the provider's responses
change, run **Actions → Refresh cassettes → Run workflow**. It re-records, proves the new
recordings replay, refuses to continue if a credential appears in them, and pushes a branch.

Open the pull request yourself from the link in the job summary. A pull request opened by the
workflow would carry no CI checks, and on a cassette refresh those checks are the review.

## Releasing by hand

Rarely needed. `uv run cz bump` locally, push the commit and tag, then re-run the `publish`
job. Note that pushing a tag does not by itself publish: the publish job is reached through
`needs:`, not through a tag trigger.
```

- [ ] **Step 4: Add it to the nav**

In `mkdocs.yml`, add `Releasing: releasing.md` to the `Reference` section, after `CI integration: ci.md`.

- [ ] **Step 5: Update `docs/roadmap.md` and `CLAUDE.md`**

In `docs/roadmap.md`, change the M5 row's status to `shipped`, and replace the closing paragraph of "What M5 part 1 shipped" with:

```markdown
## What M5 part 2 shipped

A merge to `main` now verifies the commit, bumps the version from the commit history, tags
it, and publishes to PyPI over Trusted Publishing — no stored credential anywhere. A manual
workflow refreshes the recorded provider traffic and hands it back as a branch to review.
See [Releasing](releasing.md).
```

In `CLAUDE.md`, update the milestone paragraph to say M5 is complete, and add these invariants:

```markdown
- **Nothing publishes that has not been verified in the same run.** `publish` is reachable
  only through `needs:` on a green `verify`; publishing is irreversible, since PyPI refuses a
  re-upload of a version that already exists.
- **A merge with no releasable commit publishes nothing and fails nothing.** `cz bump` exit
  codes 21 and 3 are no-ops, not errors.
- **The version in `action.yml` always equals the package version**, and every file spelling a
  version is listed in `version_files`. Both are asserted by `tests/test_release_config.py`.
- **No long-lived publishing credential exists.** Trusted Publishing only.
- **A cassette refresh proves the new recordings replay, and checks them for secrets, before
  pushing** — and never opens a pull request that CI has not run on.
```

- [ ] **Step 6: Verify**

```bash
uv run pytest tests/test_docs.py -v
uv sync --group docs && uv run mkdocs build --strict
uv run pytest
```

Expected: all green.

- [ ] **Step 7: Commit and open pull request B**

```bash
git add -A
git commit -m "docs: document the release process and its external setup

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

Open the pull request with the title `ci: release to PyPI automatically on merge to main`.

---

### Task 8: Verify the first real release

Runs after pull request B merges. Nothing here is a code change; it is the check that the
pipeline did what the plan says, on the one run that cannot be rehearsed offline.

- [ ] **Step 1: Watch the release run**

Run: `gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId')`
Expected: `verify` → `release` → `publish` all green.

- [ ] **Step 2: Confirm the version landed everywhere it is spelled**

```bash
git fetch --tags && git pull --ff-only
git log --oneline -2 && git describe --tags --abbrev=0
grep -n "install-spec" -A4 action.yml | grep "=="
grep -rn "skill-evaluator@v" README.md docs/ci.md
```

Expected: a `bump:` commit on `main`, a `vX.Y.Z` tag, and the identical version in all three
files. A mismatch means `version_files` missed a file — add it and let the next release fix
it, rather than editing the released tag.

- [ ] **Step 3: Confirm the package is installable**

Read the released version from the tag first, then install exactly it — do not type a version
from memory, since the whole point is to check that what the tag names is what PyPI has.

```bash
VERSION="$(git describe --tags --abbrev=0 | sed 's/^v//')"
uv tool install --force "skill-lens[pydantic-ai]==${VERSION}"
skill-lens --help
```

Expected: installs from PyPI and the help text renders.

- [ ] **Step 4: Confirm a no-op merge really is a no-op**

After the next `docs:`-only or `chore:`-only merge, check that the `release` job summary reads
"No commit since the last tag warrants a release" and `publish` was skipped.
