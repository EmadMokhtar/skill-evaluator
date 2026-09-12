# skill-lens M6 Part 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent read the files a skill ships beside `SKILL.md` and — only when the
run explicitly allows it — execute the scripts bundled there, under portable guards and an
OS sandbox where one exists.

**Architecture:** Two new framework-neutral modules. `bundle.py` is a read-only view of the
three Agent Skills directories (`scripts/`, `references/`, `assets/`), mirroring
`workspace.py`'s "methods raise, tools catch" split. `scripts.py` turns a policy and a path
into a `ScriptResult`: scrubbed environment, scratch directory, timeout that kills the
process group, capped output, plus `sandbox-exec` (macOS) / `bwrap` (Linux) wrapping.
`runners/tools.py` builds three more `AgentTool`s from them; the orchestrator gains a
`RunOptions` bundle and a once-per-run preflight; `--baseline previous` materialises the
previous bundle from git.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, PyYAML, `subprocess`, `tarfile`, pytest,
ruff (with the `S` rules), uv, Commitizen.

**Spec:** `docs/superpowers/specs/2026-09-12-skill-lens-m6-part2-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Python `>=3.11`.** `tarfile.extractall(filter="data")` needs 3.11.4+; the lockfile is
  far past that. Do not add a backport.
- **No new runtime dependency.** Everything here is standard library plus what is already
  in `pyproject.toml`.
- **No agent-framework type may appear outside `runners/pydantic_ai.py` and
  `judges/pydantic_ai.py`.** `bundle.py`, `scripts.py`, `runners/tools.py`, `runners/base.py`
  are framework-neutral. `tests/test_framework_isolation.py` asserts it.
- **`skill_lens` (underscore) never appears in user-facing output.** The name is `skill-lens`.
- **All file IO pins `encoding="utf-8"`.**
- **`extra="forbid"`** on `Config` and every case-facing model.
- **`errored` is not `failed`.** A script that will not run is a fact about the skill — it
  reaches the model as text, never `RunResult.error`. A missing interpreter or a required
  sandbox that is absent is a setup error (`ScriptSetupError`, exit 2) raised before any
  case runs.
- **A built-in tool never raises.** `run_script`, `read_skill_file` and `list_skill_files`
  return a string for every input, including wrongly-shaped arguments.
- **Ruff's `S` rules are on.** Every `subprocess` call in `src/` carries an inline `# noqa:
  S603` with the reason (fixed argv, no shell), and an interpreter looked up on `PATH` a
  `# noqa: S607` with the reason. Never widen the per-file ignores for `src/`.
- **Conventional Commits.** Every commit message is `type(scope)?: description`, lowercase,
  imperative, no trailing period. The commit-msg hook (`cz check`) enforces it.
- **Tests are zero-cost, offline, deterministic.** Scripts in tests are run with
  `sys.executable` injected as the `py` interpreter, so no test needs `python3` on `PATH`.
  Real-sandbox tests are `skipif`-guarded on the probe.
- **Documentation ships with the change.** Task 16 is not optional; `tests/test_docs.py`
  and the `docs` CI job fail without it.
- **Run the suite with `uv run pytest`** and lint with `uv run ruff check . && uv run ruff
  format --check .` before every commit.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `src/skill_lens/models.py` | `SandboxMode`, `SandboxBackend`, `Skill.bundle_root`, `ScriptStatus`, `ScriptNote`, `RunReport.scripts` / `script_notes` | 1 |
| `src/skill_lens/bundle.py` (new) | `SkillBundle`: read-only listing/read/script resolution under `scripts/`, `references/`, `assets/` | 2 |
| `src/skill_lens/skills/loader.py` | sets `bundle_root` | 3 |
| `src/skill_lens/workspace.py` | `Workspace.over_limit()` | 4 |
| `src/skill_lens/scripts.py` (new) | `ScriptPolicy`, `SandboxStatus`, `ScriptRuntime`, `ScriptResult`, `ScriptSetupError`; sandbox builders and probe; `preflight`; `run_script` | 5, 6 |
| `tests/test_sandbox_live.py` (new) | real `sandbox-exec` / `bwrap` behaviour, skipped where absent | 7 |
| `src/skill_lens/runners/tools.py` | `WORKSPACE_TOOL_NAMES`, `BUNDLE_TOOL_NAMES`, six-name `BUILTIN_TOOL_NAMES`, `build_bundle_tools`, `render_script_result` | 8 |
| `src/skill_lens/cases/loader.py` | one hint message | 8 |
| `src/skill_lens/runners/base.py`, `runners/fake.py`, `runners/pydantic_ai.py` | `scripts=` keyword; adapter registers bundle tools | 9 |
| `src/skill_lens/config.py`, `examples/skill-lens.toml` | five keys, normalisation, `script_policy()`; the annotated example config | 10 |
| `src/skill_lens/cli.py` | `--allow-scripts/--no-allow-scripts`, `RunOptions`, `ScriptSetupError` in `_AUTHORING_ERRORS` | 10, 11 |
| `src/skill_lens/orchestrator.py` | `RunOptions`, preflight, `ScriptNote`s, baseline store cleanup | 11, 12 |
| `src/skill_lens/skills/baseline.py` | `into=` keyword, bundle extraction from `git archive` | 12 |
| `src/skill_lens/reporters/{console,markdown,junit,json_reporter}.py` | script status and notes | 13 |
| `examples/log-triage/` (new), `tests/test_examples.py` | the shipped example | 14 |
| `tests/test_cassettes.py`, `tests/cassettes/` | a recorded `run_script` call | 15 |
| `docs/*.md`, `ARCHITECTURE.md`, `CLAUDE.md`, the Part 1 spec | documentation | 16 |
| (whole suite, real runs, the PR) | verification | 17 |

---

### Task 1: Models

**Files:**
- Modify: `src/skill_lens/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `SandboxMode = Literal["auto", "required", "off"]`,
  `SandboxBackend = Literal["sandbox-exec", "bwrap", "none"]`, `Skill.bundle_root: Path | None`,
  `ScriptStatus(sandbox: SandboxBackend, detail: str)`,
  `ScriptNote(skill_name: str, script_count: int)`,
  `RunReport.scripts: ScriptStatus | None`, `RunReport.script_notes: list[ScriptNote]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
def test_a_skill_has_no_bundle_unless_one_is_set():
    # None is the safe default: a Skill built by hand in a test, and the
    # --baseline none skill the orchestrator builds, must never carry the
    # candidate's scripts.
    skill = Skill(name="pdf", path=Path("/tmp/pdf"))
    assert skill.bundle_root is None


def test_script_status_records_the_backend_and_why():
    status = ScriptStatus(sandbox="none", detail="bwrap not found on PATH")
    assert status.sandbox == "none"
    with pytest.raises(ValidationError):
        ScriptStatus(sandbox="firejail", detail="")


def test_a_run_report_defaults_to_scripts_off():
    report = RunReport()
    assert report.scripts is None
    assert report.script_notes == []
    report = RunReport(
        scripts=ScriptStatus(sandbox="bwrap", detail="bwrap probe succeeded"),
        script_notes=[ScriptNote(skill_name="pdf", script_count=2)],
    )
    assert report.scripts.sandbox == "bwrap"
    assert report.script_notes[0].script_count == 2
```

Add to the imports at the top of `tests/test_models.py` (keep existing ones):
`from pathlib import Path`, `import pytest`, `from pydantic import ValidationError`, and
`RunReport, ScriptNote, ScriptStatus, Skill` from `skill_lens.models`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v -k "bundle or script"`
Expected: FAIL — `ImportError: cannot import name 'ScriptStatus'`.

- [ ] **Step 3: Add the models**

In `src/skill_lens/models.py`, after `BaselineKind`:

```python
SandboxMode = Literal["auto", "required", "off"]
SandboxBackend = Literal["sandbox-exec", "bwrap", "none"]
```

In `Skill`, after `variant: Arm = "candidate"`, and extend the docstring:

```python
    bundle_root: Path | None = None
```

Docstring addition (append a paragraph):

```
    `bundle_root` is the directory whose `scripts/`, `references/` and `assets/`
    the agent may read -- None when the skill ships none. Only the skill
    loader and the baseline resolver ever set it, so a Skill built by hand
    and the `--baseline none` skill both carry no bundle by default: keying
    the bundle tools on `path` instead would leak the candidate's scripts into
    the "no skill" arm, since every Skill has a path.
```

After `BaselineNote`, add:

```python
class ScriptStatus(BaseModel):
    """Whether bundled scripts could run this run, and under which sandbox.

    Set once per run, never per case: the sandbox decision is made in
    preflight before any case runs. `detail` is the probe's reason -- "bwrap
    not found on PATH", the first line of a refusal -- so a report says why
    the isolation an operator expected was or was not applied.
    """

    sandbox: SandboxBackend
    detail: str


class ScriptNote(BaseModel):
    """A skill bundles scripts, and execution is off.

    On the report rather than printed from the orchestrator so that every
    notice a run produces goes through the reporters -- they render, they
    never decide.
    """

    skill_name: str
    script_count: int
```

In `RunReport`, after `baseline_notes`:

```python
    scripts: ScriptStatus | None = None
    script_notes: list[ScriptNote] = Field(default_factory=list)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/models.py tests/test_models.py
git commit -m "feat(models): add bundle_root, script status and script notes"
```

---

### Task 2: The bundle — `bundle.py`

**Files:**
- Create: `src/skill_lens/bundle.py`
- Test: `tests/test_bundle.py`

**Interfaces:**
- Consumes: `check_relative_path`, `PathRefused` from `skill_lens.workspace`.
- Produces: `BUNDLE_DIRS`, `SCRIPTS_DIR`, `has_bundle(directory: Path) -> bool`,
  `script_extension(candidate: str) -> str`, `SkillBundle(root: Path)` with
  `listing() -> list[str]`, `scripts() -> list[str]`, `read(candidate: str) -> str`,
  `script(candidate: str, interpreters: Mapping[str, Sequence[str]]) -> Path`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bundle.py`:

```python
"""A read-only view of the files a skill ships beside SKILL.md.

The rule these tests protect: the bundle is `scripts/`, `references/` and
`assets/` and nothing else. Eval files sit beside SKILL.md and hold the
expected answers, so "any file beside SKILL.md" would hand the agent its own
answer key.
"""

from __future__ import annotations

import os

import pytest

from skill_lens.bundle import BUNDLE_DIRS, SkillBundle, has_bundle, script_extension
from skill_lens.workspace import PathRefused

INTERPRETERS = {"py": ("python3",), "sh": ("bash",)}


def _skill_dir(tmp_path):
    root = tmp_path / "pdf"
    (root / "scripts").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "assets").mkdir()
    (root / "evals").mkdir()
    (root / "SKILL.md").write_text("---\nname: pdf\n---\nbody\n", encoding="utf-8")
    (root / "pdf.eval.yaml").write_text("cases: []\n", encoding="utf-8")
    (root / "evals" / "more.yaml").write_text("cases: []\n", encoding="utf-8")
    (root / "scripts" / "count.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "scripts" / "nested").mkdir()
    (root / "scripts" / "nested" / "deep.sh").write_text("echo hi\n", encoding="utf-8")
    (root / "scripts" / "data.json").write_text("{}", encoding="utf-8")
    (root / "references" / "style.md").write_text("# Style\n", encoding="utf-8")
    (root / "assets" / "logo.bin").write_bytes(b"\xff\xfe\x00binary")
    return root.resolve()


def test_the_bundle_directories_are_the_agent_skills_three():
    assert BUNDLE_DIRS == ("scripts", "references", "assets")


def test_has_bundle_needs_at_least_one_of_the_three(tmp_path):
    assert has_bundle(_skill_dir(tmp_path)) is True
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "SKILL.md").write_text("x", encoding="utf-8")
    (bare / "evals").mkdir()
    assert has_bundle(bare) is False


