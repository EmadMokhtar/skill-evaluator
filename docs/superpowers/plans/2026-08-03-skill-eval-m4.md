# skill-eval M4 — Comparative Evals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run every eval case in two arms — with the skill (candidate) and without it or against its previous version (baseline) — optionally sampled N times, and report the paired difference so CI can require that a `SKILL.md` edit actually improved something.

**Architecture:** The orchestrator gains an arm loop and a repeat loop; `CaseOutcome` records which arm and which repetition it came from. A new `skills/baseline.py` resolves "the previous version" from git, driven by the skill's declared `version:`. A new `comparison.py` turns the two-armed `RunReport` into a `Delta` — pass-rate/token/cost/latency differences, plus low-signal checks and high-variance cases. Gating learns `min_delta`. Everything is additive: absent `--baseline`, the tool behaves exactly as it does today.

**Tech Stack:** Python 3.12+, Pydantic v2, Typer, pytest, uv, ruff. `git` via `subprocess` (no new dependency). PydanticAI stays confined to `runners/pydantic_ai.py` and `judges/pydantic_ai.py`.

**Spec:** `docs/superpowers/specs/2026-08-03-skill-eval-m4-design.md`

## Global Constraints

These apply to every task. They are decided behaviors, not preferences — each has a test.

- **`errored` ≠ `failed`.** `failed` = ran and scored below bar. `errored` = the runner or an evaluator blew up. Runners and judges never raise for provider failures; `resolve_previous` never raises for environmental ones (missing git, no repo, untracked file) — it **returns** `BaselineUnavailable`.
- **Absent `--baseline`, behavior is byte-identical to M3** — one arm, no delta, unchanged console output.
- **Baseline outcomes never count toward the gate.** `RunReport.total/passed/failed/errored/pass_rate/pass_rate_by_skill` read the candidate arm only.
- **The baseline arm never receives the skill's name, description or instructions** under `--baseline none`.
- **The delta is paired.** A case excluded from one arm is excluded from both.
- **A gate that verified nothing fails.** `--min-delta` with no comparable case fails, exactly as a run executing zero cases does.
- **Low-signal and high-variance never change the exit code.**
- **Authoring errors abort the run and exit 2.** A missing git history is *not* an authoring error.
- **Exit codes are the CI contract:** gate pass `0`, gate fail `1`, user/authoring error `2`.
- **`extra="forbid"`** on every user-authored model (`EvalCase`, `AssertionSpec`, `ToolSpec`, `TrajectorySpec`, `BudgetSpec`, `JudgeSpec`, `Config`, `RunResult`).
- **All file IO pins `encoding="utf-8"`**; subprocess output is decoded as UTF-8 with `errors="replace"`.
- **YAML goes through `skill_eval.yaml_loading.safe_load`**, never `yaml.safe_load`.
- **Secrets come from environment variables only** — never from `skill-eval.toml`.
- **`skill_eval` (underscore) never appears in user-facing output.** The user-facing name is `skill-eval`.
- **No agent-framework type outside `runners/pydantic_ai.py` and `judges/pydantic_ai.py`.** `skills/baseline.py` shells out to `git` and imports neither. `tests/test_framework_isolation.py` guards this.
- **TDD, always.** Write the failing test, watch it fail for the right reason, implement the minimum, watch it pass, commit.
- **Conventional Commits are enforced** by a `commit-msg` hook (`cz check`). Every commit message must match `<type>[scope]: <imperative, lowercase, no trailing period>`.
- **Zero-cost test tier.** Every test in this plan runs offline with no API key. `git` is the only external binary, and it is already required to work in this repo.