def test_listing_covers_the_three_directories_and_nothing_else(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.listing() == [
        "assets/logo.bin",
        "references/style.md",
        "scripts/count.py",
        "scripts/data.json",
        "scripts/nested/deep.sh",
    ]


def test_scripts_lists_only_what_is_under_scripts(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.scripts() == ["scripts/count.py", "scripts/data.json", "scripts/nested/deep.sh"]


def test_read_returns_text(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    assert bundle.read("references/style.md") == "# Style\n"


@pytest.mark.parametrize(
    "candidate",
    ["SKILL.md", "pdf.eval.yaml", "evals/more.yaml", "../pdf/scripts/count.py", "/etc/passwd", ""],
)
def test_read_refuses_everything_outside_the_three_directories(tmp_path, candidate):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused):
        bundle.read(candidate)


def test_read_names_the_three_directories_in_the_refusal(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match="scripts/, references/ or assets/"):
        bundle.read("SKILL.md")


def test_read_of_a_binary_file_raises_the_decode_error_for_the_tool_to_catch(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(UnicodeDecodeError):
        bundle.read("assets/logo.bin")


def test_read_of_a_missing_file_raises_oserror(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(OSError):
        bundle.read("references/missing.md")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_out_of_the_root_is_neither_listed_nor_readable(tmp_path):
    root = _skill_dir(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("hidden", encoding="utf-8")
    (root / "references" / "leak.md").symlink_to(secret)
    bundle = SkillBundle(root)
    assert "references/leak.md" not in bundle.listing()
    with pytest.raises(PathRefused):
        bundle.read("references/leak.md")


def test_script_resolves_a_bundled_script(tmp_path):
    root = _skill_dir(tmp_path)
    bundle = SkillBundle(root)
    assert bundle.script("scripts/count.py", INTERPRETERS) == root / "scripts" / "count.py"
    assert bundle.script("scripts/nested/deep.sh", INTERPRETERS) == (
        root / "scripts" / "nested" / "deep.sh"
    )


def test_script_refuses_a_reference_file(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match="not under scripts/"):
        bundle.script("references/style.md", INTERPRETERS)


def test_script_refuses_a_missing_script_and_lists_the_bundled_ones(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match=r"no such script.*scripts/count\.py, scripts/data\.json"):
        bundle.script("scripts/nope.py", INTERPRETERS)


def test_script_refuses_an_unmapped_extension_and_lists_the_allowed_ones(tmp_path):
    bundle = SkillBundle(_skill_dir(tmp_path))
    with pytest.raises(PathRefused, match="no configured interpreter.*py, sh"):
        bundle.script("scripts/data.json", INTERPRETERS)


def test_script_extension_is_lower_cased_without_the_dot():
    assert script_extension("scripts/Count.PY") == "py"
    assert script_extension("scripts/noext") == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bundle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_lens.bundle'`.

- [ ] **Step 3: Write `bundle.py`**

Create `src/skill_lens/bundle.py`:

```python
"""A read-only view of the files a skill ships beside SKILL.md.

The Agent Skills layout puts three directories next to `SKILL.md`: `scripts/`
(code the agent may run), `references/` (documents it may read) and `assets/`
(files it may use). This module exposes exactly those three and nothing else.
That exclusion is the point: `*.eval.yaml` and `evals/` sit beside `SKILL.md`
too and hold the expected answers, so "any file beside SKILL.md" would hand
the agent its own answer key.

Framework-neutral, and it follows `workspace.py`'s split: methods here
**raise** (`PathRefused`, `OSError`, `UnicodeDecodeError`); the tools built on
top of them in `runners/tools.py` **catch**, because a model asking for a bad
path is an eval signal and an exception would surface it as an infra error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from skill_lens.workspace import PathRefused, check_relative_path

BUNDLE_DIRS: tuple[str, ...] = ("scripts", "references", "assets")
SCRIPTS_DIR = "scripts"


def has_bundle(directory: Path) -> bool:
    """Does `directory` hold at least one of the three bundle directories?"""
    return any((directory / name).is_dir() for name in BUNDLE_DIRS)


def script_extension(candidate: str) -> str:
    """The extension the interpreter map is keyed by: lower case, no dot."""
    return Path(candidate.strip()).suffix.lstrip(".").lower()


@dataclass(frozen=True)
class SkillBundle:
    """The three bundle directories under one skill, read-only.

    `root` is **always already resolved**, for the same reason `Workspace.root`
    is: a containment check that compared `/tmp/...` against `/private/tmp/...`
    would compare two spellings of one directory.
    """

    root: Path

    def _contained(self, candidate: str) -> Path:
        """The absolute path `candidate` names, or raise if it is not bundle."""
        check_relative_path(candidate)
        text = candidate.strip()
        target = (self.root / text).resolve()
        if target == self.root or not target.is_relative_to(self.root):
            raise PathRefused(f"refused: {candidate!r} resolves outside the skill's directory")
        if Path(text).parts[0] not in BUNDLE_DIRS:
            raise PathRefused(
                f"refused: {candidate!r} is not under scripts/, references/ or assets/; "
                "only those three directories are readable"
            )
        return target

    def listing(self) -> list[str]:
        """Every bundled file, relative to the root, sorted, recursive.

        A symbolic link whose target lies outside the root is left out, so the
        listing never advertises a file `read` would refuse.
        """
        found: list[str] = []
        for name in BUNDLE_DIRS:
            directory = self.root / name
            if not directory.is_dir():
                continue
            for item in directory.rglob("*"):
                if item.is_file() and item.resolve().is_relative_to(self.root):
                    found.append(item.relative_to(self.root).as_posix())
        return sorted(found)

    def scripts(self) -> list[str]:
        """The listing, narrowed to `scripts/`."""
        return [entry for entry in self.listing() if entry.split("/", 1)[0] == SCRIPTS_DIR]

    def read(self, candidate: str) -> str:
        """The file's text. Raises OSError if missing, UnicodeDecodeError if binary."""
        return self._contained(candidate).read_text(encoding="utf-8")

    def script(self, candidate: str, interpreters: Mapping[str, Sequence[str]]) -> Path:
        """The absolute path of a runnable script, or raise saying why not.

        Every refusal carries what the model needs for its next call to be
        right: the bundled scripts when the name was wrong, the allowed
        extensions when the interpreter was.
        """
        target = self._contained(candidate)
        if Path(candidate.strip()).parts[0] != SCRIPTS_DIR:
            raise PathRefused(
                f"refused: {candidate!r} is not under scripts/; only bundled scripts can be run"
            )
        if not target.is_file():
            bundled = ", ".join(self.scripts()) or "(none)"
            raise PathRefused(f"refused: no such script {candidate!r}; bundled scripts: {bundled}")
        extension = script_extension(candidate)
        if extension not in interpreters:
            allowed = ", ".join(sorted(interpreters)) or "(none)"
            raise PathRefused(
                f"refused: {candidate!r} has no configured interpreter; "
                f"script_interpreters allows: {allowed}"
            )
        return target
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bundle.py tests/test_framework_isolation.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/bundle.py tests/test_bundle.py
git commit -m "feat: add a read-only view of a skill's bundled files"
```

---

### Task 3: The loader sets `bundle_root`

**Files:**
- Modify: `src/skill_lens/skills/loader.py`
- Test: `tests/test_skill_loader.py`

**Interfaces:**
- Consumes: `has_bundle` from `skill_lens.bundle`.
- Produces: `parse_skill_file` returns a `Skill` whose `bundle_root` is the resolved skill
  directory when `has_bundle` is true, else `None`. `parse_skill_text` never sets it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skill_loader.py`:

```python
def test_bundle_root_is_set_when_a_bundle_directory_exists(tmp_path):
    skill_dir = _write_skill(tmp_path, "pdf")
    (skill_dir / "references").mkdir()
    skill = parse_skill_file(skill_dir / "SKILL.md")
    assert skill.bundle_root == skill_dir.resolve()


def test_bundle_root_is_none_for_a_bare_skill(tmp_path):
    skill_dir = _write_skill(tmp_path, "pdf")
    (skill_dir / "evals").mkdir()
    assert parse_skill_file(skill_dir / "SKILL.md").bundle_root is None


def test_parse_skill_text_never_sets_a_bundle_root(tmp_path):
    skill = parse_skill_text(SKILL_MD, name_fallback="pdf", path=tmp_path, source="x")
    assert skill.bundle_root is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skill_loader.py -v -k bundle_root`
Expected: FAIL — `bundle_root` is `None` in the first test.

- [ ] **Step 3: Set it in `parse_skill_file`**

In `src/skill_lens/skills/loader.py`, import `from skill_lens.bundle import has_bundle` and
change `parse_skill_file`:

```python
def parse_skill_file(skill_md: Path) -> Skill:
    """Parse one SKILL.md into a Skill, falling back to the dir name.

    `bundle_root` is set here and only here (plus the baseline resolver, which
    extracts a previous bundle from git): the text parser cannot know about a
    directory, and a Skill built anywhere else must default to "no bundle".
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillParseError(f"cannot read {skill_md}: {exc}") from exc
    directory = skill_md.parent
    skill = parse_skill_text(
        text,
        name_fallback=directory.name,
        path=directory,
        source=str(skill_md),
    )
    if has_bundle(directory):
        skill = skill.model_copy(update={"bundle_root": directory.resolve()})
    return skill
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skill_loader.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/skills/loader.py tests/test_skill_loader.py
git commit -m "feat(loader): record where a skill's bundle lives"
```

---

### Task 4: `Workspace.over_limit()`

**Files:**
- Modify: `src/skill_lens/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Produces: `Workspace.over_limit() -> str | None` — a message naming the cap and its value
  when `max_files` or `max_total_bytes` is exceeded on disk right now, else `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workspace.py`:

```python
def test_over_limit_is_none_inside_the_caps(tmp_path):
    workspace = Workspace(root=tmp_path.resolve(), limits=WorkspaceLimits(max_files=2))
    workspace.write("a.txt", "a")
    assert workspace.over_limit() is None


def test_over_limit_names_the_file_cap_a_script_blew_through(tmp_path):
    # A script writes to disk directly, so Workspace.write's projection never
    # saw these files. The check after the run is what makes that visible.
    workspace = Workspace(root=tmp_path.resolve(), limits=WorkspaceLimits(max_files=2))
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.txt").write_text(name, encoding="utf-8")
    assert workspace.over_limit() == (
        "warning: the working directory now holds 3 files; max_files is 2"
    )


def test_over_limit_names_the_byte_cap(tmp_path):
    workspace = Workspace(root=tmp_path.resolve(), limits=WorkspaceLimits(max_total_bytes=10))
    (tmp_path / "big.txt").write_text("x" * 11, encoding="utf-8")
    assert workspace.over_limit() == (
        "warning: the working directory now holds 11 bytes; max_total_bytes is 10"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_workspace.py -v -k over_limit`
Expected: FAIL — `AttributeError: 'Workspace' object has no attribute 'over_limit'`.

- [ ] **Step 3: Add the method**

In `Workspace`, after `write`:

```python
    def over_limit(self) -> str | None:
        """A warning if the directory already exceeds a cap, else None.

        For what a script wrote: it goes straight to disk, so `write`'s
        projection never saw it. The message is a warning, not a refusal --
        the bytes are already there -- and every later `write` is refused by
        the projection anyway.
        """
        count, total = self._totals()
        if count > self.limits.max_files:
            return (
                f"warning: the working directory now holds {count:,} files; "
                f"max_files is {self.limits.max_files:,}"
            )
        if total > self.limits.max_total_bytes:
            return (
                f"warning: the working directory now holds {total:,} bytes; "
                f"max_total_bytes is {self.limits.max_total_bytes:,}"
            )
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_workspace.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/workspace.py tests/test_workspace.py
git commit -m "feat(workspace): report a cap a script wrote past"
```

---

### Task 5: `scripts.py` — policy, sandbox builders, probe

**Files:**
- Create: `src/skill_lens/scripts.py`
- Test: `tests/test_scripts.py`

**Interfaces:**
- Consumes: `SandboxMode`, `SandboxBackend` from `skill_lens.models`.
- Produces: `DEFAULT_INTERPRETERS`, `DEFAULT_TIMEOUT_SECONDS = 30.0`,
  `DEFAULT_MAX_OUTPUT_BYTES = 20_000`, `ScriptSetupError`, `ScriptPolicy`, `SandboxStatus`,
  `ScriptRuntime`, `macos_profile(workspace, scratch, tempdir) -> str`,
  `bwrap_argv(workspace, scratch, tempdir, argv) -> list[str]`,
  `wrap(backend, argv, workspace, scratch, tempdir) -> list[str]`,
  `probe_sandbox(mode, *, system=None, which=shutil.which, run=subprocess.run) -> SandboxStatus`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scripts.py`:

```python
"""Running a bundled script: policy, sandbox wrapping, the probe.

`run_script` itself is covered further down this file (Task 6). Everything
here is string-level or injected: no real sandbox is invoked, so the tests
pass on every platform. The real backends are exercised in
tests/test_sandbox_live.py, skipped where absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from skill_lens.scripts import (
    DEFAULT_INTERPRETERS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    SandboxStatus,
    ScriptPolicy,
    bwrap_argv,
    macos_profile,
    probe_sandbox,
    wrap,
)

WS = Path("/private/var/folders/ab/T/skill-lens-x")
SCRATCH = Path("/private/var/folders/ab/T/skill-lens-scratch-y")
TEMPDIR = Path("/private/var/folders/ab/T")


def test_the_defaults_are_the_documented_ones():
    assert dict(DEFAULT_INTERPRETERS) == {"py": ("python3",), "sh": ("bash",)}
    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert DEFAULT_MAX_OUTPUT_BYTES == 20_000
    policy = ScriptPolicy()
    assert policy.sandbox == "auto"
    assert dict(policy.interpreters) == dict(DEFAULT_INTERPRETERS)


def test_the_macos_profile_denies_network_and_writes_then_reallows_the_two_directories():
    profile = macos_profile(WS, SCRATCH, TEMPDIR)
    lines = profile.splitlines()
    assert lines[0] == "(version 1)"
    assert "(allow default)" in lines
    assert "(deny network*)" in lines
    assert "(deny file-write*)" in lines
    assert f'(allow file-write* (subpath "{WS}") (subpath "{SCRATCH}"))' in lines
    assert '(allow file-write-data (literal "/dev/null"))' in lines
    # Sibling workspaces vanish: deny the whole temp dir, re-allow our two.
    assert lines.index(f'(deny file-read* (subpath "{TEMPDIR}"))') < lines.index(
        f'(allow file-read* (subpath "{WS}") (subpath "{SCRATCH}"))'
    )


def test_the_macos_profile_escapes_quotes_and_backslashes_in_paths():
    odd = Path('/tmp/we"ird\\dir')
    profile = macos_profile(odd, SCRATCH, TEMPDIR)
    assert '(subpath "/tmp/we\\"ird\\\\dir")' in profile


def test_the_bwrap_argv_binds_the_two_directories_over_a_read_only_root():
    argv = bwrap_argv(WS, SCRATCH, TEMPDIR, ["python3", "x.py"])
    assert argv[:7] == ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc"]
    tmpfs = argv.index("--tmpfs")
    assert argv[tmpfs + 1] == str(TEMPDIR)
    # The tmpfs must come BEFORE the binds, or it would hide them.
    assert tmpfs < argv.index("--bind")
    assert argv[argv.index("--bind") : argv.index("--bind") + 3] == ["--bind", str(WS), str(WS)]
    for flag in ("--unshare-net", "--unshare-pid", "--die-with-parent", "--new-session"):
        assert flag in argv
    assert argv[-3:] == ["--", "python3", "x.py"]


def test_wrap_leaves_argv_alone_without_a_backend():
    assert wrap("none", ["python3", "x.py"], WS, SCRATCH, TEMPDIR) == ["python3", "x.py"]


def test_wrap_prefixes_sandbox_exec_with_the_profile():
    wrapped = wrap("sandbox-exec", ["python3", "x.py"], WS, SCRATCH, TEMPDIR)
    assert wrapped[:2] == ["sandbox-exec", "-p"]
    assert wrapped[2] == macos_profile(WS, SCRATCH, TEMPDIR)
    assert wrapped[3:] == ["python3", "x.py"]


def _completed(returncode: int, stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=b"", stderr=stderr)


def test_off_never_probes():
    def which(_name):
        raise AssertionError("which must not be called")

    def run(*_a, **_k):
        raise AssertionError("run must not be called")

    status = probe_sandbox("off", system="Darwin", which=which, run=run)
    assert status == SandboxStatus(backend="none", detail='script_sandbox = "off"')


def test_macos_with_a_working_sandbox_exec():
    seen = {}

    def run(argv, **_kwargs):
        seen["argv"] = argv
        return _completed(0)

    status = probe_sandbox("auto", system="Darwin", which=lambda _n: "/usr/bin/sandbox-exec", run=run)
    assert status == SandboxStatus(backend="sandbox-exec", detail="sandbox-exec probe succeeded")
    assert seen["argv"][:2] == ["sandbox-exec", "-p"]
    assert seen["argv"][-1] == "/usr/bin/true"


def test_macos_without_sandbox_exec_on_path():
    status = probe_sandbox("auto", system="Darwin", which=lambda _n: None, run=None)
    assert status == SandboxStatus(backend="none", detail="sandbox-exec not found on PATH")


def test_linux_reports_the_first_stderr_line_of_a_failing_bwrap_probe():
    def run(_argv, **_kwargs):
        return _completed(1, b"bwrap: setting up uid map: Permission denied\nmore\n")

    status = probe_sandbox("auto", system="Linux", which=lambda _n: "/usr/bin/bwrap", run=run)
    assert status == SandboxStatus(
        backend="none",
        detail="bwrap probe failed: bwrap: setting up uid map: Permission denied",
    )


def test_linux_with_a_working_bwrap():
    status = probe_sandbox(
        "auto", system="Linux", which=lambda _n: "/usr/bin/bwrap", run=lambda *_a, **_k: _completed(0)
    )
    assert status == SandboxStatus(backend="bwrap", detail="bwrap probe succeeded")


def test_a_probe_that_cannot_start_is_none_with_the_error():
    def run(*_a, **_k):
        raise OSError("exec format error")

    status = probe_sandbox("auto", system="Linux", which=lambda _n: "/usr/bin/bwrap", run=run)
    assert status.backend == "none"
    assert status.detail == "bwrap probe failed: exec format error"


def test_an_unknown_platform_has_no_backend():
    status = probe_sandbox("auto", system="Windows", which=lambda _n: "x", run=None)
    assert status == SandboxStatus(backend="none", detail="no sandbox backend on Windows")


@pytest.mark.parametrize("mode", ["auto", "required"])
def test_required_and_auto_probe_the_same_way(mode):
    # The difference between them is preflight's decision, not the probe's.
    status = probe_sandbox(mode, system="Darwin", which=lambda _n: None, run=None)
    assert status.backend == "none"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scripts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_lens.scripts'`.

- [ ] **Step 3: Write the first half of `scripts.py`**

Create `src/skill_lens/scripts.py`:

```python
"""Run a script bundled with the skill under test, under guards and a sandbox.

Framework-neutral. Nothing here knows about tools or agents; it turns a policy
and a path into a `ScriptResult`, and `runners/tools.py` renders that for the
model.

Two layers of protection, and the report always says which applied:

* **Portable guards**, on every platform: the environment is rebuilt from an
  allowlist (the key that pays for the run is never in it), temporary files go
  to a scratch directory outside the workspace, a wall-clock timeout kills the
  whole process group, and output is read from files and capped with a visible
  cut.
* **An OS sandbox where one exists**: `sandbox-exec` on macOS, `bwrap` on
  Linux. It blocks the network, writes outside the workspace and the scratch
  directory, and reads of every *other* skill-lens temporary directory.

What the sandbox does not do: hide the rest of the filesystem. A script can
read what the CI user can read, and what it prints reaches the model and the
report. The sandbox is defence in depth; the `allow_scripts` opt-in is the
decision.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skill_lens.bundle import SkillBundle, script_extension
from skill_lens.models import SandboxBackend, SandboxMode, Skill
from skill_lens.workspace import PathRefused, Workspace

DEFAULT_INTERPRETERS: Mapping[str, tuple[str, ...]] = {"py": ("python3",), "sh": ("bash",)}
DEFAULT_TIMEOUT_SECONDS = 30.0
# Equal to evaluators/judge.py's MAX_ARTIFACT_BYTES on purpose: one number for
# "how much untrusted output reaches a model".
DEFAULT_MAX_OUTPUT_BYTES = 20_000
SCRATCH_PREFIX = "skill-lens-scratch-"
PROBE_TIMEOUT_SECONDS = 10.0

# What a script's environment may carry over from the harness. An allowlist,
# never os.environ with keys removed: the provider key skill-lens itself holds
# is absent by construction rather than by remembering to delete it.
_KEPT_ENV: tuple[str, ...] = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
# Python does not start on Windows without SystemRoot.
_KEPT_ENV_WINDOWS: tuple[str, ...] = ("SystemRoot", "COMSPEC", "PATHEXT", "USERPROFILE")


class ScriptSetupError(Exception):
    """Scripts were enabled but cannot run on this machine.

    A missing interpreter, or `script_sandbox = "required"` with no backend.
    Raised by `preflight` before any case runs -- and before any money is
    spent -- and reported as exit 2 by the CLI, like every other setup error.
    """


@dataclass(frozen=True)
class ScriptPolicy:
    """The repository's script settings, built from `Config` by the CLI."""

    sandbox: SandboxMode = "auto"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    interpreters: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_INTERPRETERS)
    )


@dataclass(frozen=True)
class SandboxStatus:
    """Which backend the probe found, and why -- or why not."""

    backend: SandboxBackend
    detail: str


@dataclass(frozen=True)
class ScriptRuntime:
    """What reaches a runner: the policy plus the once-per-run sandbox decision."""

    policy: ScriptPolicy
    sandbox: SandboxStatus


@dataclass(frozen=True)
class ScriptResult:
    """One script call's outcome. Never an exception.

    `refused` set means nothing ran and the message says why, written for the
    model. Otherwise `exit_code` is the process's, or None with `timed_out`.
    `stdout` / `stderr` are already capped, marker included.
    """

    refused: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    workspace_warning: str | None = None


def _quoted(path: Path) -> str:
    """A path as a sandbox-profile string literal.

    A profile is a string, and a string is where injection lives: a `"` in a
    temp path is impossible on macOS, but the builder escapes anyway.
    """
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(workspace: Path, scratch: Path, tempdir: Path) -> str:
    """The `sandbox-exec` profile: allow by default, deny the two things that
    matter, then re-allow the places the script must reach. Later rules win.

    The read denial on the temp directory is what stops a script reading the
    baseline arm's workspace under --concurrency: every workspace and scratch
    directory lives there, and only our own two are allowed back.
    """
    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            "(deny file-write*)",
            f"(allow file-write* (subpath {_quoted(workspace)}) (subpath {_quoted(scratch)}))",
            '(allow file-write-data (literal "/dev/null"))',
            f"(deny file-read* (subpath {_quoted(tempdir)}))",
            f"(allow file-read* (subpath {_quoted(workspace)}) (subpath {_quoted(scratch)}))",
        ]
    )


def bwrap_argv(workspace: Path, scratch: Path, tempdir: Path, argv: Sequence[str]) -> list[str]:
    """The `bwrap` command line: the whole filesystem read-only, the temp
    directory replaced by an empty tmpfs so sibling workspaces vanish, our two
    writable directories bound back in, no network, and `--die-with-parent`
    so a killed harness takes the script with it. Order matters: the tmpfs
    must be mounted before the binds it would otherwise hide.
    """
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", str(tempdir),
        "--bind", str(workspace), str(workspace),
        "--bind", str(scratch), str(scratch),
        "--unshare-net",
        "--unshare-pid",
        "--die-with-parent",
        "--new-session",
        "--",
        *argv,
    ]  # fmt: skip


def wrap(
    backend: SandboxBackend, argv: Sequence[str], workspace: Path, scratch: Path, tempdir: Path
) -> list[str]:
    """argv, wrapped in the backend's launcher -- or unchanged for `none`."""
    if backend == "sandbox-exec":
        return ["sandbox-exec", "-p", macos_profile(workspace, scratch, tempdir), *argv]
    if backend == "bwrap":
        return bwrap_argv(workspace, scratch, tempdir, argv)
    return list(argv)


def _bwrap_probe_argv() -> list[str]:
    return [
        "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
        "--unshare-net", "--unshare-pid", "--die-with-parent", "--", "/bin/true",
    ]  # fmt: skip


def _probe(backend: SandboxBackend, argv: list[str], run: Callable[..., Any]) -> SandboxStatus:
    """Run the backend once. Present is not the same as working."""
    try:
        completed = run(argv, capture_output=True, timeout=PROBE_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SandboxStatus("none", f"{backend} probe failed: {exc}")
    if completed.returncode != 0:
        lines = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        reason = lines[0] if lines else f"exit {completed.returncode}"
        return SandboxStatus("none", f"{backend} probe failed: {reason}")
    return SandboxStatus(backend, f"{backend} probe succeeded")


def probe_sandbox(
    mode: SandboxMode,
    *,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
) -> SandboxStatus:
    """Decide the backend for this run, empirically.

    `system`, `which` and `run` are injectable so the decision table is
    testable on every platform; the defaults are the real thing.
    """
    if mode == "off":
        return SandboxStatus("none", 'script_sandbox = "off"')
    system = platform.system() if system is None else system
    if system == "Darwin":
        if which("sandbox-exec") is None:
            return SandboxStatus("none", "sandbox-exec not found on PATH")
        argv = ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", "/usr/bin/true"]
        return _probe("sandbox-exec", argv, run)
    if system == "Linux":
        if which("bwrap") is None:
            return SandboxStatus("none", "bwrap not found on PATH")
        return _probe("bwrap", _bwrap_probe_argv(), run)
    return SandboxStatus("none", f"no sandbox backend on {system}")
```

`ruff format` will reflow the `# fmt: skip` lists; the comment keeps the one-flag-per-line
shape readable. If ruff complains about the trailing `# fmt: skip` placement, move each
list onto formatted lines instead — readability, not layout, is the requirement.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scripts.py tests/test_framework_isolation.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/scripts.py tests/test_scripts.py
git commit -m "feat(scripts): add the script policy, sandbox wrapping and the probe"
```

---

### Task 6: `scripts.py` — `preflight` and `run_script`

**Files:**
- Modify: `src/skill_lens/scripts.py`
- Test: `tests/test_scripts.py`

**Interfaces:**
- Consumes: `SkillBundle`, `script_extension`; `Workspace.over_limit()` (Task 4).
- Produces: `script_environment(scratch, *, parent=None, windows=None) -> dict[str, str]`,
  `preflight(skills, policy, *, system=None, which=shutil.which, run=subprocess.run) -> ScriptRuntime`,
  `run_script(bundle, workspace, candidate, args, runtime) -> ScriptResult`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scripts.py`:

```python
import os
import sys
import time

from skill_lens.bundle import SkillBundle
from skill_lens.models import Skill
from skill_lens.scripts import (
    SCRATCH_PREFIX,
    ScriptResult,
    ScriptRuntime,
    ScriptSetupError,
    preflight,
    run_script,
    script_environment,
)
from skill_lens.workspace import Workspace, WorkspaceLimits

NO_SANDBOX = SandboxStatus(backend="none", detail="test")


def _policy(**overrides) -> ScriptPolicy:
    # sys.executable, so no test depends on a python3 on PATH.
    settings = {"interpreters": {"py": (sys.executable,)}, "sandbox": "off"}
    settings.update(overrides)
    return ScriptPolicy(**settings)


def _runtime(**overrides) -> ScriptRuntime:
    return ScriptRuntime(policy=_policy(**overrides), sandbox=NO_SANDBOX)


def _bundle(tmp_path, **scripts: str) -> SkillBundle:
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    for name, source in scripts.items():
        (root / "scripts" / name).write_text(source, encoding="utf-8")
    return SkillBundle(root.resolve())


def _workspace(tmp_path, **limits) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(root=root.resolve(), limits=WorkspaceLimits(**limits))


# --- environment -------------------------------------------------------------


def test_the_environment_is_an_allowlist_not_a_denylist(tmp_path):
    parent = {
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "LANG": "C.UTF-8",
        "OPENAI_API_KEY": "sk-secret",
        "AWS_SECRET_ACCESS_KEY": "also-secret",
        "TMPDIR": "/somewhere/else",
    }
    env = script_environment(tmp_path, parent=parent, windows=False)
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/x"
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["TMPDIR"] == env["TMP"] == env["TEMP"] == str(tmp_path)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_windows_keeps_what_python_needs_to_start(tmp_path):
    parent = {"PATH": "C:\\x", "SystemRoot": "C:\\Windows", "COMSPEC": "cmd.exe"}
    assert script_environment(tmp_path, parent=parent, windows=True)["SystemRoot"] == "C:\\Windows"
    assert "SystemRoot" not in script_environment(tmp_path, parent=parent, windows=False)


# --- preflight ---------------------------------------------------------------


def _skill(tmp_path, *scripts: str) -> Skill:
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in scripts:
        (root / "scripts" / name).write_text("", encoding="utf-8")
    return Skill(name="pdf", path=root, bundle_root=root.resolve())


def test_preflight_rejects_a_missing_interpreter_naming_the_script(tmp_path):
    skill = _skill(tmp_path, "count.py")
    policy = ScriptPolicy(sandbox="off", interpreters={"py": ("python3",)})
    with pytest.raises(ScriptSetupError, match=r"count\.py needs python3, which is not on PATH"):
        preflight([skill], policy, which=lambda _n: None)


def test_preflight_ignores_files_with_no_mapped_extension(tmp_path):
    skill = _skill(tmp_path, "data.json", "notes.txt")
    runtime = preflight([skill], ScriptPolicy(sandbox="off"), which=lambda _n: None)
    assert runtime.sandbox.backend == "none"


def test_preflight_skips_skills_without_a_bundle(tmp_path):
    skill = Skill(name="bare", path=tmp_path)
    runtime = preflight([skill], ScriptPolicy(sandbox="off", interpreters={"py": ("nope",)}))
    assert runtime.policy.sandbox == "off"


def test_preflight_required_without_a_backend_is_a_setup_error(tmp_path):
    policy = ScriptPolicy(sandbox="required")
    with pytest.raises(ScriptSetupError, match='script_sandbox = "required" but no sandbox'):
        preflight([], policy, system="Windows")


def test_preflight_auto_without_a_backend_records_the_reason(tmp_path):
    runtime = preflight([], ScriptPolicy(sandbox="auto"), system="Windows")
    assert runtime.sandbox == SandboxStatus(backend="none", detail="no sandbox backend on Windows")


# --- run_script --------------------------------------------------------------

PRINTS = "import sys\nprint('out', *sys.argv[1:])\nprint('err', file=sys.stderr)\nsys.exit(3)\n"


def test_exit_code_stdout_stderr_and_args_round_trip(tmp_path):
    bundle = _bundle(tmp_path, **{"count.py": PRINTS})
    result = run_script(bundle, _workspace(tmp_path), "scripts/count.py", ["a", 2], _runtime())
    assert result.refused is None
    assert result.exit_code == 3
    assert result.timed_out is False
    assert result.stdout.strip() == "out a 2"
    assert result.stderr.strip() == "err"
    assert result.workspace_warning is None


def test_the_working_directory_is_the_workspace(tmp_path):
    bundle = _bundle(tmp_path, **{"cwd.py": "import os; print(os.getcwd())"})
    workspace = _workspace(tmp_path)
    result = run_script(bundle, workspace, "scripts/cwd.py", [], _runtime())
    assert Path(result.stdout.strip()).resolve() == workspace.root


def test_a_script_can_write_into_the_workspace(tmp_path):
    bundle = _bundle(tmp_path, **{"w.py": "open('out.txt', 'w').write('hello')"})
    workspace = _workspace(tmp_path)
    run_script(bundle, workspace, "scripts/w.py", [], _runtime())
    assert workspace.read("out.txt") == "hello"


def test_tmpdir_points_at_a_scratch_directory_that_is_gone_afterwards(tmp_path):
    bundle = _bundle(tmp_path, **{"t.py": "import os, tempfile; print(tempfile.gettempdir())"})
    workspace = _workspace(tmp_path)
    result = run_script(bundle, workspace, "scripts/t.py", [], _runtime())
    scratch = Path(result.stdout.strip())
    assert scratch.name.startswith(SCRATCH_PREFIX)
    assert not scratch.is_relative_to(workspace.root)
    assert not scratch.exists()


def test_the_provider_key_never_reaches_a_script(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-planted")
    bundle = _bundle(tmp_path, **{"env.py": "import os; print(sorted(os.environ))"})
    result = run_script(bundle, _workspace(tmp_path), "scripts/env.py", [], _runtime())
    assert "OPENAI_API_KEY" not in result.stdout
    assert "PATH" in result.stdout


def test_a_script_that_outlives_the_timeout_is_stopped_and_reported(tmp_path):
    bundle = _bundle(tmp_path, **{"sleep.py": "import time; print('start', flush=True); time.sleep(30)"})
    started = time.monotonic()
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/sleep.py", [], _runtime(timeout_seconds=0.5)
    )
    assert time.monotonic() - started < 10
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.stdout.strip() == "start"


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows uses taskkill")
def test_a_timeout_kills_the_grandchild_too(tmp_path):
    # The script starts a sleeper and waits on it. Killing only the direct
    # child would leave the sleeper running until its own timer expired.
    source = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "child.wait()\n"
    )
    bundle = _bundle(tmp_path, **{"spawn.py": source})
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/spawn.py", [], _runtime(timeout_seconds=0.5)
    )
    assert result.timed_out is True
    grandchild = int(result.stdout.strip())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(grandchild, 9)
        pytest.fail("the grandchild survived the group kill")


def test_output_past_the_cap_is_cut_with_the_exact_count(tmp_path):
    bundle = _bundle(tmp_path, **{"big.py": "import sys; sys.stdout.write('x' * 1000)"})
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/big.py", [], _runtime(max_output_bytes=100)
    )
    assert result.stdout == "x" * 100 + "\n... [truncated, 900 bytes omitted]"


def test_output_within_the_cap_carries_no_marker(tmp_path):
    bundle = _bundle(tmp_path, **{"small.py": "import sys; sys.stdout.write('x' * 100)"})
    result = run_script(
        bundle, _workspace(tmp_path), "scripts/small.py", [], _runtime(max_output_bytes=100)
    )
    assert result.stdout == "x" * 100


def test_a_script_writing_past_a_workspace_cap_yields_a_warning(tmp_path):
    bundle = _bundle(tmp_path, **{"fill.py": "open('big.txt', 'w').write('x' * 50)"})
    workspace = _workspace(tmp_path, max_total_bytes=10)
    result = run_script(bundle, workspace, "scripts/fill.py", [], _runtime())
    assert result.workspace_warning == (
        "warning: the working directory now holds 50 bytes; max_total_bytes is 10"
    )


def test_a_refused_path_is_returned_not_raised(tmp_path):
    bundle = _bundle(tmp_path, **{"count.py": PRINTS})
    result = run_script(bundle, _workspace(tmp_path), "scripts/nope.py", [], _runtime())
    assert result == ScriptResult(refused="refused: no such script 'scripts/nope.py'; bundled scripts: scripts/count.py")


def test_an_interpreter_missing_at_call_time_is_a_refusal(tmp_path, monkeypatch):
    # Baseline bundles are not preflighted, so this can happen after preflight.
    bundle = _bundle(tmp_path, **{"x.sh": "echo hi"})
    monkeypatch.setattr("skill_lens.scripts.shutil.which", lambda _n: None)
    runtime = _runtime(interpreters={"sh": ("definitely-not-a-shell",)})
    result = run_script(bundle, _workspace(tmp_path), "scripts/x.sh", [], runtime)
    assert result.refused == (
        "refused: scripts/x.sh needs definitely-not-a-shell, which is not on PATH"
    )


@pytest.mark.skipif(os.name == "nt", reason="shutil.which needs a PATHEXT suffix on Windows")
def test_an_interpreter_that_will_not_start_is_a_refusal(tmp_path):
    # Executable bit set (or shutil.which would return None and the refusal
    # would be "not on PATH"), but not a real binary: exec fails.
    bundle = _bundle(tmp_path, **{"x.py": "print(1)"})
    broken = tmp_path / "broken"
    broken.write_text("not executable", encoding="utf-8")
    broken.chmod(0o755)
    runtime = ScriptRuntime(
        policy=ScriptPolicy(sandbox="off", interpreters={"py": (str(broken),)}), sandbox=NO_SANDBOX
    )
    result = run_script(bundle, _workspace(tmp_path), "scripts/x.py", [], runtime)
    assert result.refused is not None
    assert result.refused.startswith(f"refused: cannot start {broken}")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scripts.py -v`
Expected: FAIL — `ImportError: cannot import name 'preflight'`.

- [ ] **Step 3: Append the second half of `scripts.py`**

Append to `src/skill_lens/scripts.py`:

```python
def script_environment(
    scratch: Path, *, parent: Mapping[str, str] | None = None, windows: bool | None = None
) -> dict[str, str]:
    """The environment a script runs in: rebuilt from an allowlist.

    `parent` and `windows` are injectable for tests; the defaults are the real
    process environment and platform.
    """
    parent = os.environ if parent is None else parent
    windows = os.name == "nt" if windows is None else windows
    keys = _KEPT_ENV + (_KEPT_ENV_WINDOWS if windows else ())
    env = {key: parent[key] for key in keys if key in parent}
    env.update(
        TMPDIR=str(scratch),
        TMP=str(scratch),
        TEMP=str(scratch),
        # The bundle is read-only under the sandbox; without this every import
        # would fill stderr with __pycache__ write errors.
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
    )
    return env


def preflight(
    skills: Sequence[Skill],
    policy: ScriptPolicy,
    *,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
) -> ScriptRuntime:
    """Once per run, after discovery, before any case: can scripts run here?

    Every candidate script whose extension is mapped needs its interpreter on
    PATH; `required` needs a backend. Both fail before any money is spent. A
    file under `scripts/` with an unmapped extension is not an error -- it is
    simply not runnable. Baseline bundles are resolved later, per case, so
    `run_script` looks an interpreter up again at call time and refuses (not
    raises) if it is gone.
    """
    for skill in skills:
        if skill.bundle_root is None:
            continue
        bundle = SkillBundle(skill.bundle_root)
        for relative in bundle.scripts():
            argv = policy.interpreters.get(script_extension(relative))
            if argv is None:
                continue
            if which(argv[0]) is None:
                raise ScriptSetupError(
                    f"{skill.bundle_root / relative} needs {argv[0]}, which is not on PATH"
                )
    status = probe_sandbox(policy.sandbox, system=system, which=which, run=run)
    if policy.sandbox == "required" and status.backend == "none":
        raise ScriptSetupError(
            f'script_sandbox = "required" but no sandbox is available: {status.detail}'
        )
    return ScriptRuntime(policy=policy, sandbox=status)


def _group_kwargs() -> dict[str, Any]:
    """Make the child lead its own process group, so a timeout can kill the tree."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the child and everything it started. Never raises."""
    if os.name == "nt":
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            # S607: taskkill is found on PATH on purpose; it lives in System32
            # on every Windows install and an absolute path would be wrong.
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL cannot be ignored
        pass


def _read_capped(path: Path, budget: int) -> str:
    """The first `budget` bytes of a captured stream, plus an honest marker."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            data = handle.read(budget)
    except OSError as exc:
        return f"(unreadable: {exc})"
    text = data.decode("utf-8", errors="replace")
    if size > budget:
        text += f"\n... [truncated, {size - budget:,} bytes omitted]"
    return text


def run_script(
    bundle: SkillBundle,
    workspace: Workspace,
    candidate: str,
    args: Sequence[object],
    runtime: ScriptRuntime,
) -> ScriptResult:
    """Run one bundled script with the workspace as its working directory.

    Never raises: every outcome, including an interpreter that will not start,
    is a `ScriptResult`. Output goes to files in the scratch directory, not
    into memory -- a script printing gigabytes inside the timeout must not
    take the harness down with it -- and is read back capped.
    """
    policy = runtime.policy
    try:
        target = bundle.script(candidate, policy.interpreters)
    except PathRefused as exc:
        return ScriptResult(refused=str(exc))
    prefix = policy.interpreters[script_extension(candidate)]
    interpreter = shutil.which(prefix[0])
    if interpreter is None:
        return ScriptResult(
            refused=f"refused: {candidate.strip()} needs {prefix[0]}, which is not on PATH"
        )
    argv = [interpreter, *prefix[1:], str(target), *(str(argument) for argument in args)]

    try:
        scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX)).resolve()
    except OSError as exc:
        return ScriptResult(refused=f"refused: cannot create a scratch directory: {exc}")
    try:
        tempdir = Path(tempfile.gettempdir()).resolve()
        wrapped = wrap(runtime.sandbox.backend, argv, workspace.root, scratch, tempdir)
        stdout_path = scratch / "stdout"
        stderr_path = scratch / "stderr"
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            try:
                process = subprocess.Popen(  # noqa: S603 - argv list, shell=False, nothing joined
                    wrapped,
                    cwd=workspace.root,
                    env=script_environment(scratch),
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    **_group_kwargs(),
                )
            except OSError as exc:
                return ScriptResult(refused=f"refused: cannot start {prefix[0]}: {exc}")
            timed_out = False
            exit_code: int | None
            try:
                exit_code = process.wait(timeout=policy.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = None
                _kill_tree(process)
        return ScriptResult(
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=_read_capped(stdout_path, policy.max_output_bytes),
            stderr=_read_capped(stderr_path, policy.max_output_bytes),
            workspace_warning=workspace.over_limit(),
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
```

Note the `refused` message for a missing interpreter at call time uses the *configured*
name (`prefix[0]`) — the same wording preflight uses — while `cannot start` quotes the
resolved path, which is what actually failed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scripts.py -v`
Expected: PASS (the grandchild test is skipped on Windows only).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/scripts.py tests/test_scripts.py
git commit -m "feat(scripts): run a bundled script under portable guards"
```

---

### Task 7: The real sandbox, where it exists

**Files:**
- Create: `tests/test_sandbox_live.py`

**Interfaces:**
- Consumes: `probe_sandbox`, `run_script`, `ScriptRuntime`, `ScriptPolicy`, `SkillBundle`,
  `Workspace`.

- [ ] **Step 1: Write the tests**

Create `tests/test_sandbox_live.py`:

```python
"""The real sandbox backends, on machines that have one.

Skipped where the probe fails (Windows, a Linux without bwrap, a CI runner
whose kernel refuses user namespaces). tests/test_scripts.py covers the
wrapping at string level everywhere; this file is the proof that the strings
do what they claim. Everything here is still offline: the "network" attempt
targets 127.0.0.1 and must FAIL.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from skill_lens.bundle import SkillBundle
from skill_lens.scripts import ScriptPolicy, ScriptRuntime, probe_sandbox, run_script
from skill_lens.workspace import Workspace

STATUS = probe_sandbox("auto")
pytestmark = pytest.mark.skipif(
    STATUS.backend == "none", reason=f"no sandbox on this machine: {STATUS.detail}"
)


def _runtime() -> ScriptRuntime:
    policy = ScriptPolicy(sandbox="auto", interpreters={"py": (sys.executable,)})
    return ScriptRuntime(policy=policy, sandbox=STATUS)


def _run(tmp_path: Path, source: str) -> tuple[Workspace, str, str, int | None]:
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "probe.py").write_text(source, encoding="utf-8")
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    workspace = Workspace(root=ws_root.resolve())
    result = run_script(SkillBundle(root.resolve()), workspace, "scripts/probe.py", [], _runtime())
    assert result.refused is None, result.refused
    return workspace, result.stdout, result.stderr, result.exit_code


def test_the_network_is_blocked(tmp_path):
    source = (
        "import socket\n"
        "s = socket.socket()\n"
        "s.settimeout(2)\n"
        "try:\n"
        "    s.connect(('127.0.0.1', 9))\n"
        "except OSError as exc:\n"
        "    print('blocked', type(exc).__name__)\n"
        "else:\n"
        "    print('connected')\n"
    )
    _, out, _, _ = _run(tmp_path, source)
    assert out.startswith("blocked")


def test_writes_outside_the_workspace_are_refused_and_inside_succeed(tmp_path):
    outside = tmp_path / "outside.txt"
    source = (
        "import pathlib\n"
        f"try:\n    pathlib.Path({str(outside)!r}).write_text('x')\n"
        "    print('outside: wrote')\n"
        "except OSError:\n    print('outside: refused')\n"
        "pathlib.Path('inside.txt').write_text('y')\n"
        "print('inside: wrote')\n"
    )
    workspace, out, err, _ = _run(tmp_path, source)
    assert "outside: refused" in out, err
    assert "inside: wrote" in out
    assert not outside.exists()
    assert workspace.read("inside.txt") == "y"


def test_a_sibling_skill_lens_directory_is_unreadable(tmp_path):
    import tempfile

    sibling = Path(tempfile.mkdtemp(prefix="skill-lens-other-")).resolve()
    try:
        (sibling / "secret.txt").write_text("baseline output", encoding="utf-8")
        source = (
            "import pathlib\n"
            f"try:\n    print(pathlib.Path({str(sibling / 'secret.txt')!r}).read_text())\n"
            "except OSError:\n    print('unreadable')\n"
        )
        _, out, _, _ = _run(tmp_path, source)
        assert out.strip() == "unreadable"
    finally:
        import shutil

        shutil.rmtree(sibling, ignore_errors=True)


def test_the_bundle_is_read_only(tmp_path):
    source = (
        "import pathlib, sys\n"
        "here = pathlib.Path(sys.argv[0]).parent\n"
        "try:\n    (here / 'evil.py').write_text('x')\n    print('wrote')\n"
        "except OSError:\n    print('refused')\n"
    )
    _, out, _, _ = _run(tmp_path, source)
    assert out.strip() == "refused"
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_sandbox_live.py -v`
Expected on this Mac: PASS for all four (the probe succeeds here — verified during design).
On a machine with no backend: all SKIPPED with the probe's detail as the reason.

If a test fails on macOS, the profile is wrong, not the test: fix `macos_profile` in
`scripts.py` (the usual culprits are a missing `(allow file-write-data (literal
"/dev/null"))`, or the temp directory not being resolved — compare `tempfile.gettempdir()`
against `Path(tempfile.gettempdir()).resolve()`), re-run Task 5's string tests, and re-run
this file.

- [ ] **Step 3: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add tests/test_sandbox_live.py
git commit -m "test: prove the real sandbox blocks network, writes and sibling reads"
```

---

### Task 8: The three bundle tools

**Files:**
- Modify: `src/skill_lens/runners/tools.py`
- Modify: `src/skill_lens/cases/loader.py` (one message)
- Test: `tests/test_builtin_tools.py`, `tests/test_case_loader.py`, `tests/test_pydantic_ai_runner.py`

**Interfaces:**
- Consumes: `SkillBundle`, `run_script`, `ScriptRuntime`, `ScriptResult`, `PathRefused`,
  `Workspace`.
- Produces: `WORKSPACE_TOOL_NAMES = ("list_files", "read_file", "write_file")`,
  `BUNDLE_TOOL_NAMES = ("list_skill_files", "read_skill_file", "run_script")`,
  `BUILTIN_TOOL_NAMES = WORKSPACE_TOOL_NAMES + BUNDLE_TOOL_NAMES`,
  `render_script_result(result: ScriptResult, timeout_seconds: float) -> str`,
  `build_bundle_tools(bundle: SkillBundle, workspace: Workspace, runtime: ScriptRuntime | None) -> list[AgentTool]`.

- [ ] **Step 1: Update the two existing tests that assume three built-ins**

In `tests/test_builtin_tools.py`, change the import to also bring `WORKSPACE_TOOL_NAMES` and
change `test_the_three_tools_are_built_under_their_declared_names` to:

```python
def test_the_three_workspace_tools_are_built_under_their_declared_names(tmp_path):
    _, tools = _tools(tmp_path)
    assert set(tools) == set(WORKSPACE_TOOL_NAMES)


def test_the_builtin_names_are_the_workspace_three_plus_the_bundle_three():
    assert BUILTIN_TOOL_NAMES == (
        "list_files",
        "read_file",
        "write_file",
        "list_skill_files",
        "read_skill_file",
        "run_script",
    )
```

In `tests/test_pydantic_ai_runner.py`, import `WORKSPACE_TOOL_NAMES` alongside
`BUILTIN_TOOL_NAMES` and change the assertion in
`test_the_builtin_tools_are_registered_when_a_workspace_is_given` to
`assert set(WORKSPACE_TOOL_NAMES) <= set(seen["tools"])`. Leave
`test_no_builtin_tools_without_a_workspace` on `BUILTIN_TOOL_NAMES` — none of the six may
appear without a workspace.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_builtin_tools.py`:

```python
import sys

from skill_lens.bundle import SkillBundle
from skill_lens.runners.tools import (
    BUNDLE_TOOL_NAMES,
    WORKSPACE_TOOL_NAMES,
    build_bundle_tools,
    render_script_result,
)
from skill_lens.scripts import SandboxStatus, ScriptPolicy, ScriptResult, ScriptRuntime

RUNTIME = ScriptRuntime(
    policy=ScriptPolicy(sandbox="off", interpreters={"py": (sys.executable,)}),
    sandbox=SandboxStatus(backend="none", detail="test"),
)


def _bundle(tmp_path, with_script=True):
    # exist_ok: several tests build the same bundle twice under one tmp_path.
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True, exist_ok=True)
    (root / "references" / "style.md").write_text("# Style\n", encoding="utf-8")
    if with_script:
        (root / "scripts").mkdir(exist_ok=True)
        (root / "scripts" / "hello.py").write_text(
            "import sys; print('hello', *sys.argv[1:])", encoding="utf-8"
        )
    return SkillBundle(root.resolve())


def _bundle_tools(tmp_path, runtime=RUNTIME, with_script=True):
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir(exist_ok=True)
    tools = build_bundle_tools(_bundle(tmp_path, with_script), workspace, runtime)
    return workspace, {tool.name: tool for tool in tools}


def test_the_read_tools_are_always_built_and_run_script_only_with_a_runtime(tmp_path):
    _, with_runtime = _bundle_tools(tmp_path)
    assert set(with_runtime) == set(BUNDLE_TOOL_NAMES)
    _, without = _bundle_tools(tmp_path, runtime=None)
    assert set(without) == {"list_skill_files", "read_skill_file"}


def test_run_script_is_absent_when_the_bundle_has_no_scripts(tmp_path):
    _, tools = _bundle_tools(tmp_path, with_script=False)
    assert "run_script" not in tools


def test_list_skill_files_lists_the_bundle(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    assert tools["list_skill_files"].call() == "references/style.md\nscripts/hello.py"


def test_read_skill_file_reads_and_refuses(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    assert tools["read_skill_file"].call(path="references/style.md") == "# Style\n"
    assert tools["read_skill_file"].call(path="SKILL.md").startswith("refused:")
    assert tools["read_skill_file"].call(path="references/nope.md").startswith(
        "refused: cannot read references/nope.md"
    )
    assert tools["read_skill_file"].call().startswith("refused:")
    assert tools["read_skill_file"].call(path=42).startswith("refused:")


def test_run_script_renders_exit_code_and_both_streams(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    rendered = tools["run_script"].call(path="scripts/hello.py", args=["a", "b"])
    assert rendered == "exit code: 0\nstdout:\nhello a b\n\nstderr:\n(empty)"


def test_run_script_accepts_a_wrongly_shaped_args_without_raising(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    assert "hello single" in tools["run_script"].call(path="scripts/hello.py", args="single")
    assert "hello\n" in tools["run_script"].call(path="scripts/hello.py", args=None)
    assert "hello\n" in tools["run_script"].call(path="scripts/hello.py")


def test_run_script_returns_a_refusal_for_a_bad_path(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    assert tools["run_script"].call(path="references/style.md").startswith("refused:")
    assert tools["run_script"].call().startswith("refused:")


def test_the_descriptions_name_no_skill(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    for tool in tools.values():
        assert "skill" in tool.description.lower()  # they say what they are for...
        assert "hello" not in tool.description  # ...without naming this skill's files


def test_run_script_schema_takes_a_path_and_optional_string_args(tmp_path):
    _, tools = _bundle_tools(tmp_path)
    schema = tools["run_script"].json_schema
    assert schema["required"] == ["path"]
    assert schema["properties"]["args"] == {"type": "array", "items": {"type": "string"}}
    assert schema["additionalProperties"] is False


def test_each_bundle_tool_gets_its_own_schema_object(tmp_path):
    a = {t.name: t for t in build_bundle_tools(_bundle(tmp_path), _bundle_tools(tmp_path)[0], RUNTIME)}
    b = {t.name: t for t in build_bundle_tools(_bundle(tmp_path), _bundle_tools(tmp_path)[0], RUNTIME)}
    for name in a:
        assert a[name].json_schema is not b[name].json_schema


def test_render_script_result_states_a_timeout_and_a_warning():
    result = ScriptResult(timed_out=True, stdout="partial", workspace_warning="warning: too big")
    assert render_script_result(result, 30.0) == (
        "stopped after 30 s (script_timeout_seconds)\nstdout:\npartial\nstderr:\n(empty)\n"
        "warning: too big"
    )


def test_render_script_result_passes_a_refusal_through():
    assert render_script_result(ScriptResult(refused="refused: nope"), 30.0) == "refused: nope"
```

Append to `tests/test_case_loader.py` (the file already has `_write(tmp_path, body)` and
imports `parse_cases_file` and `CaseParseError`):

```python
def test_a_case_tool_named_run_script_collides_with_a_built_in(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    tools:\n"
        "      - name: run_script\n        description: mine\n",
    )
    with pytest.raises(CaseParseError, match="run_script.*collides with a built-in"):
        parse_cases_file(path)


def test_a_trajectory_may_name_run_script_when_a_workspace_exists(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n"
        "    trajectory:\n      called: [run_script, read_skill_file]\n",
    )
    (case,) = parse_cases_file(path)
    assert case.trajectory is not None
    assert case.trajectory.called == ["run_script", "read_skill_file"]


def test_a_trajectory_naming_run_script_without_a_workspace_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    trajectory:\n      called: [run_script]\n",
    )
    with pytest.raises(CaseParseError, match="workspace and bundle tools only exist"):
        parse_cases_file(path)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_builtin_tools.py tests/test_case_loader.py -v`
Expected: FAIL — `ImportError: cannot import name 'WORKSPACE_TOOL_NAMES'`.

- [ ] **Step 4: Implement**

In `src/skill_lens/runners/tools.py`, add imports:

```python
from skill_lens.bundle import SkillBundle
from skill_lens.scripts import ScriptResult, ScriptRuntime, run_script
```

Replace the `BUILTIN_TOOL_NAMES` definition with:

```python
# The names the built-in tools are registered under. `cases/loader.py` reads
# BUILTIN_TOOL_NAMES to reject a case tool that would collide with one, and to
# accept these names in a trajectory block -- both need the answer without
# asking a runner. All six are reserved in every workspace case, bundle or
# not: a name that is sometimes free is a name nobody can rely on.
WORKSPACE_TOOL_NAMES: tuple[str, ...] = ("list_files", "read_file", "write_file")
BUNDLE_TOOL_NAMES: tuple[str, ...] = ("list_skill_files", "read_skill_file", "run_script")
BUILTIN_TOOL_NAMES: tuple[str, ...] = WORKSPACE_TOOL_NAMES + BUNDLE_TOOL_NAMES
```

Append at the end of the file:

```python
def _run_script_schema() -> dict[str, Any]:
    """A fresh schema for run_script. Built per call, like `_path_schema`."""
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["path"],
        "additionalProperties": False,
    }


def render_script_result(result: ScriptResult, timeout_seconds: float) -> str:
    """What the model reads after a script call.

    A refusal passes through as-is. Otherwise: the exit line, then both
    streams (each already capped by `run_script`), then a workspace warning
    if the script wrote past a cap. `(empty)` rather than nothing, so the
    model can tell "no output" from "the tool broke".
    """
    if result.refused is not None:
        return result.refused
    head = (
        f"stopped after {timeout_seconds:g} s (script_timeout_seconds)"
        if result.timed_out
        else f"exit code: {result.exit_code}"
    )
    lines = [head, "stdout:", result.stdout or "(empty)", "stderr:", result.stderr or "(empty)"]
    if result.workspace_warning is not None:
        lines.append(result.workspace_warning)
    return "\n".join(lines)


def build_bundle_tools(
    bundle: SkillBundle, workspace: Workspace, runtime: ScriptRuntime | None
) -> list[AgentTool]:
    """The bundle tools, bound to one skill's bundle and one workspace.

    The two read tools are always built. `run_script` is built only when the
    bundle actually has something under `scripts/` AND the run enabled
    execution (`runtime` is not None) -- a tool that could only ever refuse
    would cost prompt tokens and teach the model nothing.

    Descriptions say what the tools are for without naming the skill or its
    files: `SKILL.md` is where "run scripts/count.py" comes from, and that is
    the thing under measurement.

    Every callable catches, like the workspace tools: a model asking for a bad
    path is an eval signal, and an exception would surface it as an infra
    error.
    """

    def list_skill_files(**_extra: Any) -> str:
        try:
            entries = bundle.listing()
        except OSError as exc:  # pragma: no cover - a directory the loader just saw
            return f"refused: cannot list the skill's files: {exc}"
        return "\n".join(entries) if entries else "(empty)"

    def read_skill_file(path: Any = "", **_extra: Any) -> str:
        target = str(path)
        try:
            return bundle.read(target)
        except PathRefused as exc:
            return str(exc)
        except UnicodeDecodeError:
            return f"refused: the content of {target} is not valid UTF-8 text"
        except UnicodeError:
            return f"refused: {target} could not be handled as UTF-8 text"
        except OSError as exc:
            return f"refused: cannot read {target}: {exc}"

    tools = [
        AgentTool(
            name="list_skill_files",
            description=(
                "List the files bundled with the loaded skill, one path per line, relative "
                "to the skill's directory (scripts/, references/, assets/)."
            ),
            json_schema=_empty_schema(),
            call=list_skill_files,
        ),
        AgentTool(
            name="read_skill_file",
            description=(
                "Read a file bundled with the loaded skill. `path` is relative to the "
                "skill's directory, for example `references/style.md`."
            ),
            json_schema=_path_schema(),
            call=read_skill_file,
        ),
    ]
    if runtime is None or not bundle.scripts():
        return tools

    def run_script_tool(path: Any = "", args: Any = (), **_extra: Any) -> str:
        # A model may send args as a string, a number, null, or nothing. None
        # of those may raise: coerce to a list of strings and let the script
        # see what the model meant.
        if isinstance(args, (list, tuple)):
            arguments: list[object] = list(args)
        elif args in ("", None):
            arguments = []
        else:
            arguments = [args]
        result = run_script(bundle, workspace, str(path), arguments, runtime)
        return render_script_result(result, runtime.policy.timeout_seconds)

    tools.append(
        AgentTool(
            name="run_script",
            description=(
                "Run a script bundled with the loaded skill, with the working directory as "
                "its current directory. `path` is relative to the skill's directory, for "
                "example `scripts/count.py`; `args` are passed as command-line arguments. "
                "Returns the exit code, stdout and stderr."
            ),
            json_schema=_run_script_schema(),
            call=run_script_tool,
        )
    )
    return tools
```

In `src/skill_lens/cases/loader.py`, change the trajectory hint string
`" Built-in file tools only exist in a case with a 'workspace:' block."` to
`" Built-in workspace and bundle tools only exist in a case with a 'workspace:' block."`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_builtin_tools.py tests/test_case_loader.py tests/test_pydantic_ai_runner.py tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/runners/tools.py src/skill_lens/cases/loader.py tests/test_builtin_tools.py tests/test_case_loader.py tests/test_pydantic_ai_runner.py
git commit -m "feat(tools): offer a skill's bundle and its scripts to the agent"
```

---

### Task 9: The runner protocol and the adapters

**Files:**
- Modify: `src/skill_lens/runners/base.py`, `src/skill_lens/runners/fake.py`,
  `src/skill_lens/runners/pydantic_ai.py`
- Test: `tests/test_pydantic_ai_runner.py`, `tests/test_fake_runner.py`

**Interfaces:**
- Produces: `Runner.run(skill, case, workspace=None, scripts: ScriptRuntime | None = None)`.
  `PydanticAIRunner._build_agent(skill, case, workspace, scripts)` registers
  `build_bundle_tools(...)` when `skill.bundle_root` and `workspace` are both set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pydantic_ai_runner.py`:

```python
import sys

from skill_lens.runners.tools import BUNDLE_TOOL_NAMES
from skill_lens.scripts import SandboxStatus, ScriptPolicy, ScriptRuntime

RUNTIME = ScriptRuntime(
    policy=ScriptPolicy(sandbox="off", interpreters={"py": (sys.executable,)}),
    sandbox=SandboxStatus(backend="none", detail="test"),
)


def _bundled_skill(tmp_path):
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "hello.py").write_text("print('hi from script')", encoding="utf-8")
    return SKILL.model_copy(update={"path": root, "bundle_root": root.resolve()})


def _seen_tools(skill, workspace, scripts):
    seen: dict[str, list[str]] = {}

    def reply(messages, info: AgentInfo):
        seen["tools"] = [tool.name for tool in info.function_tools]
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(
        skill, case(), workspace=workspace, scripts=scripts
    )
    return set(seen["tools"])


def test_bundle_tools_are_registered_with_a_bundle_and_a_workspace(tmp_path):
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    tools = _seen_tools(_bundled_skill(tmp_path), workspace, RUNTIME)
    assert set(BUNDLE_TOOL_NAMES) <= tools


def test_no_run_script_without_a_runtime(tmp_path):
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    tools = _seen_tools(_bundled_skill(tmp_path), workspace, None)
    assert {"list_skill_files", "read_skill_file"} <= tools
    assert "run_script" not in tools


def test_no_bundle_tools_without_a_workspace(tmp_path):
    tools = _seen_tools(_bundled_skill(tmp_path), None, RUNTIME)
    assert not set(BUNDLE_TOOL_NAMES) & tools


def test_no_bundle_tools_for_a_skill_without_a_bundle(tmp_path):
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    tools = _seen_tools(SKILL, workspace, RUNTIME)
    assert not set(BUNDLE_TOOL_NAMES) & tools


def test_a_model_running_a_script_gets_its_output_back(tmp_path):
    runner = PydanticAIRunner(
        model=scripted(tool_call("run_script", {"path": "scripts/hello.py"}), text("done"))
    )
    workspace = Workspace(root=(tmp_path / "ws").resolve())
    workspace.root.mkdir()
    result = runner.run(_bundled_skill(tmp_path), case(), workspace=workspace, scripts=RUNTIME)
    assert result.error is None
    assert result.tool_calls[0].name == "run_script"
    # The transcript is pydantic-ai's serialised message list; its exact shape
    # is the adapter's business. What matters is that the script's output
    # reached the model at all.
    assert "hi from script" in str(result.transcript)
```

Append to `tests/test_fake_runner.py`:

```python
def test_the_fake_runner_accepts_and_ignores_the_scripts_keyword():
    from skill_lens.scripts import SandboxStatus, ScriptPolicy, ScriptRuntime

    runtime = ScriptRuntime(
        policy=ScriptPolicy(), sandbox=SandboxStatus(backend="none", detail="test")
    )
    runner = FakeRunner(default=RunResult(output="ok"))
    skill = Skill(name="pdf", path=Path("/tmp/pdf"))
    case = EvalCase(name="x", task="t")
    assert runner.run(skill, case, scripts=runtime).output == "ok"
```

(Adapt the `FakeRunner`, `RunResult`, `Skill`, `EvalCase`, `Path` imports to what that file
already imports.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pydantic_ai_runner.py tests/test_fake_runner.py -v -k "bundle or script"`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'scripts'`.

- [ ] **Step 3: Implement**

`src/skill_lens/runners/base.py` — import `from skill_lens.scripts import ScriptRuntime` and
change the signature and docstring:

```python
    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: ScriptRuntime | None = None,
    ) -> RunResult:
```

Append to the docstring:

```
        `scripts` is the run's script policy plus the sandbox decision, or
        None when execution is off. An adapter offers `run_script` only when
        it is set AND the skill's `bundle_root` has something under
        `scripts/`; the two read tools need only `bundle_root`. Additive with
        a default, so a runner written against Part 1 keeps working.
```

`src/skill_lens/runners/fake.py` — change `run`'s signature to
`def run(self, skill: Skill, case: EvalCase, workspace: Workspace | None = None, scripts: object = None) -> RunResult:`
and add one line to its docstring: "`scripts` is accepted for protocol symmetry and
ignored: a scripted runner runs nothing."

`src/skill_lens/runners/pydantic_ai.py` — add imports:

```python
from skill_lens.bundle import SkillBundle
from skill_lens.runners.tools import build_bundle_tools  # add to the existing import list
from skill_lens.scripts import ScriptRuntime
```

Change `_build_agent`:

```python
    def _build_agent(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None,
        scripts: ScriptRuntime | None,
    ) -> Any:
        from pydantic_ai import Agent, Tool

        built = [build_mock_tool(spec) for spec in case.tools]
        if case.mode == "offered":
            built.append(build_skill_tool(skill))
        if workspace is not None:
            built.extend(build_workspace_tools(workspace))
            # The bundle tools need the workspace: it is the script's working
            # directory and the sandbox's only writable area. Registered in
            # offered mode too -- an agent that declines the skill has no
            # reason to call them, and one that triggers it needs them exactly
            # as a loaded case does.
            if skill.bundle_root is not None:
                built.extend(build_bundle_tools(SkillBundle(skill.bundle_root), workspace, scripts))
```

(the rest of the method is unchanged), and `run`:

```python
    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: ScriptRuntime | None = None,
    ) -> RunResult:
        ...
            agent = self._build_agent(skill, case, workspace, scripts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pydantic_ai_runner.py tests/test_fake_runner.py tests/test_framework_isolation.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/runners tests/test_pydantic_ai_runner.py tests/test_fake_runner.py
git commit -m "feat(runners): hand the script runtime to the adapter"
```

---

### Task 10: Config keys and the `--allow-scripts` flag

**Files:**
- Modify: `src/skill_lens/config.py`, `src/skill_lens/cli.py`
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `Config.allow_scripts: bool = False`, `Config.script_sandbox: SandboxMode = "auto"`,
  `Config.script_timeout_seconds: float` (gt 0, default 30.0),
  `Config.max_script_output_bytes: int` (gt 0, default 20000),
  `Config.script_interpreters: dict[str, list[str]]` (keys normalised, values non-empty),
  `Config.script_policy() -> ScriptPolicy`. CLI: `--allow-scripts/--no-allow-scripts`
  resolved into `resolved_allow_scripts` (wired to the orchestrator in Task 11).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_script_defaults_are_off_auto_and_the_documented_numbers(tmp_path):
    config = load_config(start=tmp_path)
    assert config.allow_scripts is False
    assert config.script_sandbox == "auto"
    assert config.script_timeout_seconds == 30.0
    assert config.max_script_output_bytes == 20_000
    assert config.script_interpreters == {"py": ["python3"], "sh": ["bash"]}


def test_script_keys_load_from_toml(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text(
        'allow_scripts = true\nscript_sandbox = "required"\nscript_timeout_seconds = 5\n'
        "max_script_output_bytes = 100\n\n[script_interpreters]\n"
        'py = ["python3", "-X", "utf8"]\nrb = ["ruby"]\n',
        encoding="utf-8",
    )
    config = load_config(path=path)
    assert config.allow_scripts is True
    assert config.script_sandbox == "required"
    assert config.script_timeout_seconds == 5.0
    assert config.max_script_output_bytes == 100
    # A partial table replaces the default, like any TOML table would.
    assert config.script_interpreters == {"py": ["python3", "-X", "utf8"], "rb": ["ruby"]}


def test_interpreter_keys_are_normalised_to_a_bare_lower_case_extension(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text('[script_interpreters]\n".PY" = ["python3"]\n', encoding="utf-8")
    assert load_config(path=path).script_interpreters == {"py": ["python3"]}


@pytest.mark.parametrize(
    "toml",
    [
        '[script_interpreters]\npy = []\n',
        '[script_interpreters]\n"" = ["python3"]\n',
        'script_sandbox = "firejail"\n',
        "script_timeout_seconds = 0\n",
        "max_script_output_bytes = -1\n",
    ],
)
def test_invalid_script_settings_are_config_errors(tmp_path, toml):
    path = tmp_path / "skill-lens.toml"
    path.write_text(toml, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=path)


def test_script_policy_carries_every_setting_as_tuples(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text(
        'script_sandbox = "off"\nscript_timeout_seconds = 2\nmax_script_output_bytes = 9\n'
        '[script_interpreters]\npy = ["python3", "-B"]\n',
        encoding="utf-8",
    )
    policy = load_config(path=path).script_policy()
    assert policy.sandbox == "off"
    assert policy.timeout_seconds == 2.0
    assert policy.max_output_bytes == 9
    assert policy.interpreters == {"py": ("python3", "-B")}
```

Append to `tests/test_cli.py`:

```python
def test_allow_scripts_flags_override_the_config_in_both_directions(tmp_path):
    # Three states, like --keep-workspace: with allow_scripts = true committed
    # there must still be a way to get a scripts-off run without editing the
    # file. The scripts line is printed only when execution is on.
    skill_dir = _make_skill(tmp_path, cases=WORKSPACE_CASES_YAML)
    config = tmp_path / "skill-lens.toml"
    config.write_text('allow_scripts = true\nscript_sandbox = "off"\n', encoding="utf-8")

    on = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert "scripts: on, sandbox: none" in on.stdout

    off = runner.invoke(app, ["run", str(skill_dir), "--config", str(config), "--no-allow-scripts"])
    assert "scripts: on" not in off.stdout

    flag_on = runner.invoke(app, ["run", str(skill_dir), "--allow-scripts"])
    assert "scripts: on, sandbox:" in flag_on.stdout
```

(This last test passes only after Task 11 wires the flag through and Task 13 prints the
line. Write it now, expect it to fail until then, and re-run it at the end of Task 13.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v -k script`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'allow_scripts'`, and
the TOML test fails validation on the unknown key.

- [ ] **Step 3: Implement the config**

In `src/skill_lens/config.py`, add imports:

```python
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from skill_lens.models import SandboxMode
from skill_lens.scripts import (
    DEFAULT_INTERPRETERS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    ScriptPolicy,
)
```

Add the fields after `max_total_bytes`:

```python
    allow_scripts: bool = False
    script_sandbox: SandboxMode = "auto"
    script_timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    max_script_output_bytes: int = Field(default=DEFAULT_MAX_OUTPUT_BYTES, gt=0)
    script_interpreters: dict[str, list[str]] = Field(
        default_factory=lambda: {ext: list(argv) for ext, argv in DEFAULT_INTERPRETERS.items()}
    )

    @field_validator("script_interpreters")
    @classmethod
    def _normalise_interpreters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        """Keys become a bare lower-case extension; every argv must be non-empty.

        `SkillBundle.script` looks up the lower-cased extension without its
        dot, so `".PY"` and `"py"` have to be the same key or an author's
        spelling would silently never match.
        """
        normalised: dict[str, list[str]] = {}
        for key, argv in value.items():
            extension = key.strip().lstrip(".").lower()
            if not extension:
                raise ValueError(f"script_interpreters has an empty extension key {key!r}")
            if not argv:
                raise ValueError(
                    f"script_interpreters.{key} is empty; name an interpreter, e.g. [\"python3\"]"
                )
            normalised[extension] = list(argv)
        return normalised

    def script_policy(self) -> ScriptPolicy:
        """The script settings as the orchestrator consumes them.

        Does not look at `allow_scripts`: the CLI resolves that against its
        flag and decides whether to pass the policy at all.
        """
        return ScriptPolicy(
            sandbox=self.script_sandbox,
            timeout_seconds=self.script_timeout_seconds,
            max_output_bytes=self.max_script_output_bytes,
            interpreters={ext: tuple(argv) for ext, argv in self.script_interpreters.items()},
        )
```

Add to the class docstring, after the paragraph about the three caps:

```
    `allow_scripts` is the trust switch for M6 part 2: a `SKILL.md` under
    evaluation is unvetted code, and running the scripts bundled with it is a
    decision the operator of the run states, never something an eval file can
    turn on. `--allow-scripts` / `--no-allow-scripts` override it in either
    direction. The four `script_*` keys are repository policy with no per-run
    reason to vary -- config-only, validated here, like the workspace caps.
    Setting them while `allow_scripts` is false is the normal state of a
    repository that turns execution on only in one CI job.
```

- [ ] **Step 4: Add the CLI flag**

In `src/skill_lens/cli.py`, add the option after `full_output`:

```python
    allow_scripts: Annotated[
        bool | None,
        typer.Option(
            "--allow-scripts/--no-allow-scripts",
            help="Run scripts bundled under the skill's scripts/ directory (off by default).",
        ),
    ] = None,
```

and after `resolved_full_output = ...`:

```python
        resolved_allow_scripts = (
            allow_scripts if allow_scripts is not None else settings.allow_scripts
        )
```

`resolved_allow_scripts` is consumed in Task 11; until then ruff will flag it as unused —
that is expected for this one commit, so silence it with a trailing
`# noqa: F841 - wired in the next commit` and remove the noqa in Task 11.

- [ ] **Step 5: The annotated example config**

`tests/test_examples.py::test_the_example_config_mentions_every_key` requires every
`Config` field to appear in `examples/skill-lens.toml`. In that test file, add to
`test_the_example_config_parses_and_sets_what_it_claims`:
`assert config.allow_scripts is True` and `assert config.script_sandbox == "auto"`.

In `examples/skill-lens.toml`, replace the block after the three caps and before
`[per_skill_min]` with:

```toml
# Runaway guards on what one case's workspace may write.
# max_file_bytes = 1000000
# max_files = 200
# max_total_bytes = 5000000

# Run the scripts a skill bundles under scripts/. Off by default: a SKILL.md
# under evaluation is unvetted code, and skill-lens runs in CI. On here so
# that examples/log-triage can run its counting script.
allow_scripts = true

# "auto": use sandbox-exec (macOS) or bwrap (Linux) when the probe succeeds,
# otherwise the portable guards alone; "required" refuses to run without one;
# "off" never probes. The report says which applied on every run.
# script_sandbox = "auto"

# Wall clock per script call; the whole process tree is killed at expiry.
# script_timeout_seconds = 30.0

# Per stream; anything beyond is cut with a visible marker.
# max_script_output_bytes = 20000

# Interpreter per file extension, looked up on PATH. Anything else is refused.
# [script_interpreters]
# py = ["python3"]
# sh = ["bash"]

# A stricter floor for one skill, by name. Must stay the last section: in
# TOML every key after a [table] header belongs to that table.
[per_skill_min]
order-support = 1.0
```

(`[script_interpreters]` stays commented out so `[per_skill_min]` remains the last live
table — the comment above it explains why that matters.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_cli.py tests/test_examples.py -v`
Expected: PASS except `test_allow_scripts_flags_override_the_config_in_both_directions`
(deferred to Task 13).

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/config.py src/skill_lens/cli.py examples/skill-lens.toml tests/test_config.py tests/test_cli.py tests/test_examples.py
git commit -m "feat(config): add allow_scripts, the sandbox mode and the script caps"
```

---

### Task 11: `RunOptions`, preflight and the notice in the orchestrator

**Files:**
- Modify: `src/skill_lens/orchestrator.py`, `src/skill_lens/cli.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Produces: `RunOptions(keep_workspace: bool = False, limits: WorkspaceLimits = DEFAULT_LIMITS, scripts: ScriptPolicy | None = None)`,
  `DEFAULT_OPTIONS = RunOptions()`; `run_evals(..., options: RunOptions | None = None)` —
  the `keep_workspace` and `workspace_limits` parameters are **removed**;
  `_run_one(..., options=DEFAULT_OPTIONS, runtime=None)`; `_run_item(item, evaluators, options, runtime)`;
  `_execute(items, evaluators, concurrency, executor_factory, options=DEFAULT_OPTIONS, runtime=None)`.
  `RunReport.scripts` set from preflight; `RunReport.script_notes` filled when execution is off.
  `ScriptSetupError` in `cli._AUTHORING_ERRORS`.

- [ ] **Step 1: Update the two existing call sites in the tests**

In `tests/test_orchestrator.py`, import `RunOptions` from `skill_lens.orchestrator`, then:
- `test_keep_workspace_leaves_the_directory_and_the_path`: replace `keep_workspace=True,`
  with `options=RunOptions(keep_workspace=True),`.
- `test_configured_limits_reach_the_workspace`: replace
  `workspace_limits=WorkspaceLimits(max_files=7),` with
  `options=RunOptions(limits=WorkspaceLimits(max_files=7)),`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
import sys

from skill_lens.models import ScriptNote
from skill_lens.scripts import ScriptPolicy, ScriptRuntime, ScriptSetupError


def _bundled_skill(tmp_path, *scripts: str) -> Skill:
    root = tmp_path / "bundled"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in scripts:
        (root / "scripts" / name).write_text("print('x')", encoding="utf-8")
    return Skill(name="bundled", description="d", instructions="i", path=root, bundle_root=root.resolve())


class _ScriptAwareRunner(_RecordingRunner):
    """Records the runtime it was handed."""

    def __init__(self) -> None:
        super().__init__()
        self.runtimes: list[ScriptRuntime | None] = []

    def run(self, skill, case, workspace=None, scripts=None):
        self.runtimes.append(scripts)
        return super().run(skill, case, workspace=workspace)


def test_run_options_defaults_reproduce_the_old_behaviour(tmp_path):
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec())
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.scripts is None
    assert report.script_notes == []
    assert report.outcomes[0].result.workspace is None
    assert runner.seen[0].limits == DEFAULT_LIMITS


def test_scripts_off_leaves_a_note_per_skill_that_bundles_scripts(tmp_path):
    runner = _RecordingRunner()
    skill = _bundled_skill(tmp_path, "a.py", "b.sh")
    report = run_evals([skill], [runner], evals_path=_evals(tmp_path, _case()))
    assert report.scripts is None
    assert report.script_notes == [ScriptNote(skill_name="bundled", script_count=2)]


def test_scripts_off_notes_nothing_for_a_bundle_without_scripts(tmp_path):
    runner = _RecordingRunner()
    skill = _bundled_skill(tmp_path)
    (tmp_path / "bundled" / "scripts").rmdir()
    (tmp_path / "bundled" / "references").mkdir()
    report = run_evals([skill], [runner], evals_path=_evals(tmp_path, _case()))
    assert report.script_notes == []


def test_scripts_on_runs_preflight_once_and_hands_the_runtime_to_the_runner(tmp_path):
    runner = _ScriptAwareRunner()
    policy = ScriptPolicy(sandbox="off", interpreters={"py": (sys.executable,)})
    report = run_evals(
        [_bundled_skill(tmp_path, "a.py")],
        [runner],
        evals_path=_evals(tmp_path, _case(workspace=WorkspaceSpec())),
        options=RunOptions(scripts=policy),
    )
    assert report.scripts is not None
    assert report.scripts.sandbox == "none"
    assert report.scripts.detail == 'script_sandbox = "off"'
    assert report.script_notes == []
    (runtime,) = runner.runtimes
    assert runtime is not None and runtime.policy is policy


def test_scripts_off_passes_no_scripts_keyword_so_part_1_runners_keep_working(tmp_path):
    class _PartOneRunner:
        name = "old"

        def run(self, skill, case, workspace=None):
            return RunResult(output="ok")

    report = run_evals([_skill(tmp_path)], [_PartOneRunner()], evals_path=_evals(tmp_path, _case()))
    assert report.outcomes[0].status == "passed"


def test_a_setup_error_aborts_before_any_case_runs(tmp_path):
    runner = _ScriptAwareRunner()
    policy = ScriptPolicy(sandbox="off", interpreters={"py": ("no-such-interpreter-xyz",)})
    with pytest.raises(ScriptSetupError, match="no-such-interpreter-xyz"):
        run_evals(
            [_bundled_skill(tmp_path, "a.py")],
            [runner],
            evals_path=_evals(tmp_path, _case()),
            options=RunOptions(scripts=policy),
        )
    assert runner.runtimes == []
```

Add `DEFAULT_LIMITS` to the `skill_lens.workspace` import at the top of the file.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL — `ImportError: cannot import name 'RunOptions'`.

- [ ] **Step 4: Implement `RunOptions` and the wiring**

In `src/skill_lens/orchestrator.py`:

Imports — add `ScriptNote, ScriptStatus` to the `skill_lens.models` import, and:

```python
from skill_lens.bundle import SkillBundle
from skill_lens.scripts import ScriptPolicy, ScriptRuntime, preflight
```

After the imports, before `_run_one`:

```python
@dataclass(frozen=True)
class RunOptions:
    """Per-run settings that are not part of what an eval asserts.

    Part 1 threaded `keep_workspace` and `limits` as two loose parameters and
    said a bundle could come when there was a third thing. `scripts` is the
    third: None means execution is off, which is the default so that
    upgrading never runs unvetted code on its own.
    """

    keep_workspace: bool = False
    limits: WorkspaceLimits = DEFAULT_LIMITS
    scripts: ScriptPolicy | None = None


DEFAULT_OPTIONS = RunOptions()
```

`_run_one` — replace the `keep_workspace`/`limits` keyword parameters with
`options: RunOptions = DEFAULT_OPTIONS, runtime: ScriptRuntime | None = None`, use
`options.limits` in `create_workspace(...)` and `options.keep_workspace` in both cleanup
checks, and replace the `runner.run(...)` call with:

```python
        # `scripts=` is passed only when execution is on: a runner written
        # against Part 1 has no such parameter and must keep working until
        # the day someone turns scripts on, at which point a TypeError names
        # the runner that cannot take them.
        extra = {"scripts": runtime} if runtime is not None else {}
        result = runner.run(skill, case, workspace=workspace, **extra)
```

`_run_item` — signature `(item, evaluators, options: RunOptions, runtime: ScriptRuntime | None)`,
passing `options=options, runtime=runtime` to `_run_one`.

`_execute` — replace the two parameters with `options: RunOptions = DEFAULT_OPTIONS,
runtime: ScriptRuntime | None = None` and pass `options, runtime` in both the sequential
list comprehension and `executor.submit(...)`.

`run_evals` — replace `keep_workspace: bool = False, workspace_limits: WorkspaceLimits | None = None`
with `options: RunOptions | None = None`; replace the docstring's last paragraph with:

```
    `options` bundles the per-run settings -- keeping workspaces, the
    workspace caps, and the script policy; None means every default. When
    `options.scripts` is set, `preflight` runs once here, after discovery and
    before any case: a missing interpreter or a required sandbox that is
    absent raises `ScriptSetupError` before any money is spent, and the
    sandbox decision is recorded on the report. When it is not set, every
    skill that bundles scripts gets a `ScriptNote` so the report can say
    execution was off.
```

and change the body after `plan = _plan_work(...)`:

```python
    options = options if options is not None else DEFAULT_OPTIONS
    plan = _plan_work(skills, runners, evals_path, tag, case_filter, baseline, repeat)
    runtime: ScriptRuntime | None = None
    status: ScriptStatus | None = None
    notes: list[ScriptNote] = []
    if options.scripts is not None:
        runtime = preflight(skills, options.scripts)
        status = ScriptStatus(sandbox=runtime.sandbox.backend, detail=runtime.sandbox.detail)
    else:
        for skill in skills:
            if skill.bundle_root is None:
                continue
            count = len(SkillBundle(skill.bundle_root).scripts())
            if count:
                notes.append(ScriptNote(skill_name=skill.name, script_count=count))
    outcomes = _execute(
        plan.items, evaluators, concurrency, executor_factory, options, runtime
    )
    return RunReport(
        outcomes=outcomes,
        skipped_skills=plan.skipped,
        tag_filtered_skills=plan.tag_filtered,
        case_filtered_skills=plan.case_filtered,
        baseline_kind=baseline,
        repeat=repeat,
        baseline_notes=plan.notes,
        scripts=status,
        script_notes=notes,
    )
```

(Task 12 wraps `_execute` in a `try/finally` for the baseline store; leave room for it.)

In `src/skill_lens/cli.py`: import `RunOptions` from `skill_lens.orchestrator` and
`ScriptSetupError` from `skill_lens.scripts`; add `ScriptSetupError` to `_AUTHORING_ERRORS`
with a comment `# scripts enabled but cannot run here: a missing interpreter, or a required sandbox that is absent`;
remove the `# noqa: F841` from Task 10; and replace the two keyword arguments in the
`run_evals(...)` call with:

```python
            options=RunOptions(
                keep_workspace=resolved_keep_workspace,
                limits=workspace_limits,
                scripts=settings.script_policy() if resolved_allow_scripts else None,
            ),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py tests/test_cli.py -v`
Expected: PASS except the deferred CLI test from Task 10.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/orchestrator.py src/skill_lens/cli.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): bundle the run options and preflight bundled scripts"
```

---

### Task 12: `--baseline previous` materialises the previous bundle

**Files:**
- Modify: `src/skill_lens/skills/baseline.py`, `src/skill_lens/orchestrator.py`
- Test: `tests/test_baseline_resolution.py`, `tests/test_orchestrator.py`

**Interfaces:**
- Produces: `resolve_previous(skill, *, into: Path) -> Skill | BaselineUnavailable` — the
  keyword is required; the returned `Skill.bundle_root` is a fresh directory under `into`
  holding the commit's `scripts/`, `references/`, `assets/`, or `None` if the commit had
  none. `orchestrator._BaselineStore` with `directory() -> Path` (made on first use) and
  `cleanup()`.

- [ ] **Step 1: Update the existing tests for the new keyword**

In `tests/test_baseline_resolution.py`, add a helper after `_commit` and route every
existing `resolve_previous(...)` call through it:

```python
def _resolve(tmp_path: Path, skill):
    """Resolve with a baseline store under tmp_path, so pytest cleans it up."""
    into = tmp_path / "baselines"
    into.mkdir(exist_ok=True)
    return resolve_previous(skill, into=into)
```

i.e. `resolve_previous(parse_skill_file(repo / "SKILL.md"))` becomes
`_resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))` everywhere in the file.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_baseline_resolution.py`:

```python
def _commit_bundle(repo: Path, files: dict[str, str], message: str) -> None:
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def test_the_previous_bundle_comes_from_the_same_commit_as_its_skill_md(tmp_path):
    repo = _repo(tmp_path)
    _commit_bundle(
        repo,
        {"SKILL.md": _skill_md("1.0.0", "old"), "scripts/count.py": "print('old')",
         "references/style.md": "old style", "pdf.eval.yaml": "cases: []\n"},
        "feat: v1",
    )
    _commit_bundle(
        repo,
        {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/count.py": "print('new')"},
        "feat: v2",
    )

    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))

    assert isinstance(previous, Skill)
    assert previous.bundle_root is not None
    assert previous.bundle_root.is_relative_to((tmp_path / "baselines").resolve())
    assert (previous.bundle_root / "scripts" / "count.py").read_text(encoding="utf-8") == "print('old')"
    assert (previous.bundle_root / "references" / "style.md").read_text(encoding="utf-8") == "old style"
    # Only the three bundle directories are materialised: never the eval file.
    assert not (previous.bundle_root / "pdf.eval.yaml").exists()
    assert not (previous.bundle_root / "SKILL.md").exists()


def test_a_previous_commit_with_no_bundle_yields_no_bundle_root(tmp_path):
    # Never the candidate's: a baseline with bundle_root=None gets no bundle tools.
    repo = _repo(tmp_path)
    _commit(repo, _skill_md("1.0.0", "old"), "feat: v1")
    _commit_bundle(
        repo, {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/new.py": "print(1)"}, "feat: v2"
    )
    previous = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))
    assert isinstance(previous, Skill)
    assert previous.bundle_root is None
    assert list((tmp_path / "baselines").iterdir()) == []


def test_two_resolutions_never_share_a_directory(tmp_path):
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"SKILL.md": _skill_md("1.0.0", "old"), "scripts/a.py": "1"}, "feat: v1")
    _commit_bundle(repo, {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/a.py": "2"}, "feat: v2")
    skill = parse_skill_file(repo / "SKILL.md")
    first = _resolve(tmp_path, skill)
    second = _resolve(tmp_path, skill)
    assert first.bundle_root != second.bundle_root


def test_an_archive_failure_is_unavailable_not_raised(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _commit_bundle(repo, {"SKILL.md": _skill_md("1.0.0", "old"), "scripts/a.py": "1"}, "feat: v1")
    _commit_bundle(repo, {"SKILL.md": _skill_md("1.1.0", "new"), "scripts/a.py": "2"}, "feat: v2")
    import skill_lens.skills.baseline as baseline_module

    real = baseline_module._git_bytes

    def failing(args, cwd):
        return None if args[0] == "archive" else real(args, cwd)

    monkeypatch.setattr(baseline_module, "_git_bytes", failing)
    result = _resolve(tmp_path, parse_skill_file(repo / "SKILL.md"))
    assert isinstance(result, BaselineUnavailable)
    assert "archive" in result.reason
```

Append to `tests/test_orchestrator.py`:

```python
import subprocess


def _git_skill_with_history(tmp_path) -> Skill:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    for version, body in (("1.0.0", "old"), ("1.1.0", "new")):
        (repo / "SKILL.md").write_text(
            f"---\nname: s\nversion: \"{version}\"\n---\n{body}\n", encoding="utf-8"
        )
        (repo / "scripts").mkdir(exist_ok=True)
        (repo / "scripts" / "a.py").write_text(f"print('{body}')", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"feat: {version}"], cwd=repo, check=True)
    from skill_lens.skills.loader import parse_skill_file

    return parse_skill_file(repo / "SKILL.md")


def test_baseline_bundle_directories_are_deleted_when_the_run_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    runner = _ScriptAwareRunner()
    skill = _git_skill_with_history(tmp_path)
    run_evals(
        [skill], [runner], evals_path=_evals(tmp_path, _case(workspace=WorkspaceSpec())),
        baseline="previous",
    )
    assert not list(tmp_path.glob("skill-lens-baselines-*"))


def test_baseline_bundle_directories_are_deleted_even_when_a_case_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    skill = _git_skill_with_history(tmp_path)

    class _Exploding:
        name = "assertion"

        def evaluate(self, case, result):
            raise InvalidAssertionValue("boom")

    with pytest.raises(InvalidAssertionValue):
        run_evals(
            [skill], [_RecordingRunner()], evals_path=_evals(tmp_path, _case()),
            evaluators=[_Exploding()], baseline="previous",
        )
    assert not list(tmp_path.glob("skill-lens-baselines-*"))


def test_the_baseline_arm_sees_the_previous_bundle_and_the_candidate_the_current_one(tmp_path):
    runner = _ScriptAwareRunner()
    skill = _git_skill_with_history(tmp_path)
    seen: dict[str, str] = {}

    class _Peeking(_ScriptAwareRunner):
        def run(self, s, case, workspace=None, scripts=None):
            seen[s.variant] = (s.bundle_root / "scripts" / "a.py").read_text(encoding="utf-8")
            return super().run(s, case, workspace=workspace, scripts=scripts)

    runner = _Peeking()
    run_evals([skill], [runner], evals_path=_evals(tmp_path, _case()), baseline="previous")
    assert seen == {"candidate": "print('new')", "baseline": "print('old')"}
```

Add `import tempfile` to the top of `tests/test_orchestrator.py` if it is not there.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_baseline_resolution.py tests/test_orchestrator.py -v`
Expected: FAIL — `TypeError: resolve_previous() got an unexpected keyword argument 'into'`.

- [ ] **Step 4: Implement the extraction**

In `src/skill_lens/skills/baseline.py`:

Imports — add `import io`, `import tarfile`, `import tempfile`, and
`from skill_lens.bundle import BUNDLE_DIRS` and `from skill_lens.workspace import sanitise_label`.

Split `_git` so a bytes variant exists:

```python
def _git_bytes(args: list[str], cwd: Path) -> bytes | None:
    """Run git in `cwd`; return raw stdout, or None if the command failed."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            # S607: `git` is found on PATH on purpose. An absolute path would
            # be wrong on most machines, and a missing git must come back as
            # BaselineUnavailable (the OSError below), never as a crash.
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _git(args: list[str], cwd: Path) -> str | None:
    """`_git_bytes`, decoded -- for everything that is text."""
    raw = _git_bytes(args, cwd)
    return None if raw is None else raw.decode("utf-8", errors="replace")
```

Add the extractor:

```python
def _materialise_bundle(
    skill: Skill, sha: str, into: Path
) -> Path | None | BaselineUnavailable:
    """The commit's bundle directories, extracted under `into`.

    `git archive <sha> -- .` from the skill directory yields the subtree with
    paths relative to it (`scripts/x.py`), which is what makes filtering on the
    first path component possible. `filter="data"` is the safe extraction
    filter: no absolute paths, no `..`, no link escaping the target. A commit
    with none of the three directories yields None -- the baseline then gets
    no bundle tools, never the candidate's.
    """
    archive = _git_bytes(["archive", "--format=tar", sha, "--", "."], cwd=skill.path)
    if archive is None:
        return BaselineUnavailable(skill.name, f"cannot archive commit {sha[:8]}")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            members = [
                member
                for member in tar.getmembers()
                if member.name.split("/", 1)[0] in BUNDLE_DIRS
            ]
            if not members:
                return None
            target = Path(tempfile.mkdtemp(prefix=f"{sanitise_label(skill.name)}-", dir=into))
            try:
                tar.extractall(target, members=members, filter="data")
            except (tarfile.TarError, OSError, ValueError):
                shutil.rmtree(target, ignore_errors=True)
                raise
    except (tarfile.TarError, OSError, ValueError) as exc:
        return BaselineUnavailable(
            skill.name, f"cannot extract the bundle at commit {sha[:8]}: {exc}"
        )
    return target.resolve()
```

Change `resolve_previous`:

```python
def resolve_previous(skill: Skill, *, into: Path) -> Skill | BaselineUnavailable:
    """The newest earlier version of `skill`, or why there isn't one.

    `into` is the run's baseline-bundle directory (see the orchestrator's
    `_BaselineStore`): the previous bundle is extracted into a fresh
    subdirectory of it, so two resolutions never share one, and the
    orchestrator deletes the whole thing when the run ends. Required, not
    optional: a caller who forgot it would get a baseline whose instructions
    say "run scripts/count.py" against a bundle that does not exist.
    """
```

and, in the loop, replace `return previous.model_copy(update={"variant": "baseline"})` with:

```python
        if _qualifies(previous, skill, blob, working_text):
            bundle_root = _materialise_bundle(skill, sha, into)
            if isinstance(bundle_root, BaselineUnavailable):
                return bundle_root
            return previous.model_copy(update={"variant": "baseline", "bundle_root": bundle_root})
```

In `src/skill_lens/orchestrator.py`:

Add `import shutil`, `import tempfile`. Add after `RunOptions`:

```python
class _BaselineStore:
    """The per-run directory previous bundles are extracted into.

    Made on first use so a run without `--baseline previous` never creates
    it; deleted by `run_evals` in a `finally`, however the run ended. Not
    kept by `--keep-workspace`: a baseline bundle is an input, not an output,
    and the commit it came from is in the report already.
    """

    def __init__(self) -> None:
        self.root: Path | None = None

    def directory(self) -> Path:
        if self.root is None:
            self.root = Path(tempfile.mkdtemp(prefix="skill-lens-baselines-")).resolve()
        return self.root

    def cleanup(self) -> None:
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)
```

`_baseline_skill` gains a `store: _BaselineStore` parameter and calls
`resolve_previous(skill, into=store.directory())`. `_plan_work` gains `store: _BaselineStore`
and passes it through. `run_evals` creates `store = _BaselineStore()` before `_plan_work`,
passes it, and wraps everything from `_plan_work` to the `RunReport(...)` return in:

```python
    store = _BaselineStore()
    try:
        plan = _plan_work(skills, runners, evals_path, tag, case_filter, baseline, repeat, store)
        ...
        outcomes = _execute(plan.items, evaluators, concurrency, executor_factory, options, runtime)
    finally:
        # However the run ended -- an authoring error out of an evaluator
        # included -- the previous bundles go.
        store.cleanup()
    return RunReport(...)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_baseline_resolution.py tests/test_orchestrator.py tests/test_arms.py tests/test_cli.py -v`
Expected: PASS (except the deferred CLI test).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/skills/baseline.py src/skill_lens/orchestrator.py tests/test_baseline_resolution.py tests/test_orchestrator.py
git commit -m "feat(baseline): pair a previous SKILL.md with its own bundle"
```

---

### Task 13: Reporters

**Files:**
- Modify: `src/skill_lens/reporters/console.py`, `reporters/markdown.py`, `reporters/junit.py`,
  `reporters/json_reporter.py`
- Test: `tests/test_reporters.py`, `tests/test_markdown_reporter.py`, `tests/test_junit_reporter.py`,
  `tests/test_cli.py` (the deferred test from Task 10)

**Interfaces:**
- Consumes: `RunReport.scripts`, `RunReport.script_notes`.
- Produces: console line `scripts: on, sandbox: <backend>` (+ ` (<detail>)` when `none`) at
  the top of the run and one `skill <name> bundles N script(s); execution is off
  (allow_scripts = true or --allow-scripts)` line per note; Markdown `_scripts(report)`
  optional block; JUnit `<properties><property name="skill-lens.scripts.sandbox" .../>`
  as the first child of every skill `<testsuite>`; JSON `scripts` and `script_notes`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reporters.py`:

```python
from skill_lens.models import ScriptNote, ScriptStatus


def test_console_says_which_sandbox_ran_the_scripts():
    report = _report().model_copy(
        update={"scripts": ScriptStatus(sandbox="bwrap", detail="bwrap probe succeeded")}
    )
    text = render_console(report)
    assert text.splitlines()[0] == "scripts: on, sandbox: bwrap"


def test_console_explains_a_missing_sandbox():
    report = _report().model_copy(
        update={"scripts": ScriptStatus(sandbox="none", detail="bwrap not found on PATH")}
    )
    assert render_console(report).splitlines()[0] == (
        "scripts: on, sandbox: none (bwrap not found on PATH)"
    )


def test_console_is_silent_about_scripts_when_they_are_off_and_nothing_bundles_any():
    assert "scripts:" not in render_console(_report())


def test_console_notes_bundled_scripts_that_did_not_run():
    report = _report().model_copy(
        update={"script_notes": [ScriptNote(skill_name="pdf", script_count=2)]}
    )
    assert (
        "skill pdf bundles 2 scripts; execution is off (allow_scripts = true or --allow-scripts)"
        in render_console(report)
    )
    one = _report().model_copy(update={"script_notes": [ScriptNote(skill_name="pdf", script_count=1)]})
    assert "bundles 1 script;" in render_console(one)


def test_json_carries_the_script_status_and_notes():
    report = _report().model_copy(
        update={
            "scripts": ScriptStatus(sandbox="sandbox-exec", detail="sandbox-exec probe succeeded"),
            "script_notes": [ScriptNote(skill_name="pdf", script_count=1)],
        }
    )
    payload = json.loads(render_json(report))
    assert payload["scripts"] == {"sandbox": "sandbox-exec", "detail": "sandbox-exec probe succeeded"}
    assert payload["script_notes"] == [{"skill_name": "pdf", "script_count": 1}]
    assert json.loads(render_json(_report()))["scripts"] is None
```

Append to `tests/test_markdown_reporter.py` (uses that file's `_mixed_report()`):

```python
from skill_lens.models import ScriptNote, ScriptStatus


def test_the_scripts_block_states_the_sandbox_and_the_notes():
    report = _mixed_report().model_copy(
        update={
            "scripts": ScriptStatus(sandbox="none", detail="no sandbox backend on Windows"),
            "script_notes": [ScriptNote(skill_name="pdf", script_count=3)],
        }
    )
    text = render_markdown(report)
    assert "Scripts: on, sandbox: none (no sandbox backend on Windows)" in text
    assert "`pdf` bundles 3 scripts; execution is off" in text


def test_the_scripts_block_is_optional_and_dropped_under_truncation():
    report = _mixed_report().model_copy(
        update={"scripts": ScriptStatus(sandbox="bwrap", detail="bwrap probe succeeded")}
    )
    full = render_markdown(report)
    assert "Scripts: on" in full
    # A budget that holds the verdict and summary but not every optional block
    # drops blocks from the end; the scripts line is one of them.
    trimmed = render_markdown(report, max_chars=len(full) - 1)
    assert len(trimmed) <= len(full) - 1
    assert "Scripts: on" not in trimmed
```

Append to `tests/test_junit_reporter.py` (uses that file's `_outcome` and `_parse`):

```python
from skill_lens.models import ScriptStatus


def test_the_sandbox_is_a_suite_property_when_scripts_ran():
    report = RunReport(
        outcomes=[_outcome()],
        scripts=ScriptStatus(sandbox="sandbox-exec", detail="sandbox-exec probe succeeded"),
    )
    root = _parse(report)
    suite = root.find("testsuite")
    prop = suite.find("properties/property")
    assert prop is not None
    assert prop.get("name") == "skill-lens.scripts.sandbox"
    assert prop.get("value") == "sandbox-exec"


def test_no_properties_element_when_scripts_are_off():
    root = _parse(RunReport(outcomes=[_outcome()]))
    assert root.find("testsuite/properties") is None
```

(Adapt `RunReport` to that file's imports.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reporters.py tests/test_markdown_reporter.py tests/test_junit_reporter.py -v -k "script or sandbox"`
Expected: FAIL on the assertions (the models already accept the fields).

- [ ] **Step 3: Implement**

`src/skill_lens/reporters/console.py` — add before `_kept_workspaces`:

```python
def _script_lines(report: RunReport) -> list[str]:
    """One line saying whether scripts ran and under which sandbox.

    Printed on every run that enabled scripts, so an operator can tell from
    the log whether the isolation they expected was applied -- `none` says
    why. A skill whose scripts did not run because execution is off gets a
    line too, however execution was left off: a bundled script that silently
    never runs would look like a skill that does not need it.
    """
    lines: list[str] = []
    if report.scripts is not None:
        line = f"scripts: on, sandbox: {report.scripts.sandbox}"
        if report.scripts.sandbox == "none":
            line += f" ({report.scripts.detail})"
        lines.append(line)
    for note in report.script_notes:
        plural = "" if note.script_count == 1 else "s"
        lines.append(
            f"skill {note.skill_name} bundles {note.script_count} script{plural}; "
            "execution is off (allow_scripts = true or --allow-scripts)"
        )
    return lines
```

In `render_console`, replace `lines: list[str] = []` with:

```python
    lines: list[str] = _script_lines(report)
    if lines:
        lines.append("")
```

`src/skill_lens/reporters/markdown.py` — add after `_skipped`:

```python
def _scripts(report: RunReport) -> str:
    """The sandbox line and the not-run notes, as one optional block."""
    bits = []
    if report.scripts is not None:
        line = f"Scripts: on, sandbox: {_escape(report.scripts.sandbox)}"
        if report.scripts.sandbox == "none":
            line += f" ({_escape(report.scripts.detail)})"
        bits.append(line)
    for note in report.script_notes:
        plural = "" if note.script_count == 1 else "s"
        bits.append(
            f"`{_escape(note.skill_name)}` bundles {note.script_count} script{plural}; "
            "execution is off (`allow_scripts = true` or `--allow-scripts`)"
        )
    return "<sub>" + "<br>".join(bits) + "</sub>" if bits else ""
```

and add `_scripts(report),` as the last entry of the `optional` list in `render_markdown`.

`src/skill_lens/reporters/junit.py` — in `render_junit`, right after
`suite = SubElement(root, "testsuite", name=_xml_safe(skill_name))`:

```python
        if report.scripts is not None:
            # Properties are where JUnit puts run-level facts; a testcase is
            # the wrong place for something true of the whole run.
            properties = SubElement(suite, "properties")
            SubElement(
                properties,
                "property",
                name="skill-lens.scripts.sandbox",
                value=_xml_safe(report.scripts.sandbox),
            )
            SubElement(
                properties,
                "property",
                name="skill-lens.scripts.detail",
                value=_xml_safe(report.scripts.detail),
            )
```

`src/skill_lens/reporters/json_reporter.py` — after the `baseline_notes` line:

```python
    payload["scripts"] = report.scripts.model_dump() if report.scripts is not None else None
    payload["script_notes"] = [note.model_dump() for note in report.script_notes]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_reporters.py tests/test_markdown_reporter.py tests/test_junit_reporter.py tests/test_cli.py -v`
Expected: PASS, including Task 10's deferred
`test_allow_scripts_flags_override_the_config_in_both_directions`.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/reporters tests/test_reporters.py tests/test_markdown_reporter.py tests/test_junit_reporter.py
git commit -m "feat(reporters): say which sandbox ran the scripts, and which scripts did not run"
```

---

### Task 14: The `log-triage` example

**Files:**
- Create: `examples/log-triage/SKILL.md`, `examples/log-triage/scripts/count_levels.py`,
  `examples/log-triage/references/report-format.md`, `examples/log-triage/log-triage.eval.yaml`
- Test: `tests/test_examples.py`

(`examples/skill-lens.toml` already carries the script keys — Task 10 did that so the
config test never went red in between.)

- [ ] **Step 1: Update the examples tests**

In `tests/test_examples.py`:
- `test_every_example_skill_is_discovered`: expected list becomes
  `["csv-report", "greeting", "log-triage", "order-support"]`.
- Append:

```python
import subprocess
import sys


def test_log_triage_bundles_a_script_and_a_reference():
    skill = next(s for s in load_skills(EXAMPLES) if s.name == "log-triage")
    assert skill.bundle_root == (EXAMPLES / "log-triage").resolve()
    from skill_lens.bundle import SkillBundle

    assert SkillBundle(skill.bundle_root).listing() == [
        "references/report-format.md",
        "scripts/count_levels.py",
    ]


def test_the_log_triage_script_counts_levels_with_the_standard_library_only(tmp_path):
    # The eval's expected counts are computed by this script; if it drifts,
    # the recorded cassette and the assertions drift with it.
    (tmp_path / "app.log").write_text(
        "2026-01-01 INFO start\n2026-01-01 ERROR db down\n2026-01-01 WARN slow\n"
        "2026-01-01 ERROR db down again\n2026-01-01 INFO done\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(EXAMPLES / "log-triage" / "scripts" / "count_levels.py"), "app.log"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout == "ERROR: 2\nINFO: 2\nWARN: 1\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_examples.py -v`
Expected: FAIL — the discovered names list lacks `log-triage`.

- [ ] **Step 3: Write the example**

`examples/log-triage/SKILL.md`:

```markdown
---
name: log-triage
description: Count log lines per level with the bundled script and write a triage report
version: "1.0.0"
---

# log-triage

When asked to triage a log file:

1. Run `scripts/count_levels.py` with `run_script`, passing the log file's name as its
   one argument. It prints one `LEVEL: count` line per level, sorted by level name.
   Do not count the lines yourself; the script is the source of truth.
2. Read `references/report-format.md` with `read_skill_file` and follow it exactly.
3. Write `triage.md` with `write_file`.

Never report a level the script did not print.
```

`examples/log-triage/scripts/count_levels.py`:

```python
"""Count log lines per level. Standard library only.

Usage: count_levels.py <logfile>

Each line is `<timestamp> <LEVEL> <message>`. Prints `LEVEL: n`, one per
level, sorted by level name, so the output is stable for an eval to assert on.
"""

import sys
from collections import Counter
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: count_levels.py <logfile>", file=sys.stderr)
        return 2
    counts: Counter[str] = Counter()
    for line in Path(argv[1]).read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 2:
            counts[parts[1]] += 1
    for level in sorted(counts):
        print(f"{level}: {counts[level]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

`examples/log-triage/references/report-format.md`:

```markdown
# Triage report format

`triage.md` has exactly this shape:

    # Log triage

    - ERROR: <n>
    - WARN: <n>
    - INFO: <n>

    **Most frequent: <LEVEL>**

One bullet per level the script reported, in the order ERROR, WARN, INFO, then any
other level alphabetically. Omit a level the script did not report. The last line
names the level with the highest count.
```

`examples/log-triage/log-triage.eval.yaml`:

```yaml
# The reference eval suite for M6 part 2: a skill that can only pass by running
# the script it ships with. The counts below are what count_levels.py prints
# for the seeded log; a model that guesses gets them wrong.
#
# Runs only with `allow_scripts = true` (examples/skill-lens.toml sets it) or
# `--allow-scripts`; with execution off the agent has no run_script tool, and
# the trajectory check fails -- which is the honest result.
cases:
  - name: triages a short application log
    task: Triage app.log and write triage.md.
    workspace:
      files:
        app.log: |
          2026-09-12T10:00:01 INFO service started
          2026-09-12T10:00:02 INFO listening on :8080
          2026-09-12T10:01:15 WARN slow query 1.2s
          2026-09-12T10:02:00 ERROR db connection lost
          2026-09-12T10:02:01 ERROR retry failed
          2026-09-12T10:02:05 ERROR retry failed
          2026-09-12T10:03:00 INFO db reconnected
          2026-09-12T10:04:00 WARN slow query 1.4s
    trajectory:
      called: [run_script, read_skill_file, write_file]
    assertions:
      - kind: file-produced
        file: triage.md
      - kind: contains
        value: "ERROR: 3"
        file: triage.md
      - kind: contains
        value: "WARN: 2"
        file: triage.md
      - kind: contains
        value: "INFO: 3"
        file: triage.md
      - kind: contains
        value: "Most frequent: ERROR"
        file: triage.md
      - kind: not_contains
        value: "DEBUG"
        file: triage.md
```

- [ ] **Step 4: Run the tests and the self-check**

Run: `uv run pytest tests/test_examples.py -v && uv run skill-lens list ./examples`
Expected: PASS, and the listing shows `log-triage	1 case(s)	...`.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add examples tests/test_examples.py
git commit -m "docs(examples): add log-triage, a skill that runs its bundled script"
```

---

### Task 15: A recorded `run_script` call

**Files:**
- Modify: `tests/test_cassettes.py`
- Create: `tests/cassettes/test_a_real_agent_runs_a_bundled_script.yaml` (recorded)

- [ ] **Step 1: Write the test**

Append to `tests/test_cassettes.py`:

```python
import shutil
import sys
from pathlib import Path as _Path

from skill_lens.cases.loader import load_cases_for_skill
from skill_lens.scripts import ScriptPolicy, ScriptRuntime, SandboxStatus
from skill_lens.skills.loader import load_skills
from skill_lens.workspace import create_workspace

EXAMPLES = _Path(__file__).parent.parent / "examples"


@pytest.mark.cassette
@pytest.mark.vcr
@pytest.mark.skipif(shutil.which("python3") is None, reason="the example maps .py to python3")
def test_a_real_agent_runs_a_bundled_script(replay):
    # Replay runs the script locally, so the recording stays deterministic:
    # the provider traffic is the cassette, the subprocess is real. The
    # sandbox is off here so the recording does not depend on the recording
    # machine's backend.
    skill = next(s for s in load_skills(EXAMPLES / "log-triage"))
    (case,) = load_cases_for_skill(skill)
    runtime = ScriptRuntime(
        policy=ScriptPolicy(sandbox="off", interpreters={"py": ("python3",)}),
        sandbox=SandboxStatus(backend="none", detail='script_sandbox = "off"'),
    )
    workspace = create_workspace(case.workspace, label="cassette-log-triage")
    try:
        result = PydanticAIRunner(model="openai:gpt-4o-mini", retries=0).run(
            skill, case, workspace=workspace, scripts=runtime
        )
        assert result.errored is False, result.error
        assert "run_script" in [call.name for call in result.tool_calls]
        assert AssertionEvaluator().evaluate(case, result).passed is True
        assert TrajectoryEvaluator().evaluate(case, result).passed is True
    finally:
        workspace.cleanup()
```

- [ ] **Step 2: Record it if a key is available**

Check: `test -n "$OPENAI_API_KEY" && echo key || echo no-key`

With a key: `uv run pytest tests/test_cassettes.py -k runs_a_bundled_script --record-mode=once -v`
Expected: PASS and a new file under `tests/cassettes/`. Then confirm it replays with no
network and no key: `env -u OPENAI_API_KEY uv run pytest tests/test_cassettes.py -k runs_a_bundled_script -v`
— Expected: PASS. Grep the cassette for `sk-` and `Bearer` — Expected: no matches (the
conftest scrubs headers; a match means the scrub regressed, stop and fix it before committing).

Without a key: `uv run pytest tests/test_cassettes.py -k runs_a_bundled_script -v`
Expected: SKIPPED with the "never recorded" reason. Commit the test alone; the recording is
produced by the cassette-refresh workflow (`docs/releasing.md`) — say so in the PR body.

- [ ] **Step 3: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add tests/test_cassettes.py tests/cassettes
git commit -m "test: record a real agent running a bundled script"
```

---

### Task 16: Documentation

**Files:**
- Modify: `docs/cli.md`, `docs/configuration.md`, `docs/runners.md`, `docs/eval-files.md`,
  `docs/gating.md`, `docs/security.md`, `docs/roadmap.md`, `ARCHITECTURE.md`, `CLAUDE.md`,
  `docs/superpowers/specs/2026-09-10-skill-lens-m6-design.md`
- Test: `tests/test_docs.py` (existing), `mkdocs build --strict`

- [ ] **Step 1: Run the docs tests to see what is missing**

Run: `uv run pytest tests/test_docs.py -v`
Expected: FAIL — `--allow-scripts` undocumented in `docs/cli.md`; `allow_scripts`,
`script_sandbox`, `script_timeout_seconds`, `max_script_output_bytes`,
`script_interpreters` undocumented in `docs/configuration.md`.

- [ ] **Step 2: `docs/cli.md`**

Add `[--allow-scripts | --no-allow-scripts]` to the usage block next to
`--keep-workspace`, and this row to the options table after the `--keep-workspace` row:

```markdown
| `--allow-scripts` / `--no-allow-scripts` | unset | Run the scripts a skill bundles under `scripts/`. Off by default: a `SKILL.md` under evaluation is unvetted code. Overrides the `allow_scripts` config key in either direction; omitting both leaves the config file's value in effect. Every run that enables scripts prints `scripts: on, sandbox: <backend>` so the log shows whether an OS sandbox applied |
```

- [ ] **Step 3: `docs/configuration.md`**

Add rows to the key table after `max_total_bytes`:

```markdown
| `allow_scripts` | `false` | `--allow-scripts` / `--no-allow-scripts` |
| `script_sandbox` | `"auto"` | — |
| `script_timeout_seconds` | `30.0` | — |
| `max_script_output_bytes` | `20000` | — |
| `script_interpreters` | `{ py = ["python3"], sh = ["bash"] }` | — |
```

and, after the paragraph about the three caps, a new section:

```markdown
## Bundled scripts

`allow_scripts` is the trust switch. A skill may ship code under `scripts/` beside
`SKILL.md`; with this key `true` (or `--allow-scripts`) the agent gets a `run_script` tool
that executes it. It is off by default because a `SKILL.md` under evaluation is, by
construction, code nobody has vetted, and skill-lens is built to run in CI — running that
code is a decision the operator of the run states, never something an eval file can turn
on. Reading the bundle (`references/`, `assets/`, `scripts/`) needs no opt-in.

The four `script_*` keys are repository policy, config-only:

- `script_sandbox` — `"auto"` uses an OS sandbox when the once-per-run probe finds one
  (`sandbox-exec` on macOS, `bwrap` on Linux) and the portable guards alone otherwise;
  `"required"` refuses to run scripts at all without one (exit 2, before any case runs);
  `"off"` never probes. See [Running bundled scripts](runners.md#running-bundled-scripts)
  for what each guarantees.
- `script_timeout_seconds` — wall clock per call; the whole process tree is killed at
  expiry and the model is told the script was stopped.
- `max_script_output_bytes` — per stream (stdout, stderr); anything beyond is cut with a
  marker stating exactly how many bytes were omitted. Equal to the judge's per-artifact
  cap on purpose: one number for how much untrusted output reaches a model.
- `script_interpreters` — a table from file extension to the argv prefix that runs it,
  each looked up on `PATH` (so a `uv tool install` of skill-lens, whose venv has none of
  the skill's dependencies, is not what runs the script). Keys are normalised to a bare
  lower-case extension. A script with any other extension is refused with a message that
  lists the allowed ones. A script's own dependencies are the eval's problem: install them
  in the CI job.

```toml
allow_scripts = true
script_sandbox = "required"      # never run unsandboxed on this runner
script_timeout_seconds = 10

[script_interpreters]
py = ["python3", "-X", "utf8"]
sh = ["bash"]
```

Setting `script_*` keys while `allow_scripts` is `false` is fine — it is the normal state of
a repository that turns execution on only in one CI job.
```

- [ ] **Step 4: `docs/runners.md`**

Replace the paragraph beginning **Files beside `SKILL.md` are not loaded.** with a new
section (keep it inside "The workspace" or place it right after — the anchor
`#running-bundled-scripts` must exist because `configuration.md` links to it):

```markdown
## Bundled files and scripts

The Agent Skills layout puts three directories beside `SKILL.md`: `scripts/`,
`references/` and `assets/`. When any of them exists, a case with a `workspace:` block also
gets:

| Tool | Does | Needs |
| --- | --- | --- |
| `list_skill_files` | List the bundled files, relative to the skill's directory | a bundle |
| `read_skill_file` | Read one bundled text file, e.g. `references/style.md` | a bundle |
| `run_script` | Run a file under `scripts/` with the workspace as its current directory | a bundle with scripts **and** `allow_scripts` |

Only those three directories are reachable. `SKILL.md` itself, `*.eval.yaml` and `evals/`
are refused — an eval file holds the expected answers, and a tool that could read it would
hand the agent its answer key. The tools describe themselves without naming the skill; the
instruction to run `scripts/count.py` comes from `SKILL.md`, which is what is under
measurement. The workspace preamble is unchanged and still identical in both arms.

### Running bundled scripts

`run_script(path, args)` runs the interpreter configured for the file's extension
([`script_interpreters`](configuration.md#bundled-scripts)), with the workspace as the
current directory, and returns:

```
exit code: 0
stdout:
ERROR: 3
WARN: 2
stderr:
(empty)
```

or `stopped after 30 s (script_timeout_seconds)` in place of the exit line. Every outcome is
text the model reads — a script that exits non-zero, a missing script (the message lists
the bundled ones), an extension with no interpreter (the message lists the allowed ones).
None of them is `errored`: an unrunnable script is a fact about the skill.

**The portable guards**, on every platform:

- The environment is rebuilt from an allowlist (`PATH`, `HOME`, `LANG`, `LC_ALL`,
  `LC_CTYPE`, `TZ`, plus what Python needs to start on Windows). Nothing else skill-lens
  holds reaches the script — the provider key first among them.
- `TMPDIR`/`TMP`/`TEMP` point at a scratch directory outside the workspace, deleted after
  the call, so a script's temporary files never appear in `list_files`, `file-produced` or
  the judge's artifacts.
- A wall-clock timeout kills the whole process group, not only the direct child.
- stdout and stderr are written to files, not held in memory, and read back capped; a cut
  states the exact number of bytes omitted.
- A script writes to disk directly, so the workspace caps cannot refuse it beforehand.
  After the call the directory is measured, and if it is over `max_files` or
  `max_total_bytes` the tool result ends with a `warning:` line and every later
  `write_file` is refused. The timeout is the real bound on what one script can write.

**The OS sandbox**, where one exists (`script_sandbox = "auto"`, the default):

| | Blocks | Backend |
| --- | --- | --- |
| macOS | network; writes outside the workspace and scratch; reads of other skill-lens temp directories | `sandbox-exec` — deprecated in Apple's documentation, present on current macOS, and what Bazel, Chromium and Claude Code use |
| Linux | the same | `bwrap` (bubblewrap); needs unprivileged user namespaces or a setuid install — not on a stock CI image |
| Windows | nothing | none |

The probe runs once per run, after discovery and before any case: the backend is
*executed*, not merely found on `PATH`, because present is not the same as working (an
Ubuntu 24.04 runner refuses user namespaces under AppArmor). The result is on every report
— `scripts: on, sandbox: bwrap`, or `sandbox: none (bwrap not found on PATH)` — so you can
tell from a log whether the isolation you expected applied. `"required"` turns a missing
backend into exit 2 before any money is spent.

**What the sandbox does not do.** It does not hide the rest of the filesystem: a script
can read whatever the CI user can read (the interpreter and its libraries live there),
and what it prints reaches the model and the report. The sandbox is defence in depth; the
`allow_scripts` opt-in is the decision. Do not enable scripts for a skill you would not
run by hand. See [Security](security.md#running-bundled-scripts).

**Under `--baseline previous`**, the previous version's `scripts/`, `references/` and
`assets/` are extracted from the same commit as its `SKILL.md` into a temporary directory
that is deleted when the run ends. A commit with no bundle gives the baseline no bundle
tools — never the candidate's. `--baseline none` has no bundle: there is no skill.
```

Also update the paragraph **The workspace preamble is identical in both arms** if it lists
the tools: it still names only the three workspace tools, which is correct — add one
sentence: "The bundle tools add nothing to it; they describe themselves."

- [ ] **Step 5: `docs/eval-files.md`**

In the Workspaces section, after the three-tool table, add:

```markdown
A skill that ships `scripts/`, `references/` or `assets/` beside `SKILL.md` also gets
`list_skill_files`, `read_skill_file` and — only when the run enables it — `run_script`.
See [Bundled files and scripts](runners.md#bundled-files-and-scripts). All six names are
reserved in any case with a `workspace:` block, and a `trajectory:` may name any of them:

```yaml
    trajectory:
      called: [run_script, write_file]
```
```

and change "collides with one of these three" to "collides with one of these six".

- [ ] **Step 6: `docs/gating.md`**

In the JSON report section, extend the field list sentence with: "`scripts` (`null` when
execution was off, else `{sandbox, detail}` saying which OS sandbox the run's scripts ran
under) and `script_notes` (skills that bundle scripts which did not run because execution
was off)". In the JUnit section add: "When scripts were enabled, every skill's `<testsuite>`
carries `<properties>` with `skill-lens.scripts.sandbox` and `skill-lens.scripts.detail`."

- [ ] **Step 7: `docs/security.md`**

Add a section before "Why these rules":

```markdown
## Running bundled scripts

A skill may ship code under `scripts/`. skill-lens can execute it — that is what M6 part 2
adds — and the trust model is:

- **Off by default, on only by the operator's decision.** `allow_scripts = true` in
  `skill-lens.toml` or `--allow-scripts`. Nothing in an eval file or a `SKILL.md` can turn
  it on. A `SKILL.md` under evaluation is unvetted code, and skill-lens runs in CI.
- **Portable guards always apply:** an allowlisted environment (no provider key, no
  inherited secret), a scratch directory for temporary files, a timeout that kills the
  process tree, capped output.
- **An OS sandbox applies where one exists** — `sandbox-exec` on macOS, `bwrap` on Linux:
  no network, no writes outside the workspace and scratch, no reads of other skill-lens
  temporary directories. `script_sandbox = "required"` makes its absence a hard failure.
  The report always says which backend applied.
- **What no layer prevents:** a script can read every file the CI user can read, and print
  it, and that output reaches the model and the run report. Treat enabling scripts as
  running the skill's code yourself, because it is.

Details and the per-platform table are in [Running bundled scripts](runners.md#running-bundled-scripts).
```

- [ ] **Step 8: `docs/roadmap.md`**

Change the M6 row status to `shipped`. Replace the paragraph starting "Deferred to part 2"
in "What M6 part 1 shipped" with a pointer, and add:

```markdown
## What M6 part 2 shipped

The agent can now read the files a skill ships beside `SKILL.md` — `scripts/`,
`references/`, `assets/`, and nothing else — through `list_skill_files` and
`read_skill_file`, and, only when the run says `allow_scripts = true` or
`--allow-scripts`, run a bundled script through `run_script` with the workspace as its
working directory. Every script runs under portable guards (an allowlisted environment,
a scratch directory, a process-group timeout, capped output) and under an OS sandbox
where one exists (`sandbox-exec` on macOS, `bwrap` on Linux); the report says which
applied. `--baseline previous` pairs the previous `SKILL.md` with the bundle from the
same commit. `examples/log-triage` is a skill whose eval can only pass by running its
script — and covers the `references/` example M7 deferred. Full detail is in
[Bundled files and scripts](runners.md#bundled-files-and-scripts) and
[Security](security.md#running-bundled-scripts).

Deferred: an `init` scaffold case for script-bearing skills, standard input to scripts,
a per-case timeout, and denying reads outside the workspace.
```

In "What M7 shipped", change "(bundled files are not loaded until M6 part 2)" to
"(shipped with M6 part 2 as `examples/log-triage`)".

- [ ] **Step 9: `ARCHITECTURE.md` and `CLAUDE.md`**

`ARCHITECTURE.md` module map — add rows after `workspace.py`:

```markdown
| `bundle.py` | A read-only view of the three Agent Skills directories beside `SKILL.md` (`scripts/`, `references/`, `assets/`) and nothing else — an eval file beside `SKILL.md` is never readable by the agent. Same "methods raise, tools catch" split as `workspace.py`. |
| `scripts.py` | Runs a bundled script: policy, the once-per-run preflight (interpreters, sandbox probe), the allowlisted environment, the scratch directory, the process-group timeout, capped output, and the `sandbox-exec` / `bwrap` wrapping. Never raises for a script that will not run; raises `ScriptSetupError` only from preflight. |
```

Update the `runners/tools.py` row to mention the bundle tools, the `orchestrator.py` row to
mention `RunOptions`, the preflight and the baseline store, and the `skills/baseline.py`
row to mention that it also extracts the previous bundle via `git archive`. In "Core data
models", add `bundle_root` to `Skill`, and `scripts` / `script_notes` to `RunReport`, plus
rows for `ScriptStatus` and `ScriptNote`.

Add a section after "Real-execution tools (M6 part 1)":

```markdown
### Bundled scripts (M6 part 2)

- **Script execution is off unless the run turned it on.** `allow_scripts` /
  `--allow-scripts` is the only switch; nothing in an eval file or a `SKILL.md` can enable
  it. Reading the bundle needs no opt-in.
- **The bundle is `scripts/`, `references/`, `assets/` and nothing else.** `SkillBundle`
  refuses any other first path component, so `*.eval.yaml` and `evals/` — the expected
  answers — are never readable by the agent.
- **`Skill.bundle_root` defaults to `None`, and only the loader and the baseline resolver
  set it.** Keying on `Skill.path` would leak the candidate's scripts into the `--baseline
  none` arm, since every `Skill` has a path.
- **A script's environment is built from an allowlist, never inherited.** The key that pays
  for the run is absent by construction, not by remembering to delete it.
- **A script runs with `shell=False`, its arguments as argv, always.** Nothing the model
  sends is joined into a command line.
- **A timeout kills the process group, not just the child.** `start_new_session=True` on
  POSIX (`CREATE_NEW_PROCESS_GROUP` + `taskkill /T` on Windows); under `bwrap`,
  `--die-with-parent` also covers the tree.
- **Script output is read from files, capped, and a cut is never silent.** Captured into
  memory, a script printing gigabytes inside the timeout would take the harness down.
- **`run_script` never raises, and an unrunnable script is never `RunResult.error`.** A
  missing script, an unmapped extension, an interpreter that will not start — each is text
  the model reads; that reading is the eval signal.
- **The sandbox decision is made once per run and appears on the report.** `preflight`
  runs after discovery and before any case: `required` without a backend, and a missing
  interpreter for a candidate script, raise `ScriptSetupError` (exit 2) before any money is
  spent. The backend is executed, not merely found on `PATH`.
- **A previous baseline carries its own bundle**, extracted with `git archive <sha> -- .`
  from the same commit as its `SKILL.md` (verified: that form gives subtree-relative paths;
  `<sha>:./` gives an empty archive), filtered to the three directories, with `tarfile`'s
  `data` filter. A commit with no bundle gives `bundle_root=None` — never the candidate's.
- **Baseline bundle directories are deleted when the run ends, however it ends.**
  `_BaselineStore.cleanup()` runs in a `finally` around execution. `--keep-workspace` does
  not keep them: they are an input.
- **Bundle tools require a `workspace:` block, and their names are reserved in every
  workspace case.** The workspace is the script's working directory and the sandbox's only
  writable area; a name that is sometimes free is a name nobody can rely on.
- **The workspace preamble is unchanged** — byte-identical in both arms, naming no skill.
  The bundle tools describe themselves.
- **A script's temporary files never enter the workspace.** `TMPDIR` points at a per-call
  scratch directory outside it, deleted afterwards, so `list_files`, `file-produced` and
  the judge's artifacts see only what the agent and the script deliberately produced.
```

`CLAUDE.md` — update the "Currently at" paragraph to say M6 part 2 is complete and what it
adds (two sentences), add the M6 part 2 spec path to the milestone list, and append these
bullets to the invariants list (condensed forms of the above):

```markdown
- **Script execution is off unless the run turned it on** (`allow_scripts` /
  `--allow-scripts`); nothing in an eval file or a `SKILL.md` can enable it. Reading the
  bundle needs no opt-in.
- **The bundle is `scripts/`, `references/`, `assets/` and nothing else** — an eval file
  beside `SKILL.md` is never readable by the agent.
- **`Skill.bundle_root` defaults to `None`; only the loader and the baseline resolver set
  it**, so the `--baseline none` skill never carries the candidate's scripts.
- **A script's environment is an allowlist, never `os.environ` minus keys**; `shell=False`
  always; a timeout kills the process group; output is read from files, capped, and a cut
  is never silent.
- **`run_script` never raises, and an unrunnable script is never `RunResult.error`.**
- **The sandbox decision is made once per run, in preflight, and appears on every report.**
  `required` without a backend and a missing interpreter abort with exit 2 before any case
  runs; the backend is executed, not merely found.
- **A previous baseline carries its own bundle** from the same commit (`git archive <sha>
  -- .` from the skill directory — `<sha>:./` yields an empty archive), or none; baseline
  bundle directories are deleted in a `finally`, and `--keep-workspace` does not keep them.
- **Bundle tools require a `workspace:` block; all six built-in names are reserved in
  every workspace case; the workspace preamble is unchanged.**
```

Also in `CLAUDE.md`'s documentation table, add a row: "Bundled files, `run_script`, the
sandbox or its guarantees → `docs/runners.md` and `docs/security.md`".

- [ ] **Step 10: The Part 1 spec pointer**

In `docs/superpowers/specs/2026-09-10-skill-lens-m6-design.md`, add one line at the top of
§16: `> Superseded by \`2026-09-12-skill-lens-m6-part2-design.md\`, which answers the three questions below.`
(`docs/superpowers/` is a historical archive; this one line is the only edit.)

- [ ] **Step 11: Build and test the docs**

```bash
uv sync --group docs
uv run mkdocs build --strict
uv run pytest tests/test_docs.py tests/test_naming.py -v
```

Expected: a clean build (every relative link resolves, both new anchors exist) and PASS.

- [ ] **Step 12: Commit**

```bash
git add docs ARCHITECTURE.md CLAUDE.md
git commit -m "docs: document bundled files, run_script, the sandbox and its trust model"
```

---

### Task 17: Whole-suite verification and the pull request

- [ ] **Step 1: The full zero-cost suite, lint, format, audit**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mkdocs build --strict
uv run skill-lens list ./examples
```

Expected: every test passes or is skipped for a stated reason (the cassette if unrecorded,
`test_sandbox_live.py` on a machine with no backend); no lint or format findings; the
listing shows four skills.

- [ ] **Step 2: A real run of the example through the fake runner and through the sandbox**

```bash
uv run skill-lens run examples/log-triage --allow-scripts
```

Expected: the first line is `scripts: on, sandbox: sandbox-exec` on this Mac (or `bwrap` /
`none (...)` elsewhere); the case fails (the fake runner runs nothing, so `run_script` was
never called and `triage.md` was not produced) with exit code 1 — that is the honest
result for a runner that cannot run scripts, not a bug.

```bash
uv run skill-lens run examples/log-triage
```

Expected: the line `skill log-triage bundles 1 script; execution is off (allow_scripts =
true or --allow-scripts)` appears.

If `OPENAI_API_KEY` is set:

```bash
uv run skill-lens run examples/log-triage --config examples/skill-lens.toml
```

Expected: exit 0, `triage.md` assertions all pass, the trajectory shows `run_script`.

- [ ] **Step 3: Review the diff against the spec's §17 invariants**

Open `docs/superpowers/specs/2026-09-12-skill-lens-m6-part2-design.md` §17 and, for each of
the 13 invariants, name the test that pins it. Any invariant without a test gets one now
(most are already in Tasks 2, 6, 8, 11, 12).

- [ ] **Step 4: Open the pull request**

```bash
git push -u origin claude/m6-part-2-implementation-5255f7
gh pr create --assignee @EmadMokhtar --title "feat: run the scripts a skill bundles, under an opt-in and a sandbox" --body-file - <<'BODY'
## Summary

M6 part 2. The agent can read the files a skill ships beside `SKILL.md` (`scripts/`,
`references/`, `assets/` — nothing else, so eval files are never readable), and, only
when the run says `allow_scripts = true` or `--allow-scripts`, run a bundled script with
the workspace as its working directory.

- Portable guards on every platform: allowlisted environment (no provider key), scratch
  directory, process-group timeout, capped output with a visible cut.
- OS sandbox where one exists: `sandbox-exec` (macOS) / `bwrap` (Linux), probed once per
  run; the report says which applied. `script_sandbox = "required"` fails closed.
- `--baseline previous` pairs the previous `SKILL.md` with the bundle from the same commit.
- `examples/log-triage`: a skill whose eval can only pass by running its script.

Spec: `docs/superpowers/specs/2026-09-12-skill-lens-m6-part2-design.md`.
Plan: `docs/superpowers/plans/2026-09-12-skill-lens-m6-part2.md`.

## Test plan

- [ ] `uv run pytest` — zero-cost tier green
- [ ] `tests/test_sandbox_live.py` passes on macOS (sandbox-exec) — run locally
- [ ] cassette `test_a_real_agent_runs_a_bundled_script` recorded / or noted as pending the refresh workflow
- [ ] `uv run mkdocs build --strict`
- [ ] `uv run skill-lens run examples/log-triage --config examples/skill-lens.toml` with a key: exit 0

Closes #<issue-number-for-M6-part-2>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
```

Replace `<issue-number-for-M6-part-2>` with the milestone issue's number (`gh issue list
--label milestone`); if none exists, create one first (`gh issue create --label milestone
--title "M6 part 2: run bundled skill scripts"`) so the PR links to it.