Verification commands used throughout:

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format --check .
```

---

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `src/skill_eval/skills/baseline.py` | Resolve a skill's previous version from git history. Returns `Skill` or `BaselineUnavailable`; never raises for environmental failure. |
| `src/skill_eval/comparison.py` | Turn a two-armed `RunReport` into a `Delta`: paired aggregates, per-case `ArmStats`, low-signal checks, high-variance cases. Pure function over the report — no IO. |
| `tests/test_baseline_resolution.py` | `resolve_previous` against real temporary git repos. |
| `tests/test_comparison.py` | Delta math, pairing/exclusion, low-signal, variance. |
| `tests/test_arms.py` | Orchestrator arm and repeat behavior. |
| `docs/comparative-evals.md` | The user-facing page for arms, baselines, deltas and flags. |

**Modified:**

| File | Change |
| --- | --- |
| `src/skill_eval/models.py` | `Arm`/`BaselineKind` literals; `Skill.version`/`.variant`; `CaseOutcome.arm`/`.repeat_index`; `BaselineNote`; `RunReport.baseline_kind`/`.repeat`/`.baseline_notes` + candidate-only aggregates. |
| `src/skill_eval/skills/loader.py` | Factor out `parse_skill_text`; parse `version:` frontmatter. |
| `src/skill_eval/orchestrator.py` | Arm loop, repeat loop, baseline resolution, offered-mode skip. |
| `src/skill_eval/runners/fake.py` | Optional baseline-arm scripting. |
| `src/skill_eval/runners/pydantic_ai.py` | Neutral preamble for a skill with nothing to say. |
| `src/skill_eval/evaluators/assertion.py` | Emit one `CheckResult` per assertion. |
| `src/skill_eval/evaluators/trajectory.py` | Emit one `CheckResult` per declared check. |
| `src/skill_eval/evaluators/budget.py` | Emit one `CheckResult` per declared limit. |
| `src/skill_eval/gating.py` | `min_delta`, no-comparable-case, unresolved-baseline reasons. |
| `src/skill_eval/config.py` | `baseline`, `repeat`, `min_delta`. |
| `src/skill_eval/cli.py` | `--baseline`, `--repeat`, `--min-delta`, run-plan line, delta wiring. |
| `src/skill_eval/reporters/console.py` | Comparative rendering and the delta block. |
| `src/skill_eval/reporters/json_reporter.py` | `arm`/`repeat_index` per outcome; `delta` and `baseline_notes`. |
| `examples/*/SKILL.md` | `version:` frontmatter. |
| `docs/`, `ARCHITECTURE.md`, `CLAUDE.md`, `mkdocs.yml` | Documentation shipped with the change. |

---

### Task 1: `Skill.version`, `Skill.variant`, and `parse_skill_text`

A skill needs a declared version (M4 resolves "previous" by it) and a way to say which arm it belongs to. Parsing must work on a string from `git show`, not only a file on disk.

**Files:**
- Modify: `src/skill_eval/models.py` (the `Skill` model, top of file)
- Modify: `src/skill_eval/skills/loader.py:40-56`
- Modify: `docs/getting-started.md`
- Test: `tests/test_skill_loader.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Arm = Literal["candidate", "baseline"]`, `BaselineKind = Literal["none", "previous"]` in `models.py`
  - `Skill(name: str, description: str = "", instructions: str = "", version: str = "", path: Path, variant: Arm = "candidate")`
  - `parse_skill_text(text: str, *, name_fallback: str, path: Path, source: str) -> Skill`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skill_loader.py`:

```python
def test_the_frontmatter_version_is_parsed(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\nname: pdf\ndescription: d\nversion: 1.3.0\n---\n\nBody.\n",
        encoding="utf-8",
    )
    assert parse_skill_file(skill_md).version == "1.3.0"


def test_a_missing_version_is_an_empty_string_not_an_error(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: pdf\n---\n\nBody.\n", encoding="utf-8")
    assert parse_skill_file(skill_md).version == ""


def test_a_numeric_version_is_kept_as_text(tmp_path):
    # YAML turns `1.2` into a float; a version is an identifier, not a number,
    # and 1.20 must not compare equal to 1.2.
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: pdf\nversion: 1.2\n---\n\nBody.\n", encoding="utf-8")
    assert parse_skill_file(skill_md).version == "1.2"


def test_a_skill_is_a_candidate_unless_told_otherwise(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: pdf\n---\n\nBody.\n", encoding="utf-8")
    assert parse_skill_file(skill_md).variant == "candidate"


def test_a_skill_parses_from_text_without_touching_the_filesystem():
    text = "---\nname: pdf\ndescription: d\nversion: 2.0.0\n---\n\nBody.\n"
    skill = parse_skill_text(
        text, name_fallback="fallback", path=Path("/nowhere"), source="commit abc1234"
    )
    assert (skill.name, skill.version, skill.instructions) == ("pdf", "2.0.0", "Body.")


def test_malformed_text_names_its_source_not_a_file_path():
    with pytest.raises(SkillParseError, match="commit abc1234"):
        parse_skill_text(
            "---\nname: [unclosed\n---\n\nBody.\n",
            name_fallback="fallback",
            path=Path("/nowhere"),
            source="commit abc1234",
        )
```

Add to that file's imports (keep the existing ones):

```python
from pathlib import Path

import pytest

from skill_eval.skills.loader import SkillParseError, parse_skill_file, parse_skill_text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skill_loader.py -k "version or variant or text or source" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_skill_text'`

- [ ] **Step 3: Add the model fields**

In `src/skill_eval/models.py`, beside the existing `CaseStatus` / `CaseMode` literals:

```python
Arm = Literal["candidate", "baseline"]
BaselineKind = Literal["none", "previous"]
```

Replace the `Skill` model with:

```python
class Skill(BaseModel):
    """A skill under test, loaded from a SKILL.md file.

    `version` is whatever the frontmatter declared, verbatim and as text -- it
    is an identifier, not a number, so `1.20` must not compare equal to `1.2`.
    `variant` says which arm of a comparative run this copy belongs to; the
    orchestrator sets it, and `FakeRunner` scripts against it.
    """

    name: str
    description: str = ""
    instructions: str = ""
    version: str = ""
    path: Path
    variant: Arm = "candidate"
```

- [ ] **Step 4: Split the loader so a git blob and a file share one code path**

In `src/skill_eval/skills/loader.py`, replace `parse_skill_file` with:

```python
def parse_skill_text(text: str, *, name_fallback: str, path: Path, source: str) -> Skill:
    """Parse SKILL.md content into a Skill.

    `source` only ever appears in error messages: the content may have come
    from a file or from `git show`, and an error that says "commit 4f2a1c" is
    the difference between a useful report and a confusing one.
    """
    try:
        frontmatter, body = _split_frontmatter(text)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid frontmatter in {source}: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillParseError(f"invalid frontmatter in {source}: expected a mapping")
    return Skill(
        name=str(frontmatter.get("name") or name_fallback),
        description=str(frontmatter.get("description") or ""),
        instructions=body.strip(),
        version=str(frontmatter.get("version") or ""),
        path=path,
    )


def parse_skill_file(skill_md: Path) -> Skill:
    """Parse one SKILL.md into a Skill, falling back to the dir name."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillParseError(f"cannot read {skill_md}: {exc}") from exc
    return parse_skill_text(
        text,
        name_fallback=skill_md.parent.name,
        path=skill_md.parent,
        source=str(skill_md),
    )
```

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — the new tests pass and nothing else moves (`Skill` gained defaulted fields only).

- [ ] **Step 6: Document the frontmatter field**

In `docs/getting-started.md`, in the `SKILL.md` example, add the version line and a sentence after it:

```markdown
```yaml
---
name: greeting
description: Greet a user warmly and by name
version: 1.0.0
---
```

`version:` is optional. When present, `--baseline previous` uses it to find the
previous version of the skill in git history — see
[Comparative evals](comparative-evals.md).
```

> The link target is created in Task 13. `tests/test_docs.py::test_relative_links_resolve` checks every relative link, so **create the placeholder page now** to keep the suite green:
>
> ```bash
> printf '# Comparative evals\n\nDocumented in Task 13.\n' > docs/comparative-evals.md
> ```
>
> and add it to the `nav:` in `mkdocs.yml` under `Reference:` (after `Gating and exit codes`), or `test_every_page_is_reachable_from_the_nav` fails:
>
> ```yaml
>       - Comparative evals: comparative-evals.md
> ```

- [ ] **Step 7: Run the docs tests**

Run: `uv run pytest tests/test_docs.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/skill_eval/models.py src/skill_eval/skills/loader.py tests/test_skill_loader.py docs/getting-started.md docs/comparative-evals.md mkdocs.yml
git commit -m "feat: parse a skill's declared version and its arm variant"
```

---

### Task 2: Resolve the previous version from git

**Files:**
- Create: `src/skill_eval/skills/baseline.py`
- Test: `tests/test_baseline_resolution.py`

**Interfaces:**
- Consumes: `parse_skill_text`, `Skill` (Task 1).
- Produces:
  - `BaselineUnavailable(skill_name: str, reason: str)` — a frozen dataclass
  - `resolve_previous(skill: Skill) -> Skill | BaselineUnavailable`
  - `HISTORY_LIMIT: int`, `GIT_TIMEOUT_SECONDS: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_baseline_resolution.py`:

```python
"""Resolving a skill's previous version from real git history, offline."""

from __future__ import annotations

import subprocess
from pathlib import Path

from skill_eval.models import Skill
from skill_eval.skills.baseline import (
    HISTORY_LIMIT,
    BaselineUnavailable,
    resolve_previous,
)
from skill_eval.skills.loader import parse_skill_file


def _repo(tmp_path: Path) -> Path:
    """A real git repo with committer identity set, so commits succeed in CI."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    return tmp_path


def _skill_md(version: str, body: str) -> str:
    head = f"---\nname: pdf\ndescription: Handle {body}\n"
    if version:
        head += f"version: {version}\n"
    return head + f"---\n\n{body}\n"


def _commit(repo: Path, text: str, message: str) -> None:
    (repo / "SKILL.md").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "SKILL.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def test_the_previous_version_is_the_newest_commit_with_a_different_version(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "old instructions"), "feat: v1")
    _commit(repo, _skill_md("1.1.0", "new instructions"), "feat: v2")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.version == "1.0.0"
    assert previous.instructions == "old instructions"
    assert previous.variant == "baseline"


def test_a_same_version_commit_is_skipped_in_favour_of_a_real_predecessor(tmp_path):
    # The version identifies the version. A commit that edited the body without
    # bumping it is still *this* version, so `previous` must look further back.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "oldest"), "feat: v1")
    _commit(repo, _skill_md("1.1.0", "middle"), "feat: v2")
    _commit(repo, _skill_md("1.1.0", "tweaked"), "docs: reword")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.version == "1.0.0"


def test_an_unversioned_skill_falls_back_to_the_newest_differing_content(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("", "old instructions"), "feat: v1")
    _commit(repo, _skill_md("", "new instructions"), "feat: v2")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.instructions == "old instructions"


def test_uncommitted_edits_are_compared_against_the_committed_copy(tmp_path):
    # The working copy is what runs as the candidate, so it is what the search
    # compares against -- not HEAD against HEAD~1.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("", "committed instructions"), "feat: v1")
    (repo / "SKILL.md").write_text(_skill_md("", "uncommitted edit"), encoding="utf-8")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.instructions == "committed instructions"


def test_the_candidate_directory_is_kept_so_nothing_downstream_breaks(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "old"), "feat: v1")
    _commit(repo, _skill_md("1.1.0", "new"), "feat: v2")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.path == repo


def test_an_unchanged_skill_has_no_previous_version(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "only ever this"), "feat: v1")

    result = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert str(HISTORY_LIMIT) in result.reason


def test_an_untracked_skill_reports_why(tmp_path):
    repo = _repo(tmp_path)
    (repo / "SKILL.md").write_text(_skill_md("1.0.0", "never committed"), encoding="utf-8")

    result = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "not tracked" in result.reason


def test_a_directory_outside_a_repository_reports_why(tmp_path):
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "no repo here"), encoding="utf-8")

    result = resolve_previous(parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "not inside a git repository" in result.reason


def test_a_missing_git_binary_reports_why_and_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr("skill_eval.skills.baseline.shutil.which", lambda _: None)
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "x"), encoding="utf-8")

    result = resolve_previous(parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert "git is not installed" in result.reason


def test_the_skill_name_travels_with_the_reason(tmp_path):
    (tmp_path / "SKILL.md").write_text(_skill_md("1.0.0", "x"), encoding="utf-8")

    result = resolve_previous(parse_skill_file(tmp_path / "SKILL.md"))

    assert isinstance(result, BaselineUnavailable)
    assert result.skill_name == "pdf"


def test_a_malformed_historical_version_is_skipped_not_fatal(tmp_path):
    # An old commit with broken frontmatter is not an authoring error about the
    # *current* skill, so it must not abort the run.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "good old"), "feat: v1")
    _commit(repo, "---\nname: [unclosed\n---\n\nbroken\n", "feat: broken")
    _commit(repo, _skill_md("1.2.0", "current"), "feat: v3")

    previous = resolve_previous(parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.version == "1.0.0"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_baseline_resolution.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.skills.baseline'`

- [ ] **Step 3: Write the resolver**

Create `src/skill_eval/skills/baseline.py`:

```python
"""Resolve a skill's previous version from git history.

Nothing here raises for an environmental failure -- no git, no repo, an
untracked file, a history with nothing earlier in it. Those are facts about the
user's checkout, not authoring errors about their skill, so they come back as a
`BaselineUnavailable` the report can explain. The same discipline runners follow
for provider failures.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from skill_eval.models import Skill
from skill_eval.skills.loader import SKILL_FILENAME, SkillParseError, parse_skill_text

# How far back to look. A skill edited hundreds of times still finds its
# previous version within the first few commits; the bound exists so a
# pathological history cannot turn one run into thousands of `git show` calls.
HISTORY_LIMIT = 50

# A hung git must not hang CI.
GIT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class BaselineUnavailable:
    """Why a previous version could not be resolved. Returned, never raised."""

    skill_name: str
    reason: str


def _git(args: list[str], cwd: Path) -> str | None:
    """Run git in `cwd`; return stdout, or None if the command failed."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def _qualifies(previous: Skill, working: Skill, previous_text: str, working_text: str) -> bool:
    """Is `previous` genuinely an earlier version of `working`?

    A declared version is the authority: an edit that did not bump it is still
    *this* version, however much the body changed. Without one, differing
    content is the best evidence available.
    """
    if working.version:
        return previous.version != working.version
    return previous_text != working_text


def resolve_previous(skill: Skill) -> Skill | BaselineUnavailable:
    """The newest earlier version of `skill`, or why there isn't one."""
    skill_md = skill.path / SKILL_FILENAME
    try:
        working_text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return BaselineUnavailable(skill.name, f"cannot read {skill_md}: {exc}")

    if shutil.which("git") is None:
        return BaselineUnavailable(skill.name, "git is not installed")
    if _git(["rev-parse", "--show-toplevel"], cwd=skill.path) is None:
        return BaselineUnavailable(skill.name, f"{skill.path} is not inside a git repository")
    if _git(["ls-files", "--error-unmatch", SKILL_FILENAME], cwd=skill.path) is None:
        return BaselineUnavailable(skill.name, f"{SKILL_FILENAME} is not tracked by git")

    log = _git(
        ["log", f"--max-count={HISTORY_LIMIT}", "--format=%H", "--", SKILL_FILENAME],
        cwd=skill.path,
    )
    for sha in (log or "").split():
        # `<sha>:./<file>` resolves the path relative to cwd, which is the
        # skill's directory -- not the repository root.
        blob = _git(["show", f"{sha}:./{SKILL_FILENAME}"], cwd=skill.path)
        if blob is None:
            continue
        try:
            previous = parse_skill_text(
                blob,
                name_fallback=skill.path.name,
                path=skill.path,
                source=f"{SKILL_FILENAME} at commit {sha[:8]}",
            )
        except SkillParseError:
            # A historical version with broken frontmatter is not an authoring
            # error about the skill under test. Keep looking.
            continue
        if _qualifies(previous, skill, blob, working_text):
            return previous.model_copy(update={"variant": "baseline"})

    return BaselineUnavailable(
        skill.name,
        f"no earlier version of {SKILL_FILENAME} found in the last {HISTORY_LIMIT} commits",
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_baseline_resolution.py -v`
Expected: PASS — all 11 tests.

- [ ] **Step 5: Confirm no framework leak and lint cleanly**

Run: `uv run pytest tests/test_framework_isolation.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/skill_eval/skills/baseline.py tests/test_baseline_resolution.py
git commit -m "feat: resolve a skill's previous version from git history"
```

---

### Task 3: Arms and repeats in the orchestrator

**Files:**
- Modify: `src/skill_eval/models.py` (`CaseOutcome`, `RunReport`; add `BaselineNote`)
- Modify: `src/skill_eval/orchestrator.py`
- Modify: `src/skill_eval/runners/fake.py`
- Test: `tests/test_arms.py` (create), `tests/test_models.py`

**Interfaces:**
- Consumes: `Arm`, `BaselineKind`, `Skill.variant` (Task 1); `resolve_previous`, `BaselineUnavailable` (Task 2).
- Produces:
  - `BaselineNote(skill_name: str, case_name: str = "", kind: Literal["unavailable", "skipped"], reason: str)`
  - `CaseOutcome.arm: Arm`, `CaseOutcome.repeat_index: int`
  - `RunReport.baseline_kind: BaselineKind | None`, `.repeat: int`, `.baseline_notes: list[BaselineNote]`, `.candidate_outcomes`, `.baseline_outcomes`, `.baseline_errored`
  - `run_evals(..., baseline: BaselineKind | None = None, repeat: int = 1)`
  - `FakeRunner(responses=..., default=..., baseline_responses=...)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arms.py`:

```python
"""Two-armed runs: candidate vs baseline, sampled N times."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from skill_eval.models import RunResult, Skill
from skill_eval.orchestrator import run_evals
from skill_eval.runners.fake import FakeRunner

CASES_YAML = """cases:
  - name: passes
    task: good
    assertions:
      - kind: contains
        value: yes
"""

OFFERED_YAML = """cases:
  - name: triggers
    task: good
    mode: offered
    trajectory:
      skill_triggered: true
"""


def _skill(tmp_path, yaml_text=CASES_YAML, name="pdf"):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / f"{name}.eval.yaml").write_text(yaml_text, encoding="utf-8")
    return Skill(name=name, description="d", instructions="i", path=skill_dir)


def _runner():
    """Passes with the skill, fails without it."""
    return FakeRunner(
        responses={"good": RunResult(output="yes it worked", skill_triggered=True)},
        baseline_responses={"good": RunResult(output="nope", skill_triggered=False)},
    )


def test_without_a_baseline_nothing_changes(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()])
    assert report.total == 1
    assert report.baseline_kind is None
    assert report.baseline_outcomes == []
    assert report.outcomes[0].arm == "candidate"
    assert report.outcomes[0].repeat_index == 0


def test_a_baseline_runs_every_case_twice(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], baseline="none")
    assert len(report.outcomes) == 2
    assert {o.arm for o in report.outcomes} == {"candidate", "baseline"}
    assert report.baseline_kind == "none"


def test_baseline_outcomes_do_not_touch_the_gate_numbers(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], baseline="none")
    # One candidate pass, one baseline fail -- the pass rate is about the
    # candidate arm alone, so a weak baseline must not drag it below 100%.
    assert report.total == 1
    assert report.passed == 1
    assert report.failed == 0
    assert report.pass_rate == 1.0
    assert report.pass_rate_by_skill() == {"pdf": 1.0}


def test_the_baseline_arm_gets_a_skill_with_nothing_to_say(tmp_path):
    seen: list[Skill] = []

    class Recorder(FakeRunner):
        def run(self, skill, case):
            seen.append(skill)
            return super().run(skill, case)

    run_evals([_skill(tmp_path)], [Recorder()], baseline="none")
    baseline = next(s for s in seen if s.variant == "baseline")
    assert baseline.description == ""
    assert baseline.instructions == ""
    assert baseline.version == ""


def test_repeat_samples_each_arm_n_times(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], baseline="none", repeat=3)
    assert len(report.outcomes) == 6
    assert sorted(o.repeat_index for o in report.candidate_outcomes) == [0, 1, 2]
    assert report.repeat == 3


def test_repeat_without_a_baseline_still_samples(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], repeat=4)
    assert report.total == 4
    assert report.baseline_outcomes == []


def test_a_repeat_below_one_is_a_programming_error(tmp_path):
    with pytest.raises(ValueError, match="repeat must be at least 1"):
        run_evals([_skill(tmp_path)], [_runner()], repeat=0)


def test_an_offered_case_skips_the_baseline_under_none(tmp_path):
    report = run_evals([_skill(tmp_path, yaml_text=OFFERED_YAML)], [_runner()], baseline="none")
    assert report.baseline_outcomes == []
    assert [n.kind for n in report.baseline_notes] == ["skipped"]
    assert report.baseline_notes[0].case_name == "triggers"


def test_an_offered_case_runs_both_arms_under_previous(tmp_path):
    repo = tmp_path
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    skill_dir = repo / "pdf"
    skill_dir.mkdir()
    (skill_dir / "pdf.eval.yaml").write_text(OFFERED_YAML, encoding="utf-8")
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: pdf\nversion: 1.0.0\n---\n\nold\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feat: v1"], cwd=repo, check=True)
    skill_md.write_text("---\nname: pdf\nversion: 1.1.0\n---\n\nnew\n", encoding="utf-8")

    from skill_eval.skills.loader import load_skills

    report = run_evals(load_skills(skill_dir), [_runner()], baseline="previous")

    assert len(report.baseline_outcomes) == 1
    assert report.baseline_notes == []


def test_an_unresolvable_baseline_is_a_note_not_a_crash(tmp_path):
    report = run_evals([_skill(tmp_path)], [_runner()], baseline="previous")
    assert report.baseline_outcomes == []
    assert [n.kind for n in report.baseline_notes] == ["unavailable"]
    assert report.baseline_notes[0].skill_name == "pdf"


def test_an_errored_baseline_run_is_counted_apart_from_errored(tmp_path):
    runner = FakeRunner(
        responses={"good": RunResult(output="yes it worked")},
        baseline_responses={"good": RunResult(error="provider 500")},
    )
    report = run_evals([_skill(tmp_path)], [runner], baseline="none")
    assert report.errored == 0
    assert report.baseline_errored == 1
```

Also append to `tests/test_fake_runner.py`:

```python
def test_the_baseline_arm_can_be_scripted_separately():
    runner = FakeRunner(
        responses={"t": RunResult(output="with skill")},
        baseline_responses={"t": RunResult(output="without skill")},
    )
    case = EvalCase(name="c", task="t")
    candidate = Skill(name="s", path=Path("."))
    baseline = Skill(name="s", path=Path("."), variant="baseline")

    assert runner.run(candidate, case).output == "with skill"
    assert runner.run(baseline, case).output == "without skill"


def test_an_unscripted_baseline_arm_falls_back_to_the_shared_script():
    runner = FakeRunner(responses={"t": RunResult(output="shared")})
    baseline = Skill(name="s", path=Path("."), variant="baseline")
    assert runner.run(baseline, EvalCase(name="c", task="t")).output == "shared"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_arms.py -v`
Expected: FAIL — `TypeError: run_evals() got an unexpected keyword argument 'baseline'`

- [ ] **Step 3: Add the models**

In `src/skill_eval/models.py`, add `BaselineNote` above `CaseOutcome`:

```python
class BaselineNote(BaseModel):
    """Why a case or a skill has no baseline arm.

    A typed record, not a formatted string: gating has to distinguish "the
    baseline could not be resolved" (a problem) from "this case deliberately
    has no baseline" (not one), and parsing prose to tell them apart is how
    that distinction rots.
    """

    skill_name: str
    case_name: str = ""
    kind: Literal["unavailable", "skipped"]
    reason: str
```

Replace `CaseOutcome` with:

```python
class CaseOutcome(BaseModel):
    """The fully-scored result of one (skill, case, runner) combination.

    `repeat_index` is the 0-based index of one repetition; `RunReport.repeat` is
    how many were requested. The names differ because confusing the two is how
    an off-by-one reaches a report.
    """

    skill_name: str
    case_name: str
    runner: str
    status: CaseStatus
    scores: list[EvalScore] = Field(default_factory=list)
    result: RunResult | None = None
    arm: Arm = "candidate"
    repeat_index: int = 0
```

- [ ] **Step 4: Make `RunReport` aggregate the candidate arm only**

In `src/skill_eval/models.py`, replace the `RunReport` header and aggregate properties (keep `judge_cost_usd` as it is — it sums **both** arms, because that is real money spent):

```python
class RunReport(BaseModel):
    """Aggregated results for a whole run.

    Every aggregate below reads the **candidate** arm. A strong baseline means
    the skill was unnecessary, not that CI should go red, so baseline outcomes
    are reported but never gated on. With no baseline the two sets are
    identical and none of these numbers move.
    """

    outcomes: list[CaseOutcome] = Field(default_factory=list)
    skipped_skills: list[str] = Field(default_factory=list)
    tag_filtered_skills: list[str] = Field(default_factory=list)
    baseline_kind: BaselineKind | None = None
    repeat: int = 1
    baseline_notes: list[BaselineNote] = Field(default_factory=list)

    @property
    def candidate_outcomes(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.arm == "candidate"]

    @property
    def baseline_outcomes(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.arm == "baseline"]

    @property
    def total(self) -> int:
        return len(self.candidate_outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.candidate_outcomes if o.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.candidate_outcomes if o.status == "failed")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.candidate_outcomes if o.status == "errored")

    @property
    def baseline_errored(self) -> int:
        """Errored baseline runs, surfaced apart from `errored`.

        An errored baseline invalidates that case's delta rather than counting
        as a case that broke -- `errored` is about the candidate. It still gets
        its own number so it cannot hide.
        """
        return sum(1 for o in self.baseline_outcomes if o.status == "errored")

    @property
    def pass_rate(self) -> float:
        if not self.candidate_outcomes:
            return 0.0
        return self.passed / self.total
```

And in `pass_rate_by_skill`, change the loop source:

```python
    def pass_rate_by_skill(self) -> dict[str, float]:
        """Pass rate per skill name, for per-skill gating and reporting."""
        buckets: dict[str, list[CaseOutcome]] = {}
        for outcome in self.candidate_outcomes:
            buckets.setdefault(outcome.skill_name, []).append(outcome)
        return {
            name: sum(1 for o in items if o.status == "passed") / len(items)
            for name, items in buckets.items()
        }
```

- [ ] **Step 5: Teach `FakeRunner` about the baseline arm**

Replace `src/skill_eval/runners/fake.py`:

```python
"""A deterministic, offline runner used to test the whole pipeline."""

from __future__ import annotations

from skill_eval.models import EvalCase, RunResult, Skill


class FakeRunner:
    """Returns scripted RunResults. Never touches the network.

    `baseline_responses` lets a test script the two arms differently -- the
    only way a zero-cost test can express "this skill helps". It is consulted
    via `skill.variant` and falls back to `responses`, so existing single-arm
    scripts keep working unchanged.
    """

    name = "fake"

    def __init__(
        self,
        responses: dict[str, RunResult] | None = None,
        default: RunResult | None = None,
        baseline_responses: dict[str, RunResult] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default
        self._baseline_responses = baseline_responses or {}

    def run(self, skill: Skill, case: EvalCase) -> RunResult:
        if skill.variant == "baseline" and case.task in self._baseline_responses:
            return self._baseline_responses[case.task].model_copy(deep=True)
        if case.task in self._responses:
            return self._responses[case.task].model_copy(deep=True)
        if self._default is not None:
            return self._default.model_copy(deep=True)
        return RunResult(output=f"[fake] {skill.name} handled: {case.task}")
```

- [ ] **Step 6: Add the arm and repeat loops to the orchestrator**

In `src/skill_eval/orchestrator.py`, extend the imports:

```python
from skill_eval.models import (
    Arm,
    BaselineKind,
    BaselineNote,
    CaseOutcome,
    EvalCase,
    RunReport,
    Skill,
)
from skill_eval.skills.baseline import BaselineUnavailable, resolve_previous
```

Change `_run_one`'s signature and the two `CaseOutcome(...)` constructions inside it:

```python
def _run_one(
    skill: Skill,
    case: EvalCase,
    runner: Runner,
    evaluators: list[Evaluator],
    *,
    arm: Arm = "candidate",
    repeat_index: int = 0,
    report_skill_name: str | None = None,
) -> CaseOutcome:
    """Run a single combination and score it, keeping errored distinct from failed.

    `report_skill_name` is the *candidate's* name. A baseline resolved from git
    keeps its own name and description -- that is what makes an `offered` run
    against the previous version honest -- but both arms must group under one
    heading in the report, and the candidate's name is that heading.
    """
    name = report_skill_name if report_skill_name is not None else skill.name
    result = runner.run(skill, case)
    if result.errored:
        return CaseOutcome(
            skill_name=name,
            case_name=case.name,
            runner=runner.name,
            status="errored",
            scores=[],
            result=result,
            arm=arm,
            repeat_index=repeat_index,
        )
    scores = [evaluator.evaluate(case, result) for evaluator in evaluators]
    if any(score.errored for score in scores):
        status = "errored"
    else:
        status = "passed" if all(score.passed for score in scores) else "failed"
    return CaseOutcome(
        skill_name=name,
        case_name=case.name,
        runner=runner.name,
        status=status,
        scores=scores,
        result=result,
        arm=arm,
        repeat_index=repeat_index,
    )
```

Add two helpers above `run_evals`:

```python
def _baseline_skill(
    skill: Skill, kind: BaselineKind, notes: list[BaselineNote]
) -> Skill | None:
    """The skill the baseline arm runs, or None with a note explaining why not."""
    if kind == "none":
        # Empty description *and* empty instructions is what makes the runner
        # fall back to a neutral preamble, so the skill's name never leaks into
        # a baseline prompt.
        return Skill(
            name=skill.name,
            description="",
            instructions="",
            version="",
            path=skill.path,
            variant="baseline",
        )
    resolved = resolve_previous(skill)
    if isinstance(resolved, BaselineUnavailable):
        notes.append(
            BaselineNote(
                skill_name=resolved.skill_name, kind="unavailable", reason=resolved.reason
            )
        )
        return None
    return resolved


def _arms(
    case: EvalCase,
    skill: Skill,
    baseline_skill: Skill | None,
    kind: BaselineKind | None,
    notes: list[BaselineNote],
) -> list[tuple[Arm, Skill]]:
    """Which arms this case runs in."""
    arms: list[tuple[Arm, Skill]] = [("candidate", skill)]
    if baseline_skill is None:
        return arms
    if case.mode == "offered" and kind == "none":
        # There is no skill to offer, so `skill_triggered` would be false by
        # construction. Running it would spend real money to prove a tautology
        # and would report the artifact as "the skill helped 100%".
        notes.append(
            BaselineNote(
                skill_name=skill.name,
                case_name=case.name,
                kind="skipped",
                reason="mode: offered has nothing to offer under --baseline none",
            )
        )
        return arms
    arms.append(("baseline", baseline_skill))
    return arms
```

Extend `run_evals`'s signature and body (docstring additions shown; keep the existing paragraphs):

```python
def run_evals(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None = None,
    evaluators: list[Evaluator] | None = None,
    tag: str | None = None,
    judge: Judge | None = None,
    baseline: BaselineKind | None = None,
    repeat: int = 1,
) -> RunReport:
    """Run every (skill, case, runner, arm, repetition) and aggregate the results.

    ... (existing paragraphs unchanged) ...

    `baseline` opts into the second arm; None means today's single-arm run.
    `repeat` samples each arm that many times, each repetition being its own
    outcome. A `BaselineUnavailable` is not an authoring error -- it is a fact
    about the user's checkout -- so it becomes a note on the report rather than
    aborting the run.
    """
    if evaluators is not None and judge is not None:
        raise ValueError(
            "run_evals() received both `evaluators` and `judge`; pass an explicit "
            "JudgeEvaluator inside `evaluators` instead of also passing `judge`."
        )
    if repeat < 1:
        raise ValueError(f"repeat must be at least 1, got {repeat}")
    evaluators = (
        evaluators
        if evaluators is not None
        else [
            AssertionEvaluator(),
            TrajectoryEvaluator(),
            BudgetEvaluator(),
            JudgeEvaluator(judge if judge is not None else FakeJudge()),
        ]
    )
    outcomes: list[CaseOutcome] = []
    skipped: list[str] = []
    tag_filtered: list[str] = []
    notes: list[BaselineNote] = []
    for skill in skills:
        cases = load_cases_for_skill(skill, evals_path=evals_path)
        if not cases:
            skipped.append(skill.name)
            continue
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
            if not cases:
                tag_filtered.append(skill.name)
                continue
        # Resolved once per skill, never per case or per repetition: it shells
        # out to git.
        baseline_skill = (
            None if baseline is None else _baseline_skill(skill, baseline, notes)
        )
        for case in cases:
            for arm, arm_skill in _arms(case, skill, baseline_skill, baseline, notes):
                for runner in runners:
                    for index in range(repeat):
                        outcomes.append(
                            _run_one(
                                arm_skill,
                                case,
                                runner,
                                evaluators,
                                arm=arm,
                                repeat_index=index,
                                report_skill_name=skill.name,
                            )
                        )
    return RunReport(
        outcomes=outcomes,
        skipped_skills=skipped,
        tag_filtered_skills=tag_filtered,
        baseline_kind=baseline,
        repeat=repeat,
        baseline_notes=notes,
    )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_arms.py tests/test_fake_runner.py -v`
Expected: PASS

- [ ] **Step 8: Run the whole suite for regressions**

Run: `uv run pytest`
Expected: PASS — no existing test should move; single-arm runs produce only candidate outcomes, so every aggregate is unchanged.

- [ ] **Step 9: Commit**

```bash
git add src/skill_eval/models.py src/skill_eval/orchestrator.py src/skill_eval/runners/fake.py tests/test_arms.py tests/test_fake_runner.py
git commit -m "feat: run each eval case in a candidate and a baseline arm"
```

---

### Task 4: The baseline arm never sees the skill's name

`_system_prompt` emits `# {name}` as a header. For a baseline with nothing else in it, that leaks `# refund-handler` into the prompt and the delta measures the leak.

**Files:**
- Modify: `src/skill_eval/runners/pydantic_ai.py:47-52`
- Test: `tests/test_pydantic_ai_runner.py`

**Interfaces:**
- Consumes: `Skill` (Task 1).
- Produces: `BASELINE_PREAMBLE: str` exported from `runners/pydantic_ai.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pydantic_ai_runner.py`:

```python
EMPTY_SKILL = Skill(name="order-support", description="", instructions="", variant="baseline", path=Path("."))


def test_a_skill_with_nothing_to_say_gets_a_neutral_preamble():
    seen = {}

    def reply(messages, info: AgentInfo):
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(EMPTY_SKILL, case())
    assert seen["instructions"] == BASELINE_PREAMBLE


def test_a_baseline_prompt_never_leaks_the_skill_name():
    # The whole point of the baseline arm: if its prompt names the skill, the
    # delta measures the leak rather than the skill.
    seen = {}

    def reply(messages, info: AgentInfo):
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(EMPTY_SKILL, case())
    assert "order-support" not in seen["instructions"]


def test_a_baseline_resolved_from_git_still_gets_its_own_prompt():
    # `--baseline previous` has real content, so it is prompted normally --
    # the neutral preamble keys on emptiness, not on the arm.
    previous = Skill(
        name="order-support",
        description="Handle refunds",
        instructions="Old instructions.",
        variant="baseline",
        path=Path("."),
    )
    seen = {}

    def reply(messages, info: AgentInfo):
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(previous, case())
    assert "Old instructions." in seen["instructions"]
```

Extend that file's import of the runner module:

```python
from skill_eval.runners.pydantic_ai import BASELINE_PREAMBLE, OFFERED_PREAMBLE, PydanticAIRunner
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pydantic_ai_runner.py -k "neutral or leak or resolved_from_git" -v`
Expected: FAIL — `ImportError: cannot import name 'BASELINE_PREAMBLE'`

- [ ] **Step 3: Implement the neutral preamble**

In `src/skill_eval/runners/pydantic_ai.py`, add beside `OFFERED_PREAMBLE`:

```python
# A skill with no description and no instructions has nothing to say. Emitting
# the usual `# {name}` header anyway would put the skill's name into a baseline
# run's prompt, and the delta would then measure that leak rather than the
# skill. The rule keys on emptiness, not on the arm, so no runner has to know
# which arm it is serving -- a runner that *could* branch on the arm could cheat.
BASELINE_PREAMBLE = "You are a helpful assistant."
```

Replace `_system_prompt`:

```python
def _system_prompt(skill: Skill) -> str:
    """The skill, as the agent sees it: identity first, then its instructions."""
    if not skill.description and not skill.instructions:
        return BASELINE_PREAMBLE
    header = f"# {skill.name}"
    if skill.description:
        header = f"{header}\n\n{skill.description}"
    return f"{header}\n\n{skill.instructions}".strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pydantic_ai_runner.py -v`
Expected: PASS — all tests, including the pre-existing `test_the_skill_instructions_reach_the_model`.

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/runners/pydantic_ai.py tests/test_pydantic_ai_runner.py
git commit -m "fix: keep the skill name out of an empty baseline prompt"
```

---

### Task 5: Per-check verdicts from `AssertionEvaluator`

Comparing whole evaluators across arms cannot name the dead-weight assertion, and naming it is most of the value. Ids come from the **case**, so they are identical in both arms.

**Files:**
- Modify: `src/skill_eval/evaluators/assertion.py`
- Test: `tests/test_assertion_evaluator.py`

**Interfaces:**
- Consumes: `CheckResult` (exists in `models.py`).
- Produces: `EvalScore.checks` populated by `AssertionEvaluator` with ids of the form `{kind}[{index}]`, where `index` is the position in `case.assertions`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_assertion_evaluator.py`:

```python
def test_each_assertion_gets_its_own_check():
    case = EvalCase(
        name="c",
        task="t",
        assertions=[
            AssertionSpec(kind="contains", value="yes"),
            AssertionSpec(kind="contains", value="never"),
        ],
    )
    score = AssertionEvaluator().evaluate(case, RunResult(output="yes indeed"))

    assert [(c.id, c.passed) for c in score.checks] == [
        ("contains[0]", True),
        ("contains[1]", False),
    ]


def test_check_ids_are_positional_so_they_pair_across_arms():
    # Two assertions of the same kind must not collide, or a low-signal report
    # cannot say which one is dead weight.
    case = EvalCase(
        name="c",
        task="t",
        assertions=[
            AssertionSpec(kind="contains", value="a"),
            AssertionSpec(kind="contains", value="b"),
        ],
    )
    score = AssertionEvaluator().evaluate(case, RunResult(output="a b"))
    assert [c.id for c in score.checks] == ["contains[0]", "contains[1]"]


def test_every_check_carries_evidence():
    case = EvalCase(
        name="c", task="t", assertions=[AssertionSpec(kind="contains", value="never")]
    )
    score = AssertionEvaluator().evaluate(case, RunResult(output="nope"))
    assert score.checks[0].evidence
    assert "never" in score.checks[0].evidence


def test_a_case_with_no_assertions_has_no_checks():
    score = AssertionEvaluator().evaluate(EvalCase(name="c", task="t"), RunResult(output="x"))
    assert score.checks == []
    assert score.passed is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_assertion_evaluator.py -k "check" -v`
Expected: FAIL — `assert [] == [('contains[0]', True), ('contains[1]', False)]`

- [ ] **Step 3: Emit the checks**

Replace `AssertionEvaluator` in `src/skill_eval/evaluators/assertion.py`:

```python
class AssertionEvaluator:
    """Every assertion must hold; the score is the fraction that held.

    Each assertion also comes back as its own `CheckResult`. Ids are positional
    and derived from the *case*, never from the result, so the same ids appear
    in both arms of a comparative run and can be paired -- which is what makes
    "this assertion passes with or without the skill" detectable.
    """

    name = "assertion"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        if not case.assertions:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no assertions")
        checks: list[CheckResult] = []
        failures: list[str] = []
        for index, spec in enumerate(case.assertions):
            held = _check(spec, result.output)
            description = f"{spec.kind}({spec.value!r})"
            if not held:
                failures.append(description)
            checks.append(
                CheckResult(
                    id=f"{spec.kind}[{index}]",
                    passed=held,
                    evidence=f"{description} {'held' if held else 'did not hold'}",
                )
            )
        passed_count = len(case.assertions) - len(failures)
        detail = "all assertions held" if not failures else "failed: " + ", ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=passed_count / len(case.assertions),
            detail=detail,
            checks=checks,
        )
```

Extend the module's import:

```python
from skill_eval.models import AssertionSpec, CheckResult, EvalCase, EvalScore, RunResult
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_assertion_evaluator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/evaluators/assertion.py tests/test_assertion_evaluator.py
git commit -m "feat: report one check per assertion so checks pair across arms"
```

---

### Task 6: Per-check verdicts from `TrajectoryEvaluator` and `BudgetEvaluator`

A trajectory check that passes with or without the skill is exactly the same blind spot as a dead-weight assertion.

**Files:**
- Modify: `src/skill_eval/evaluators/trajectory.py`
- Modify: `src/skill_eval/evaluators/budget.py`
- Test: `tests/test_trajectory_evaluator.py`, `tests/test_budget_evaluator.py`

**Interfaces:**
- Consumes: `CheckResult`.
- Produces: check ids `called:{tool}`, `forbidden:{tool}`, `order`, `max_calls`, `skill_triggered`, `max_tokens`, `max_cost_usd`, `max_latency_ms`.

- [ ] **Step 1: Write the failing trajectory tests**

Append to `tests/test_trajectory_evaluator.py`:

```python
def test_each_declared_tool_gets_its_own_check():
    case = EvalCase(
        name="c",
        task="t",
        trajectory=TrajectorySpec(called=["lookup_order", "issue_refund"]),
    )
    result = RunResult(tool_calls=[ToolCall(name="lookup_order")])
    score = TrajectoryEvaluator().evaluate(case, result)

    assert [(c.id, c.passed) for c in score.checks] == [
        ("called:lookup_order", True),
        ("called:issue_refund", False),
    ]


def test_order_and_max_calls_are_single_checks():
    case = EvalCase(
        name="c",
        task="t",
        trajectory=TrajectorySpec(order=["a", "b"], max_calls=1),
    )
    result = RunResult(tool_calls=[ToolCall(name="b"), ToolCall(name="a")])
    score = TrajectoryEvaluator().evaluate(case, result)

    assert [(c.id, c.passed) for c in score.checks] == [("order", False), ("max_calls", False)]


def test_the_triggering_check_has_an_id_of_its_own():
    case = EvalCase(
        name="c", task="t", mode="offered", trajectory=TrajectorySpec(skill_triggered=True)
    )
    score = TrajectoryEvaluator().evaluate(case, RunResult(skill_triggered=True))
    assert [c.id for c in score.checks] == ["skill_triggered"]


def test_an_errored_trajectory_reports_no_checks():
    # The runner reported no triggering decision at all. There is no verdict to
    # record, and a check here would read as a real one.
    case = EvalCase(
        name="c", task="t", mode="offered", trajectory=TrajectorySpec(skill_triggered=True)
    )
    score = TrajectoryEvaluator().evaluate(case, RunResult(skill_triggered=None))
    assert score.errored is True
    assert score.checks == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_trajectory_evaluator.py -k check -v`
Expected: FAIL — `assert [] == [('called:lookup_order', True), ...]`

- [ ] **Step 3: Rewrite the trajectory check function to return checks**

Replace `_check` and `_total_checks` in `src/skill_eval/evaluators/trajectory.py` with a single function that yields checks, and update the evaluator:

```python
def _checks(spec: TrajectorySpec, called: list[str], triggered: bool | None) -> list[CheckResult]:
    """One CheckResult per declared check, in a stable order.

    Ids come from the spec, never from the result, so the same ids appear in
    both arms of a comparative run.
    """
    checks: list[CheckResult] = []

    for name in spec.called:
        held = name in called
        checks.append(
            CheckResult(
                id=f"called:{name}",
                passed=held,
                evidence=f"{name} was {'called' if held else 'never called'}",
            )
        )

    for name in spec.forbidden:
        held = name not in called
        checks.append(
            CheckResult(
                id=f"forbidden:{name}",
                passed=held,
                evidence=f"forbidden tool {name} was {'not called' if held else 'called'}",
            )
        )

    if spec.order:
        held = _is_subsequence(spec.order, called)
        arrow = " -> ".join(spec.order)
        checks.append(
            CheckResult(
                id="order",
                passed=held,
                evidence=(
                    f"order {arrow} followed"
                    if held
                    else f"order {arrow} not followed, got {called}"
                ),
            )
        )

    if spec.max_calls is not None:
        held = len(called) <= spec.max_calls
        checks.append(
            CheckResult(
                id="max_calls",
                passed=held,
                evidence=f"made {len(called)} tool calls, limit is {spec.max_calls}",
            )
        )

    if spec.skill_triggered is not None:
        held = triggered == spec.skill_triggered
        checks.append(
            CheckResult(
                id="skill_triggered",
                passed=held,
                evidence=(
                    "skill was triggered"
                    if triggered
                    else "skill was not triggered"
                )
                + f"; expected {spec.skill_triggered}",
            )
        )

    return checks


class TrajectoryEvaluator:
    """Every declared check must hold; the score is the fraction that held."""

    name = "trajectory"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        spec = case.trajectory
        if spec is None:
            return EvalScore(
                evaluator=self.name, passed=True, score=1.0, detail="no trajectory checks"
            )
        if spec.skill_triggered is not None and result.skill_triggered is None:
            # The runner reported no triggering decision at all. That is an
            # infra fact about the runner, not a signal about the skill, so it
            # must not read as a skill that failed to fire -- and there is no
            # verdict to record as a check.
            return EvalScore(
                evaluator=self.name,
                passed=False,
                errored=True,
                score=0.0,
                detail=(
                    "trajectory.skill_triggered was declared but the runner reported no "
                    "triggering decision; this runner does not support 'mode: offered'"
                ),
            )
        called = [call.name for call in result.tool_calls]
        checks = _checks(spec, called, result.skill_triggered)
        if not checks:
            return EvalScore(
                evaluator=self.name, passed=True, score=1.0, detail="no trajectory checks"
            )
        failures = [c.evidence for c in checks if not c.passed]
        detail = "all trajectory checks held" if not failures else "; ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(len(checks) - len(failures)) / len(checks),
            detail=detail,
            checks=checks,
        )
```

Extend the module import:

```python
from skill_eval.models import CheckResult, EvalCase, EvalScore, RunResult, TrajectorySpec
```

> **Note on the score:** the old `_total_checks` counted `called` and `forbidden` as *one* check each however many tools they listed; the new one counts each tool. That is a deliberate improvement — two missing tools out of three should not score the same as three — and the existing score assertions in `tests/test_trajectory_evaluator.py` must be updated to match. Run the file and fix the arithmetic in any failing assertion rather than reverting the behavior.

- [ ] **Step 4: Run the trajectory tests**

Run: `uv run pytest tests/test_trajectory_evaluator.py -v`
Expected: PASS after updating any pre-existing `score ==` assertions per the note above.

- [ ] **Step 5: Write the failing budget tests**

Append to `tests/test_budget_evaluator.py`:

```python
def test_each_declared_limit_gets_its_own_check():
    case = EvalCase(
        name="c", task="t", budget=BudgetSpec(max_tokens=10, max_latency_ms=1000)
    )
    result = RunResult(input_tokens=20, output_tokens=0, latency_ms=5)
    score = BudgetEvaluator().evaluate(case, result)

    assert [(c.id, c.passed) for c in score.checks] == [
        ("max_tokens", False),
        ("max_latency_ms", True),
    ]


def test_an_unpriceable_cost_limit_is_a_failing_check_with_its_reason():
    # It is not "within budget" -- nothing was verified. The check says so
    # instead of leaving an unexplained red case.
    case = EvalCase(name="c", task="t", budget=BudgetSpec(max_cost_usd=0.01))
    result = RunResult(cost_usd=0.0, cost_note="no pricing for model 'zzz'")
    score = BudgetEvaluator().evaluate(case, result)

    assert score.passed is False
    assert [(c.id, c.passed) for c in score.checks] == [("max_cost_usd", False)]
    assert "no pricing" in score.checks[0].evidence
```

- [ ] **Step 6: Run to verify they fail**

Run: `uv run pytest tests/test_budget_evaluator.py -k check -v`
Expected: FAIL — `assert [] == [('max_tokens', False), ('max_latency_ms', True)]`

- [ ] **Step 7: Emit budget checks**

Replace `_check` and `BudgetEvaluator` in `src/skill_eval/evaluators/budget.py`:

```python
def _checks(spec: BudgetSpec, result: RunResult) -> tuple[list[CheckResult], int]:
    """One CheckResult per declared limit, plus how many were actually evaluated.

    A cost limit is declared but not evaluated when `result.cost_note` is
    non-empty: `calculate_cost` degrades to 0.0 for an unpriced model, and
    `0.0 > max_cost_usd` is always False, so evaluating it anyway would report
    "within budget" for a limit nobody checked. It becomes a *failing* check
    carrying the reason -- never a passing one.
    """
    checks: list[CheckResult] = []
    evaluated = 0

    if spec.max_tokens is not None:
        evaluated += 1
        held = result.tokens <= spec.max_tokens
        checks.append(
            CheckResult(
                id="max_tokens",
                passed=held,
                evidence=f"used {result.tokens} tokens, limit is {spec.max_tokens}",
            )
        )
    if spec.max_cost_usd is not None:
        if result.cost_note:
            checks.append(
                CheckResult(
                    id="max_cost_usd",
                    passed=False,
                    evidence=f"cost budget not evaluated: {result.cost_note}",
                )
            )
        else:
            evaluated += 1
            held = result.cost_usd <= spec.max_cost_usd
            checks.append(
                CheckResult(
                    id="max_cost_usd",
                    passed=held,
                    evidence=(
                        f"cost ${result.cost_usd:.6f}, limit is ${spec.max_cost_usd:.6f}"
                    ),
                )
            )
    if spec.max_latency_ms is not None:
        evaluated += 1
        held = result.latency_ms <= spec.max_latency_ms
        checks.append(
            CheckResult(
                id="max_latency_ms",
                passed=held,
                evidence=f"took {result.latency_ms}ms, limit is {spec.max_latency_ms}ms",
            )
        )

    return checks, evaluated


class BudgetEvaluator:
    """Every limit that was actually evaluated must hold; the score is the fraction
    of *evaluated* limits that held. A limit whose cost could not be priced is
    skipped rather than counted as passed, so an unpriced model cannot earn a
    vacuous "within budget" verdict.
    """

    name = "budget"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        spec = case.budget
        if spec is None:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no budget checks")

        checks, evaluated = _checks(spec, result)
        if not checks:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no budget checks")

        failures = [c.evidence for c in checks if not c.passed]
        if evaluated == 0:
            # Every declared limit was skipped (an unpriced cost limit was the
            # only one declared). Nothing was actually verified, so this must
            # not silently score 1.0 as though the limit held.
            return EvalScore(
                evaluator=self.name,
                passed=False,
                score=0.0,
                detail="; ".join(failures),
                checks=checks,
            )

        # `passed` keys on *all* failures -- a skipped cost limit still fails
        # the case -- while `score` counts only what was actually evaluated, so
        # an unpriced limit neither inflates nor deflates the fraction.
        skipped_ids = {c.id for c in checks if c.id == "max_cost_usd" and result.cost_note}
        real_failures = [c for c in checks if not c.passed and c.id not in skipped_ids]
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=(evaluated - len(real_failures)) / evaluated,
            detail="; ".join(failures) if failures else "within budget",
            checks=checks,
        )
```

Extend the module import:

```python
from skill_eval.models import BudgetSpec, CheckResult, EvalCase, EvalScore, RunResult
```

- [ ] **Step 8: Run the budget tests and the whole suite**

Run: `uv run pytest tests/test_budget_evaluator.py -v && uv run pytest`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/skill_eval/evaluators/trajectory.py src/skill_eval/evaluators/budget.py tests/test_trajectory_evaluator.py tests/test_budget_evaluator.py
git commit -m "feat: report one check per trajectory and budget limit"
```

---

### Task 7: `comparison.py` — paired aggregates

**Files:**
- Create: `src/skill_eval/comparison.py`
- Test: `tests/test_comparison.py`

**Interfaces:**
- Consumes: `RunReport`, `CaseOutcome`, `Arm`, `BaselineKind` (Task 3).
- Produces:
  - `ArmStats(runs, errored, passed, pass_rate, stddev, mean_tokens, mean_cost_usd, mean_latency_ms)`
  - `CaseStats(skill_name, case_name, runner, candidate, baseline, comparable, exclusion_reason, low_signal, high_variance)`
  - `LowSignalCheck(skill_name, case_name, check_id, evaluator)`
  - `CaseRef(skill_name, case_name, runner, arm, pass_rate, stddev)`
  - `Delta(baseline_kind, pass_rate_candidate, pass_rate_baseline, pass_rate_delta, tokens_delta, cost_usd_delta, latency_ms_delta, cases, low_signal, high_variance, notes)`
  - `build_delta(report: RunReport) -> Delta | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_comparison.py`:

```python
"""Paired comparison of the two arms."""

from __future__ import annotations

from skill_eval.comparison import build_delta
from skill_eval.models import (
    BaselineNote,
    CaseOutcome,
    CheckResult,
    EvalScore,
    RunReport,
    RunResult,
)


def _outcome(status, arm="candidate", index=0, *, case="c", tokens=100, cost=0.001,
             latency=500, checks=()):
    return CaseOutcome(
        skill_name="pdf",
        case_name=case,
        runner="fake",
        status=status,
        arm=arm,
        repeat_index=index,
        scores=[
            EvalScore(
                evaluator="assertion",
                passed=status == "passed",
                checks=[CheckResult(id=cid, passed=ok, evidence="e") for cid, ok in checks],
            )
        ],
        result=RunResult(input_tokens=tokens, output_tokens=0, cost_usd=cost, latency_ms=latency),
    )


def test_no_baseline_arm_means_no_delta():
    report = RunReport(outcomes=[_outcome("passed")])
    assert build_delta(report) is None


def test_the_delta_is_the_candidate_minus_the_baseline():
    report = RunReport(
        baseline_kind="none",
        outcomes=[_outcome("passed"), _outcome("failed", arm="baseline")],
    )
    delta = build_delta(report)
    assert delta.pass_rate_candidate == 1.0
    assert delta.pass_rate_baseline == 0.0
    assert delta.pass_rate_delta == 1.0


def test_efficiency_deltas_are_per_run_means():
    report = RunReport(
        baseline_kind="none",
        outcomes=[
            _outcome("passed", tokens=100, cost=0.002, latency=400),
            _outcome("passed", arm="baseline", tokens=150, cost=0.003, latency=600),
        ],
    )
    delta = build_delta(report)
    assert delta.tokens_delta == -50.0
    assert round(delta.cost_usd_delta, 6) == -0.001
    assert delta.latency_ms_delta == -200.0


def test_a_case_with_no_baseline_run_is_excluded_from_both_halves():
    report = RunReport(
        baseline_kind="none",
        outcomes=[
            _outcome("passed", case="paired"),
            _outcome("failed", arm="baseline", case="paired"),
            _outcome("failed", case="lonely"),  # candidate only
        ],
    )
    delta = build_delta(report)
    lonely = next(c for c in delta.cases if c.case_name == "lonely")
    assert lonely.comparable is False
    assert lonely.exclusion_reason
    # The lonely failure must not drag the candidate rate: only `paired` counts.
    assert delta.pass_rate_candidate == 1.0


def test_an_errored_arm_invalidates_the_pair():
    report = RunReport(
        baseline_kind="none",
        outcomes=[_outcome("passed"), _outcome("errored", arm="baseline")],
    )
    delta = build_delta(report)
    assert delta.cases[0].comparable is False
    assert "errored" in delta.cases[0].exclusion_reason


def test_errored_repetitions_are_dropped_from_the_rate_not_counted_as_failures():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0),
            _outcome("errored", index=1),
            _outcome("failed", arm="baseline", index=0),
            _outcome("failed", arm="baseline", index=1),
        ],
    )
    delta = build_delta(report)
    assert delta.cases[0].candidate.pass_rate == 1.0
    assert delta.cases[0].candidate.errored == 1


def test_the_baseline_notes_travel_with_the_delta():
    report = RunReport(
        baseline_kind="previous",
        baseline_notes=[BaselineNote(skill_name="pdf", kind="unavailable", reason="no repo")],
        outcomes=[_outcome("passed"), _outcome("passed", arm="baseline")],
    )
    assert build_delta(report).notes == ["pdf: baseline unavailable — no repo"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_comparison.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.comparison'`

- [ ] **Step 3: Write the comparison module**

Create `src/skill_eval/comparison.py`:

```python
"""Turn a two-armed RunReport into the difference between the arms.

Pure functions over the report -- no IO, no provider calls. The invariant that
governs everything here is **pairing**: a case excluded from one arm is
excluded from both, because a delta computed from half a pair is a biased
number that looks like a real one.
"""

from __future__ import annotations

from statistics import pstdev

from pydantic import BaseModel, Field

from skill_eval.models import Arm, BaselineKind, CaseOutcome, RunReport


class ArmStats(BaseModel):
    """What one arm of one case did, across its repetitions."""

    runs: int = 0
    errored: int = 0
    passed: int = 0
    pass_rate: float = 0.0
    stddev: float = 0.0
    mean_tokens: float = 0.0
    mean_cost_usd: float = 0.0
    mean_latency_ms: float = 0.0


class LowSignalCheck(BaseModel):
    """A check that passed in both arms: it inflates the score, measuring nothing."""

    skill_name: str
    case_name: str
    evaluator: str
    check_id: str


class CaseRef(BaseModel):
    """A (case, arm) whose repetitions disagreed with each other."""

    skill_name: str
    case_name: str
    runner: str
    arm: Arm
    pass_rate: float
    stddev: float


class CaseStats(BaseModel):
    """Both arms of one case, and whether they can honestly be compared."""

    skill_name: str
    case_name: str
    runner: str
    candidate: ArmStats
    baseline: ArmStats | None = None
    comparable: bool = False
    exclusion_reason: str = ""
    low_signal: list[str] = Field(default_factory=list)
    high_variance: bool = False


class Delta(BaseModel):
    """The candidate arm minus the baseline arm.

    Every delta is candidate - baseline. Higher is better for `pass_rate_delta`;
    negative is better for tokens, cost and latency.
    """

    baseline_kind: BaselineKind
    pass_rate_candidate: float = 0.0
    pass_rate_baseline: float = 0.0
    pass_rate_delta: float = 0.0
    tokens_delta: float = 0.0
    cost_usd_delta: float = 0.0
    latency_ms_delta: float = 0.0
    cases: list[CaseStats] = Field(default_factory=list)
    low_signal: list[LowSignalCheck] = Field(default_factory=list)
    high_variance: list[CaseRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _scored(outcomes: list[CaseOutcome]) -> list[CaseOutcome]:
    """Repetitions that produced a verdict. An errored run is not a failed one."""
    return [o for o in outcomes if o.status != "errored"]


def _arm_stats(outcomes: list[CaseOutcome]) -> ArmStats:
    scored = _scored(outcomes)
    hits = [1.0 if o.status == "passed" else 0.0 for o in scored]
    return ArmStats(
        runs=len(outcomes),
        errored=len(outcomes) - len(scored),
        passed=int(sum(hits)),
        pass_rate=_mean(hits),
        stddev=pstdev(hits) if len(hits) > 1 else 0.0,
        mean_tokens=_mean([o.result.tokens for o in scored if o.result]),
        mean_cost_usd=_mean([o.result.cost_usd for o in scored if o.result]),
        mean_latency_ms=_mean([float(o.result.latency_ms) for o in scored if o.result]),
    )


def _group(report: RunReport) -> dict[tuple[str, str, str], dict[Arm, list[CaseOutcome]]]:
    grouped: dict[tuple[str, str, str], dict[Arm, list[CaseOutcome]]] = {}
    for outcome in report.outcomes:
        key = (outcome.skill_name, outcome.case_name, outcome.runner)
        grouped.setdefault(key, {"candidate": [], "baseline": []})[outcome.arm].append(outcome)
    return grouped


def _exclusion_reason(candidate: list[CaseOutcome], baseline: list[CaseOutcome]) -> str:
    """Why this case cannot be compared, or "" when it can."""
    if not baseline:
        return "no baseline run"
    if not _scored(candidate):
        return "every candidate repetition errored"
    if not _scored(baseline):
        return "every baseline repetition errored"
    return ""


def build_delta(report: RunReport) -> Delta | None:
    """Compare the arms, or return None when only one arm ran."""
    if not report.baseline_outcomes or report.baseline_kind is None:
        return None

    cases: list[CaseStats] = []
    paired_candidate: list[CaseOutcome] = []
    paired_baseline: list[CaseOutcome] = []

    for (skill_name, case_name, runner), arms in _group(report).items():
        candidate, baseline = arms["candidate"], arms["baseline"]
        reason = _exclusion_reason(candidate, baseline)
        stats = CaseStats(
            skill_name=skill_name,
            case_name=case_name,
            runner=runner,
            candidate=_arm_stats(candidate),
            baseline=_arm_stats(baseline) if baseline else None,
            comparable=not reason,
            exclusion_reason=reason,
        )
        if not reason:
            paired_candidate.extend(candidate)
            paired_baseline.extend(baseline)
        cases.append(stats)

    candidate_stats = _arm_stats(paired_candidate)
    baseline_stats = _arm_stats(paired_baseline)

    return Delta(
        baseline_kind=report.baseline_kind,
        pass_rate_candidate=candidate_stats.pass_rate,
        pass_rate_baseline=baseline_stats.pass_rate,
        pass_rate_delta=candidate_stats.pass_rate - baseline_stats.pass_rate,
        tokens_delta=candidate_stats.mean_tokens - baseline_stats.mean_tokens,
        cost_usd_delta=candidate_stats.mean_cost_usd - baseline_stats.mean_cost_usd,
        latency_ms_delta=candidate_stats.mean_latency_ms - baseline_stats.mean_latency_ms,
        cases=cases,
        notes=[
            f"{note.skill_name}: baseline unavailable — {note.reason}"
            if note.kind == "unavailable"
            else f"{note.skill_name} :: {note.case_name}: baseline skipped — {note.reason}"
            for note in report.baseline_notes
        ],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comparison.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/comparison.py tests/test_comparison.py
git commit -m "feat: compute a paired delta between the candidate and baseline arms"
```

---

### Task 8: Low-signal checks and high-variance cases

**Files:**
- Modify: `src/skill_eval/comparison.py`
- Test: `tests/test_comparison.py`

**Interfaces:**
- Consumes: `Delta`, `CaseStats`, `LowSignalCheck`, `CaseRef` (Task 7); `EvalScore.checks` (Tasks 5–6).
- Produces: `Delta.low_signal` and `Delta.high_variance` populated; `CaseStats.low_signal` / `.high_variance` set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comparison.py`:

```python
def test_a_check_that_passes_in_both_arms_is_low_signal():
    report = RunReport(
        baseline_kind="none",
        outcomes=[
            _outcome("passed", checks=(("contains[0]", True), ("contains[1]", True))),
            _outcome("failed", arm="baseline", checks=(("contains[0]", True), ("contains[1]", False))),
        ],
    )
    delta = build_delta(report)
    assert [c.check_id for c in delta.low_signal] == ["contains[0]"]
    assert delta.cases[0].low_signal == ["contains[0]"]


def test_a_check_that_fails_anywhere_is_not_low_signal():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0, checks=(("contains[0]", True),)),
            _outcome("failed", index=1, checks=(("contains[0]", False),)),
            _outcome("passed", arm="baseline", index=0, checks=(("contains[0]", True),)),
            _outcome("passed", arm="baseline", index=1, checks=(("contains[0]", True),)),
        ],
    )
    assert build_delta(report).low_signal == []


def test_an_excluded_case_contributes_no_low_signal_checks():
    # Without a baseline half there is nothing to compare against, so calling a
    # check "low signal" would be an unsupported claim.
    report = RunReport(
        baseline_kind="none",
        outcomes=[
            _outcome("passed", case="lonely", checks=(("contains[0]", True),)),
            _outcome("passed", case="paired", checks=(("contains[0]", True),)),
            _outcome("failed", arm="baseline", case="paired", checks=(("contains[0]", False),)),
        ],
    )
    assert build_delta(report).low_signal == []


def test_disagreeing_repetitions_flag_a_high_variance_case():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0),
            _outcome("failed", index=1),
            _outcome("failed", arm="baseline", index=0),
            _outcome("failed", arm="baseline", index=1),
        ],
    )
    delta = build_delta(report)
    assert [(r.arm, r.pass_rate) for r in delta.high_variance] == [("candidate", 0.5)]
    assert delta.high_variance[0].stddev == 0.5
    assert delta.cases[0].high_variance is True


def test_unanimous_repetitions_are_not_flagged():
    report = RunReport(
        baseline_kind="none",
        repeat=2,
        outcomes=[
            _outcome("passed", index=0),
            _outcome("passed", index=1),
            _outcome("failed", arm="baseline", index=0),
            _outcome("failed", arm="baseline", index=1),
        ],
    )
    assert build_delta(report).high_variance == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_comparison.py -k "low_signal or variance" -v`
Expected: FAIL — `assert [] == ['contains[0]']`

- [ ] **Step 3: Implement the two flags**

In `src/skill_eval/comparison.py`, add two helpers above `build_delta`:

```python
def _check_verdicts(outcome: CaseOutcome) -> dict[tuple[str, str], bool]:
    """Every check this run produced, keyed by (evaluator, check id)."""
    return {
        (score.evaluator, check.id): check.passed
        for score in outcome.scores
        for check in score.checks
    }


def _low_signal(
    candidate: list[CaseOutcome], baseline: list[CaseOutcome]
) -> list[tuple[str, str]]:
    """Checks that passed in every scored repetition of both arms.

    A check missing from any repetition is skipped rather than assumed: an
    unanimous verdict cannot be claimed from an incomplete one.
    """
    runs = [_check_verdicts(o) for o in _scored(candidate) + _scored(baseline)]
    if not runs:
        return []
    shared = set(runs[0])
    for verdicts in runs[1:]:
        shared &= set(verdicts)
    return sorted(key for key in shared if all(verdicts[key] for verdicts in runs))


def _high_variance(stats: ArmStats) -> bool:
    """True when repetitions disagreed. Only meaningful above one repetition."""
    scored = stats.runs - stats.errored
    return scored > 1 and 0.0 < stats.pass_rate < 1.0
```

Inside `build_delta`'s loop, after computing `stats` and before `cases.append(stats)`:

```python
        low_signal: list[LowSignalCheck] = []
        if not reason:
            paired_candidate.extend(candidate)
            paired_baseline.extend(baseline)
            for evaluator, check_id in _low_signal(candidate, baseline):
                low_signal.append(
                    LowSignalCheck(
                        skill_name=skill_name,
                        case_name=case_name,
                        evaluator=evaluator,
                        check_id=check_id,
                    )
                )
            stats.low_signal = [c.check_id for c in low_signal]
            all_low_signal.extend(low_signal)

        for arm_name, arm_stats in (("candidate", stats.candidate), ("baseline", stats.baseline)):
            if arm_stats is not None and _high_variance(arm_stats):
                stats.high_variance = True
                all_high_variance.append(
                    CaseRef(
                        skill_name=skill_name,
                        case_name=case_name,
                        runner=runner,
                        arm=arm_name,
                        pass_rate=arm_stats.pass_rate,
                        stddev=arm_stats.stddev,
                    )
                )
```

Replace the existing `if not reason:` block with the one above (it now does the pairing *and* the low-signal work), initialise the two accumulators before the loop:

```python
    all_low_signal: list[LowSignalCheck] = []
    all_high_variance: list[CaseRef] = []
```

and pass them into the returned `Delta`:

```python
        low_signal=all_low_signal,
        high_variance=all_high_variance,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comparison.py -v`
Expected: PASS — all 12 tests.

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/comparison.py tests/test_comparison.py
git commit -m "feat: flag low-signal checks and high-variance cases"
```

---

### Task 9: Gate on the delta

**Files:**
- Modify: `src/skill_eval/gating.py`
- Test: `tests/test_gating.py`

**Interfaces:**
- Consumes: `Delta` (Tasks 7–8), `RunReport.baseline_notes` (Task 3).
- Produces: `evaluate_gate(report, min_pass_rate=1.0, fail_on_error=True, per_skill_min=None, min_delta=None, delta=None) -> GateResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gating.py`:

```python
def _delta(pass_rate_delta: float, *, comparable: bool = True) -> Delta:
    return Delta(
        baseline_kind="none",
        pass_rate_delta=pass_rate_delta,
        cases=[
            CaseStats(
                skill_name="pdf",
                case_name="c",
                runner="fake",
                candidate=ArmStats(runs=1, passed=1, pass_rate=1.0),
                baseline=ArmStats(runs=1),
                comparable=comparable,
                exclusion_reason="" if comparable else "no baseline run",
            )
        ],
    )


def test_a_delta_at_or_above_the_bar_passes():
    report = RunReport(outcomes=[_passed_outcome()])
    gate = evaluate_gate(report, min_delta=0.2, delta=_delta(0.2))
    assert gate.passed is True
    assert gate.exit_code == 0


def test_a_delta_below_the_bar_fails():
    report = RunReport(outcomes=[_passed_outcome()])
    gate = evaluate_gate(report, min_delta=0.2, delta=_delta(0.05))
    assert gate.passed is False
    assert gate.exit_code == 1
    assert any("delta" in reason for reason in gate.reasons)


def test_a_negative_delta_fails_a_must_not_regress_bar():
    report = RunReport(outcomes=[_passed_outcome()])
    gate = evaluate_gate(report, min_delta=0.0, delta=_delta(-0.25))
    assert gate.passed is False


def test_gating_on_a_delta_with_nothing_comparable_fails():
    # A check that verified nothing must never report a pass.
    report = RunReport(outcomes=[_passed_outcome()])
    gate = evaluate_gate(report, min_delta=0.0, delta=_delta(0.0, comparable=False))
    assert gate.passed is False
    assert any("comparable" in reason for reason in gate.reasons)


def test_gating_on_a_delta_with_no_delta_at_all_fails():
    report = RunReport(outcomes=[_passed_outcome()])
    gate = evaluate_gate(report, min_delta=0.0, delta=None)
    assert gate.passed is False


def test_an_unresolved_baseline_fails_a_delta_gate():
    report = RunReport(
        outcomes=[_passed_outcome()],
        baseline_kind="previous",
        baseline_notes=[BaselineNote(skill_name="pdf", kind="unavailable", reason="no repo")],
    )
    gate = evaluate_gate(report, min_delta=0.0, delta=_delta(0.5))
    assert gate.passed is False
    assert any("no repo" in reason for reason in gate.reasons)


def test_a_deliberately_skipped_baseline_is_not_a_gate_reason():
    # Nothing went wrong: `mode: offered` has nothing to offer under
    # `--baseline none`. It still excludes the case from the delta.
    report = RunReport(
        outcomes=[_passed_outcome()],
        baseline_kind="none",
        baseline_notes=[
            BaselineNote(skill_name="pdf", case_name="c", kind="skipped", reason="offered")
        ],
    )
    gate = evaluate_gate(report, min_delta=0.0, delta=_delta(0.5))
    assert gate.passed is True


def test_without_min_delta_the_delta_is_reported_but_not_gated():
    report = RunReport(outcomes=[_passed_outcome()])
    gate = evaluate_gate(report, delta=_delta(-0.9))
    assert gate.passed is True
```

Add a small helper and the imports at the top of `tests/test_gating.py` (reuse whatever outcome factory the file already has if one exists — otherwise add this):

```python
from skill_eval.comparison import ArmStats, CaseStats, Delta
from skill_eval.models import BaselineNote, CaseOutcome, RunReport


def _passed_outcome() -> CaseOutcome:
    return CaseOutcome(skill_name="pdf", case_name="c", runner="fake", status="passed")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_gating.py -k delta -v`
Expected: FAIL — `TypeError: evaluate_gate() got an unexpected keyword argument 'min_delta'`

- [ ] **Step 3: Extend the gate**

In `src/skill_eval/gating.py`, extend the imports and the signature, and append the new rules just before `passed = not reasons`:

```python
from skill_eval.comparison import Delta
from skill_eval.models import RunReport


def evaluate_gate(
    report: RunReport,
    min_pass_rate: float = 1.0,
    fail_on_error: bool = True,
    per_skill_min: dict[str, float] | None = None,
    min_delta: float | None = None,
    delta: Delta | None = None,
) -> GateResult:
    """Apply thresholds to a report. Errored cases fail the gate by default.

    Every pre-existing rule reads the candidate arm (see `RunReport`). `min_delta`
    adds the comparative rules: the improvement must clear the bar, and it must
    have been measured against something -- a gate that verified nothing must
    never report a pass.
    """
```

... existing body ...

```python
    if min_delta is not None:
        if delta is None:
            reasons.append(
                "a minimum delta was required but no baseline arm ran, so no "
                "improvement could be measured"
            )
        else:
            comparable = [case for case in delta.cases if case.comparable]
            if not comparable:
                reasons.append(
                    "a minimum delta was required but no case had a comparable "
                    "baseline, so no improvement could be measured"
                )
            elif delta.pass_rate_delta < min_delta:
                reasons.append(
                    f"pass-rate delta {delta.pass_rate_delta:+.0%} is below the "
                    f"required {min_delta:+.0%}"
                )
        for note in report.baseline_notes:
            # A deliberately skipped baseline is not a failure -- nothing went
            # wrong. An unresolvable one is: treating it as "no change" would
            # let a repo pass this gate forever by deleting its git history.
            if note.kind == "unavailable":
                reasons.append(
                    f"skill {note.skill_name!r} has no resolvable baseline: {note.reason}"
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_gating.py -v`
Expected: PASS

- [ ] **Step 5: Check for an import cycle**

`gating` now imports `comparison`, which imports `models`. `comparison` must not import `gating`.

Run: `uv run python -c "import skill_eval.cli"`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/skill_eval/gating.py tests/test_gating.py
git commit -m "feat: gate on the improvement over the baseline"
```

---

### Task 10: Config and CLI wiring

**Files:**
- Modify: `src/skill_eval/config.py`
- Modify: `src/skill_eval/cli.py`
- Modify: `docs/cli.md`, `docs/configuration.md`
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `run_evals(baseline=, repeat=)` (Task 3), `build_delta` (Task 7), `evaluate_gate(min_delta=, delta=)` (Task 9).
- Produces: `Config.baseline: Literal["", "none", "previous"]`, `Config.repeat: int`, `Config.min_delta: float | None`; CLI flags `--baseline`, `--repeat`, `--min-delta`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_the_comparative_fields_have_safe_defaults():
    settings = Config()
    assert settings.baseline == ""
    assert settings.repeat == 1
    assert settings.min_delta is None


def test_an_unknown_baseline_kind_is_a_config_error(tmp_path):
    path = tmp_path / "skill-eval.toml"
    path.write_text('baseline = "yesterday"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=path)
```

Append to `tests/test_cli.py`:

```python
def test_min_delta_without_a_baseline_is_a_user_error(tmp_path, runner_cli):
    skill = _skill_with_a_passing_case(tmp_path)
    result = runner_cli.invoke(app, ["run", str(skill), "--min-delta", "0.1"])
    assert result.exit_code == 2
    assert "--baseline" in result.output


def test_a_repeat_below_one_is_a_user_error(tmp_path, runner_cli):
    skill = _skill_with_a_passing_case(tmp_path)
    result = runner_cli.invoke(app, ["run", str(skill), "--repeat", "0"])
    assert result.exit_code == 2


def test_an_unknown_baseline_kind_is_a_user_error(tmp_path, runner_cli):
    skill = _skill_with_a_passing_case(tmp_path)
    result = runner_cli.invoke(app, ["run", str(skill), "--baseline", "yesterday"])
    assert result.exit_code == 2


def test_min_delta_is_satisfied_by_a_baseline_from_config(tmp_path, runner_cli):
    # The check runs against resolved values, so a baseline in skill-eval.toml
    # satisfies a --min-delta passed on the command line.
    skill = _skill_with_a_passing_case(tmp_path)
    config = tmp_path / "skill-eval.toml"
    config.write_text('baseline = "none"\n', encoding="utf-8")
    result = runner_cli.invoke(
        app, ["run", str(skill), "--config", str(config), "--min-delta", "0.0"]
    )
    assert result.exit_code != 2


def test_the_run_plan_is_not_printed_for_the_offline_runner(tmp_path, runner_cli):
    skill = _skill_with_a_passing_case(tmp_path)
    result = runner_cli.invoke(app, ["run", str(skill), "--baseline", "none", "--repeat", "2"])
    assert "Plan:" not in result.output
```

> Reuse the file's existing fixtures and helpers — `tests/test_cli.py` already has a Typer `CliRunner` fixture and a helper that writes a skill with one passing case. Match their names rather than introducing `runner_cli` / `_skill_with_a_passing_case` if the file calls them something else.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -k "delta or repeat or baseline or plan" -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'baseline'`

- [ ] **Step 3: Add the config fields**

In `src/skill_eval/config.py`, add to `Config` (and extend the class docstring with the paragraph below):

```python
    baseline: Literal["", "none", "previous"] = ""
    repeat: int = 1
    min_delta: float | None = None
```

```
    `baseline` defaults to `""` (off) so upgrading never doubles anyone's bill:
    `none` and `previous` are the two kinds of baseline, and the *absence* of a
    value is what turns comparison off. `min_delta` has no default because 0.0
    is a real, stricter choice ("must not regress") -- silently assuming it
    would gate runs nobody asked to gate.
```

- [ ] **Step 4: Wire the CLI**

In `src/skill_eval/cli.py`, extend the imports:

```python
from skill_eval.comparison import build_delta
```

Add the three options to `run`:

```python
    baseline: Annotated[
        str | None,
        typer.Option(help="Compare against a baseline: none (no skill) or previous."),
    ] = None,
    repeat: Annotated[
        int | None, typer.Option(help="Sample each arm this many times.")
    ] = None,
    min_delta: Annotated[
        float | None,
        typer.Option(help="Required improvement over the baseline; needs --baseline."),
    ] = None,
```

Inside the `try:` block, after `settings = load_config(path=config)`, resolve and validate:

```python
        baseline_kind = baseline if baseline is not None else settings.baseline
        if baseline_kind not in ("", "none", "previous"):
            raise typer.BadParameter(f"unknown baseline: {baseline_kind}")
        resolved_repeat = repeat if repeat is not None else settings.repeat
        if resolved_repeat < 1:
            raise typer.BadParameter("--repeat must be at least 1")
        resolved_min_delta = min_delta if min_delta is not None else settings.min_delta
        # Checked against resolved values so a baseline in skill-eval.toml
        # satisfies a --min-delta on the command line. A gate that verified
        # nothing must never report a pass, so this is an error, not a warning.
        if resolved_min_delta is not None and not baseline_kind:
            raise typer.BadParameter("--min-delta requires --baseline none or --baseline previous")
```

Replace the `run_evals(...)` call:

```python
        if getattr(runner_class, "needs_api_key", False):
            arms = 2 if baseline_kind else 1
            case_count = sum(len(load_cases_for_skill(s, evals_path=evals)) for s in skills)
            typer.echo(
                f"Plan: {arms} arm(s) x {resolved_repeat} repeat(s) x {case_count} case(s) "
                f"= {arms * resolved_repeat * case_count} runs"
            )
        report = run_evals(
            skills,
            [active_runner],
            evals_path=evals,
            tag=tag,
            judge=active_judge,
            baseline=baseline_kind or None,
            repeat=resolved_repeat,
        )
```

After the `try/except`, build the delta and pass it to both the gate and the reporters:

```python
    delta = build_delta(report)
    gate = evaluate_gate(
        report,
        min_pass_rate=min_pass_rate if min_pass_rate is not None else settings.min_pass_rate,
        fail_on_error=settings.fail_on_error,
        per_skill_min=settings.per_skill_min,
        min_delta=resolved_min_delta,
        delta=delta,
    )

    typer.echo(render_console(report, gate=gate, delta=delta))
    if json_output is not None:
        try:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(render_json(report, gate=gate, delta=delta), encoding="utf-8")
```

> `render_console` and `render_json` grow their `delta=` parameter in Task 11. Implement that task's signature change **before** running the CLI tests, or add the keyword there first — the two tasks touch different files but the CLI call site needs both.

- [ ] **Step 5: Document the flags and fields**

In `docs/cli.md`, add to the `run` options table (`tests/test_docs.py::test_every_cli_option_is_documented` requires the literal flag strings):

```markdown
| `--baseline` | Run a second, baseline arm: `none` (no skill loaded) or `previous` (the prior version, from git). Omit for a single-arm run. |
| `--repeat` | Sample each arm this many times. Each repetition is its own outcome. |
| `--min-delta` | Require the candidate arm to beat the baseline by at least this much. Requires `--baseline`. |
```

In `docs/configuration.md`, add the three keys (`test_every_config_field_is_documented` requires the literal backticked names):

```markdown
| `baseline` | `""` | `""` (off), `"none"` or `"previous"`. Overridden by `--baseline`. |
| `repeat` | `1` | Repetitions per arm. Overridden by `--repeat`. |
| `min_delta` | unset | Required improvement over the baseline. Unset means the delta is reported but not gated; `0.0` is the stricter "must not regress". Overridden by `--min-delta`. |
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_config.py tests/test_cli.py tests/test_docs.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/skill_eval/config.py src/skill_eval/cli.py tests/test_config.py tests/test_cli.py docs/cli.md docs/configuration.md
git commit -m "feat: add --baseline, --repeat and --min-delta"
```

---

### Task 11: Report the comparison

**Files:**
- Modify: `src/skill_eval/reporters/console.py`
- Modify: `src/skill_eval/reporters/json_reporter.py`
- Test: `tests/test_reporters.py`

**Interfaces:**
- Consumes: `Delta` (Tasks 7–8).
- Produces: `render_console(report, gate=None, delta=None)`, `render_json(report, gate=None, delta=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reporters.py`:

```python
def test_without_a_delta_the_console_output_is_unchanged():
    report = RunReport(outcomes=[_outcome("passed")])
    assert render_console(report) == render_console(report, delta=None)


def test_a_comparative_line_shows_both_arms_and_the_difference():
    report = RunReport(baseline_kind="none", repeat=2, outcomes=[...])
    text = render_console(report, delta=build_delta(report))
    assert "candidate 2/2" in text
    assert "baseline 0/2" in text


def test_the_delta_block_names_the_direction_that_is_better():
    report = RunReport(baseline_kind="none", outcomes=[...])
    text = render_console(report, delta=build_delta(report))
    assert "Delta vs baseline" in text
    assert "negative is better" in text


def test_flags_are_rendered_as_advice_not_failures():
    report = RunReport(baseline_kind="none", outcomes=[...])  # a low-signal check
    text = render_console(report, delta=build_delta(report))
    assert "low-signal" in text


def test_baseline_notes_are_shown():
    report = RunReport(
        baseline_kind="previous",
        baseline_notes=[BaselineNote(skill_name="pdf", kind="unavailable", reason="no repo")],
        outcomes=[...],
    )
    assert "no repo" in render_console(report, delta=build_delta(report))


def test_json_carries_the_arm_and_repetition_of_every_outcome():
    report = RunReport(baseline_kind="none", outcomes=[...])
    payload = json.loads(render_json(report, delta=build_delta(report)))
    assert {o["arm"] for o in payload["outcomes"]} == {"candidate", "baseline"}
    assert payload["outcomes"][0]["repeat_index"] == 0


def test_json_delta_is_null_without_a_baseline():
    payload = json.loads(render_json(RunReport(outcomes=[_outcome("passed")])))
    assert payload["delta"] is None


def test_json_totals_count_real_spend_across_both_arms():
    # Money spent is money spent: the baseline arm's tokens are real.
    report = RunReport(baseline_kind="none", outcomes=[...])
    payload = json.loads(render_json(report, delta=build_delta(report)))
    assert payload["summary"]["total_tokens"] == 200
```

> Replace each `[...]` with a two-arm outcome list built from the file's existing outcome helper — one candidate `passed` and one baseline `failed`, each with `result=RunResult(input_tokens=100)`, and for the low-signal test give both a passing `CheckResult(id="contains[0]")`. Build them the same way `tests/test_comparison.py::_outcome` does; copy that helper into `tests/test_reporters.py` rather than importing across test modules.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_reporters.py -k "delta or comparative or arm" -v`
Expected: FAIL — `TypeError: render_console() got an unexpected keyword argument 'delta'`

- [ ] **Step 3: Render the comparison on the console**

In `src/skill_eval/reporters/console.py`, add the delta parameter and the comparative branch:

```python
from skill_eval.comparison import ArmStats, CaseStats, Delta


def _fraction(stats: ArmStats) -> str:
    scored = stats.runs - stats.errored
    return f"{stats.passed}/{scored}" if scored else "0/0 (all errored)"


def _mark_for(stats: ArmStats) -> str:
    if stats.runs and stats.errored == stats.runs:
        return _MARKS["errored"]
    return _MARKS["passed" if stats.pass_rate == 1.0 else "failed"]


def _case_line(case: CaseStats) -> str:
    head = (
        f"[{_mark_for(case.candidate)}] {case.skill_name} :: {case.case_name} "
        f"({case.runner})  candidate {_fraction(case.candidate)}"
    )
    if case.baseline is None:
        return f"{head}  baseline not run"
    if not case.comparable:
        return f"{head}  baseline {_fraction(case.baseline)}  (excluded: {case.exclusion_reason})"
    difference = case.candidate.pass_rate - case.baseline.pass_rate
    return f"{head}  baseline {_fraction(case.baseline)}  {difference:+.0%}"


def _delta_block(delta: Delta) -> list[str]:
    lines = ["", f"Delta vs baseline ({delta.baseline_kind})"]
    lines.append(
        f"  pass rate  {delta.pass_rate_baseline:.0%} -> {delta.pass_rate_candidate:.0%}  "
        f"{delta.pass_rate_delta:+.0%}   (higher is better)"
    )
    lines.append(f"  tokens     {delta.tokens_delta:+.0f}   (negative is better)")
    lines.append(f"  cost       ${delta.cost_usd_delta:+.4f}")
    lines.append(f"  latency    {delta.latency_ms_delta:+.0f}ms")
    if delta.low_signal:
        lines.append("")
        lines.append("Low-signal checks (passed with and without the skill — they measure nothing):")
        lines.extend(
            f"  - {c.skill_name} :: {c.case_name}: {c.check_id}" for c in delta.low_signal
        )
    if delta.high_variance:
        lines.append("")
        lines.append("High-variance cases (repetitions disagreed — often ambiguous instructions):")
        lines.extend(
            f"  - {r.skill_name} :: {r.case_name} ({r.arm}): "
            f"{r.pass_rate:.0%}, stddev {r.stddev:.2f}"
            for r in delta.high_variance
        )
    if delta.notes:
        lines.append("")
        lines.append("Baseline notes:")
        lines.extend(f"  - {note}" for note in delta.notes)
    lines.append("")
    lines.append("Flags above are advice about the eval suite; they never fail the gate.")
    return lines
```

Change the signature and the first loop:

```python
def render_console(
    report: RunReport, gate: GateResult | None = None, delta: Delta | None = None
) -> str:
    """Render a report as plain text suitable for a terminal or CI log.

    With no `delta` this is exactly the M3 renderer: one line per outcome. A
    comparative run collapses to one line per (case, arm) instead, because
    `--repeat 5 --baseline previous` would otherwise print ten lines per case.
    """
    lines: list[str] = []
    if delta is None:
        for outcome in report.outcomes:
            ...  # unchanged body
    else:
        for case in delta.cases:
            lines.append(_case_line(case))
            failing = next(
                (
                    o
                    for o in report.candidate_outcomes
                    if (o.skill_name, o.case_name, o.runner)
                    == (case.skill_name, case.case_name, case.runner)
                    and o.status != "passed"
                ),
                None,
            )
            if failing is not None:
                for score in failing.scores:
                    if not score.passed:
                        lines.append(f"        {score.evaluator}: {score.detail}")
                if failing.result is not None and failing.result.error:
                    lines.append(f"        error: {failing.result.error}")
            if case.low_signal:
                lines.append(f"        low-signal: {', '.join(case.low_signal)}")
```

And just before the gate section at the end:

```python
    if delta is not None:
        lines.extend(_delta_block(delta))
```

- [ ] **Step 4: Render the comparison in JSON**

In `src/skill_eval/reporters/json_reporter.py`:

```python
from skill_eval.comparison import Delta


def render_json(
    report: RunReport, gate: GateResult | None = None, delta: Delta | None = None
) -> str:
    """Render a report as indented JSON for CI artifacts and tooling.

    Token, cost and latency totals sum **both** arms: money spent is money
    spent. The pass/fail counts in `summary` are the candidate arm's, because
    those are what the gate reads.
    """
```

Add `arm` and `repeat_index` to each outcome dict:

```python
                "arm": o.arm,
                "repeat_index": o.repeat_index,
```

and two top-level keys after `"outcomes"`:

```python
    payload["delta"] = delta.model_dump() if delta is not None else None
    payload["baseline_notes"] = [note.model_dump() for note in report.baseline_notes]
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_reporters.py -v && uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/skill_eval/reporters/console.py src/skill_eval/reporters/json_reporter.py tests/test_reporters.py
git commit -m "feat: report the delta, the flags and the baseline notes"
```

---

### Task 12: A cassette for the baseline prompt, and versioned examples

M4 adds no new *kind* of provider interaction, but the baseline prompt is a new prompt shape. The cassette is what proves it reaches a real provider without the skill's name in it.

**Files:**
- Modify: `tests/test_cassettes.py`
- Modify: `examples/greeting/SKILL.md`, `examples/order-support/SKILL.md`

**Interfaces:**
- Consumes: `BASELINE_PREAMBLE` (Task 4), `run_evals(baseline=)` (Task 3).
- Produces: a recorded two-arm run under `tests/cassettes/`.

- [ ] **Step 1: Add `version:` to both example skills**

`examples/greeting/SKILL.md`:

```yaml
---
name: greeting
description: Greet a user warmly and by name
version: 1.0.0
---
```

`examples/order-support/SKILL.md`:

```yaml
---
name: order-support
description: Handle customer refund requests against the 30-day return policy
version: 1.0.0
---
```

- [ ] **Step 2: Verify the dogfood step still works**

Run: `uv run skill-eval list ./examples`
Expected: two lines, one per skill, each with its case count — this is CI's self-check.

- [ ] **Step 3: Write the failing cassette test**

Append to `tests/test_cassettes.py`, matching the file's existing decorator and fixture style:

```python
@pytest.mark.vcr
def test_a_baseline_run_reaches_the_provider_without_the_skill_name(replay, tmp_path):
    """The neutral preamble is what the provider actually receives.

    A unit test proves the string; only a recorded exchange proves it survived
    the adapter and went out on the wire.
    """
    skill = Skill(name="order-support", description="", instructions="", variant="baseline",
                  path=tmp_path)
    runner = PydanticAIRunner(model="openai:gpt-4o-mini")
    result = runner.run(skill, EvalCase(name="c", task="Say hello in five words."))

    assert result.errored is False
    assert "order-support" not in result.output
```

- [ ] **Step 4: Run it in replay-only mode**

Run: `uv run pytest tests/test_cassettes.py -k baseline -v`
Expected: SKIPPED — "cassette … not recorded". That is the correct zero-cost outcome; a missing cassette skips rather than fails.

- [ ] **Step 5: Record the cassette** (needs a real key; costs a fraction of a cent)

```bash
uv run pytest tests/test_cassettes.py -k baseline --record-mode=once
```

Expected: PASS, and a new file under `tests/cassettes/test_cassettes/`.

- [ ] **Step 6: Verify replay and check the recording for secrets**

Run: `uv run pytest tests/test_cassettes.py -v`
Expected: PASS on replay.

Then confirm nothing sensitive landed on disk:

```bash
grep -rniE "sk-|authorization|openai-organization|set-cookie" tests/cassettes/ || echo "clean"
```

Expected: `clean`.

- [ ] **Step 7: Commit**

```bash
git add examples tests/test_cassettes.py tests/cassettes
git commit -m "test: record a baseline-arm exchange and version the examples"
```

> If no API key is available, skip Steps 5–6 and commit the test alone — the skip-if-absent rule keeps CI green, and the cassette can be recorded later via the repo's refresh workflow. Say so explicitly in the commit body rather than leaving it implied.

---

### Task 13: Documentation

Documentation ships **with** the change. Two CI jobs enforce it (`docs`, `docs-freshness`).

**Files:**
- Modify: `docs/comparative-evals.md` (replace the Task 1 placeholder)
- Modify: `docs/gating.md`, `docs/runners.md`, `docs/eval-files.md`, `docs/roadmap.md`
- Modify: `ARCHITECTURE.md`, `CLAUDE.md`

- [ ] **Step 1: Write the comparative-evals page**

Replace `docs/comparative-evals.md` with a page covering, in this order:

1. **Why** — an eval that only runs with the skill loaded cannot separate "the skill works" from "the model would have done it anyway".
2. **The two arms** — candidate and baseline; `--baseline none` (no skill) vs `--baseline previous` (prior version from git). Absent flag = single arm, today's behavior.
3. **How `previous` is resolved** — declared `version:` first, newest differing content as the fallback; the `BaselineUnavailable` reasons and what each means for the user.
4. **`mode: offered` cases** — run in both arms under `previous`, candidate-only under `none`, and why.
5. **`--repeat N`** — each repetition is its own outcome; at `min_pass_rate = 1.0` this is "all repetitions must pass".
6. **The delta block** — sign conventions: higher is better for pass rate, negative is better for tokens/cost/latency.
7. **Low-signal checks and high-variance cases** — what each means, and that both are advisory and never fail the gate.
8. **`--min-delta`** — including that it requires a baseline, and that a run with nothing comparable fails.
9. **Cost** — `--repeat 5 --baseline previous` is a 10× bill; the run-plan line.

Include a worked CI example:

````markdown
```bash
skill-eval run ./skills --runner pydantic-ai --baseline previous --repeat 3 --min-delta 0.0
```
````

- [ ] **Step 2: Update the reference pages**

- `docs/gating.md` — the new gate rules (delta below bar, nothing comparable, unresolved baseline), that flags never change the exit code, that baseline outcomes never count, and the new JSON keys (`arm`, `repeat_index`, `delta`, `baseline_notes`).
- `docs/runners.md` — the baseline arm, and that a skill with no description and no instructions gets a neutral preamble so its name never leaks.
- `docs/eval-files.md` — that assertion, trajectory and budget results now carry per-check ids, with the id formats from Tasks 5–6.
- `docs/roadmap.md` — mark M4 shipped and describe what landed.

- [ ] **Step 3: Update `ARCHITECTURE.md`**

Add `comparison.py` and `skills/baseline.py` to the module map, and add the new invariants from the spec's §14 with their rationale (the file is the explanation; `CLAUDE.md` is the condensed list).

- [ ] **Step 4: Update `CLAUDE.md`**

- Change the milestone line from **M3** to **M4** and name the M4 spec file.
- Add to the invariants list:
  - Absent `--baseline`, behavior is identical to the single-arm run.
  - Baseline outcomes never count toward the gate's pass rate or `errored`.
  - The baseline arm never receives the skill's name under `--baseline none`.
  - An unresolvable baseline is reported, never assumed to be "no change".
  - The delta is paired: a case excluded from one arm is excluded from both.
  - `--min-delta` without a baseline is a user error (exit 2); gating on a delta with nothing comparable fails.
  - Low-signal and high-variance flags never change the exit code.
  - `resolve_previous` never raises for environmental failures.

- [ ] **Step 5: Build the docs and run the docs tests**

```bash
uv sync --group docs
```

Run: `uv run mkdocs build --strict && uv run pytest tests/test_docs.py -v`
Expected: PASS — no broken links, every page in the nav, every flag and config key documented.

- [ ] **Step 6: Full verification**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add docs ARCHITECTURE.md CLAUDE.md
git commit -m "docs: document comparative evals, deltas and delta gating"
```

---

## Self-Review

Checked against `docs/superpowers/specs/2026-08-03-skill-eval-m4-design.md`:

| Spec section | Task |
| --- | --- |
| §3 arms, `Skill.version`/`.variant`, baseline skill construction | 1, 3 |
| §3 neutral preamble / no name leak | 4 |
| §3 `mode: offered` arm rules | 3 |
| §4 git resolution, `BaselineUnavailable`, `parse_skill_text` | 1, 2 |
| §5 per-check verdicts, id formats, evidence | 5, 6 |
| §6 `comparison.py`, pairing, low-signal, high-variance | 7, 8 |
| §7 model changes (`arm`, `repeat_index`, candidate-only aggregates, `baseline_errored`) | 3 |
| §8 orchestrator arm/repeat loops, once-per-skill resolution | 3 |
| §9 `min_delta`, no-comparable-case, unresolved baseline, skipped ≠ failure | 9 |
| §10 config, flags, parse-time rejections, run plan | 10 |
| §11 console byte-identical default, comparative lines, delta block, JSON | 11 |
| §12 tests (unit, cassette, live) | every task; 12 for the cassette |
| §13 examples gain `version:` | 12 |
| §14 invariants | Global Constraints; 13 records them in `CLAUDE.md` |

Names are consistent across tasks: `parse_skill_text`, `resolve_previous`, `BaselineUnavailable`, `BaselineNote`, `build_delta`, `ArmStats`, `CaseStats`, `LowSignalCheck`, `CaseRef`, `Delta`, `repeat_index`, `BASELINE_PREAMBLE`.
