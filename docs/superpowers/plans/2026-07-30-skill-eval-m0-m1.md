# skill-eval M0+M1 Implementation Plan

> ## ⚠️ Historical record — the code is the source of truth
>
> This is the plan **as approved on 2026-07-30**, kept unedited so the review trail stays
> legible. M0+M1 shipped, and review found real defects in the plan's own code blocks.
> **Do not copy code from this document without checking `src/` first.** The notable
> supersessions, all of which shipped differently:
>
> | Plan snippet | What shipped instead |
> |---|---|
> | `FakeRunner.run` returns stored objects | Returns `model_copy(deep=True)` — the plan's version lets a caller corrupt scripted state (Task 5) |
> | Empty report passes the gate | A run executing **zero cases fails** the gate (Task 8 / final review) |
> | `raw_cases = data["cases"] or []` | Rejects non-list and null `cases:` with `CaseParseError` (Task 4) |
> | Unguarded `re.search(spec.value, ...)` | Raises `InvalidAssertionValue` on a malformed regex (Task 6) |
> | `EvalCase` / `AssertionSpec` with no `model_config` | `extra="forbid"` — a typo'd key silently produced a vacuously-passing case (final review) |
> | Bare `read_text()` / `write_text()` | All file IO pins `encoding="utf-8"` and re-raises as a typed parse error |
> | `Config.reporters` | Removed — it was validated but never honoured; returns in M4 with a real registry |
> | CLI catches 3 exception types | Also catches evaluator authoring errors; `--json-output` creates parent dirs |
> | `per_skill_min` skips absent skills | A configured minimum for a skill that never ran fails the gate (Task 8) |
>
> Two of these are also annotated inline where they appear. See §7 of the design doc for
> the error-handling and exit-code contracts as they actually shipped.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the scaffolding (M0) and the complete deterministic, zero-cost eval engine (M1) — so `skill-eval run <path>` discovers skills, runs their eval cases through a `FakeRunner`, scores them with assertions, reports results, and gates CI with an exit code.

**Architecture:** Two protocols carry the design — `Runner` turns `(skill, task)` into a `RunResult`; `Evaluator` turns `(case, result)` into an `EvalScore`. Everything else (loaders, orchestrator, reporters, gating) is plumbing around those seams. M1 ships `FakeRunner` only, so the entire pipeline is provable in CI with no API calls.

**Tech Stack:** Python 3.11+, uv, Pydantic v2, Typer, pytest, ruff, Commitizen.

Spec: `docs/superpowers/specs/2026-07-30-skill-eval-design.md`

## Global Constraints

- User-facing name is **`skill-eval`** everywhere (command, `skill-eval.toml`, distribution name). The Python import module is `skill_eval` and must never appear in user-facing output.
- Distribution name: `skill-eval`. Package path: `src/skill_eval/`.
- Python requires-python: `>=3.11`.
- Pydantic **v2** for all models.
- Conventional Commits for every commit; Commitizen `version_provider = "uv"`.
- Secrets/API keys come from environment variables only — never from config files.
- **No network calls anywhere in M0/M1.** Every test must pass offline.
- `errored` (infra failure) and `failed` (scored below bar) are distinct states; **errored fails the gate by default**.
- Skills with no eval cases are reported as **skipped**, never silently ignored.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Deps, entry point, ruff/pytest/commitizen config, version |
| `src/skill_eval/__init__.py` | `__version__` via `importlib.metadata` |
| `src/skill_eval/models.py` | All Pydantic models: `Skill`, `EvalCase`, `RunResult`, `EvalScore`, `CaseOutcome`, `RunReport` |
| `src/skill_eval/skills/loader.py` | Walk a path for `SKILL.md` → `[Skill]` |
| `src/skill_eval/cases/loader.py` | Discover + parse eval YAML → `[EvalCase]` |
| `src/skill_eval/runners/base.py` | `Runner` protocol |
| `src/skill_eval/runners/fake.py` | `FakeRunner` — scripted, deterministic |
| `src/skill_eval/evaluators/base.py` | `Evaluator` protocol + registry |
| `src/skill_eval/evaluators/assertion.py` | contains / not_contains / regex / equals |
| `src/skill_eval/orchestrator.py` | skill × case × runner matrix → `RunReport` |
| `src/skill_eval/reporters/console.py` | Human-readable summary |
| `src/skill_eval/reporters/json_reporter.py` | Machine-readable JSON |
| `src/skill_eval/gating.py` | Thresholds → exit code |
| `src/skill_eval/config.py` | Load `skill-eval.toml`, upward discovery |
| `src/skill_eval/cli.py` | Typer app: `run` / `list` |

---

### Task 1: Project scaffolding (M0)

**Files:**
- Create: `pyproject.toml`, `src/skill_eval/__init__.py`, `tests/test_version.py`, `.gitignore`, `README.md`

**Interfaces:**
- Consumes: nothing
- Produces: importable `skill_eval` package with `skill_eval.__version__ -> str`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "skill-eval"
version = "0.1.0"
description = "Run evaluations on Agent Skills (SKILL.md) in CI/CD or manually"
readme = "README.md"
requires-python = ">=3.11"
license = { file = "LICENSE" }
dependencies = [
    "pydantic>=2.7",
    "typer>=0.12",
    "pyyaml>=6.0",
]

[project.scripts]
skill-eval = "skill_eval.cli:app"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.6",
    "commitizen>=3.29",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/skill_eval"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: hits real provider APIs; requires an API key (deselected by default)",
]
addopts = "-m 'not integration'"

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.commitizen]
name = "cz_conventional_commits"
version_provider = "uv"
tag_format = "v$version"
update_changelog_on_bump = true
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
dist/
build/
*.egg-info/
.pytest_cache/
.ruff_cache/
.coverage
```

- [ ] **Step 3: Create `README.md`**

```markdown
# skill-eval

Run evaluations on Agent Skills (`SKILL.md`) — in CI/CD or on demand.

## Install

```bash
uv sync
```

## Usage

```bash
skill-eval run ./skills
```

See `docs/superpowers/specs/` for the design.
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_version.py`:

```python
import skill_eval


def test_version_is_exposed():
    assert isinstance(skill_eval.__version__, str)
    assert skill_eval.__version__
```

- [ ] **Step 5: Run test to verify it fails**

Run: `uv run pytest tests/test_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval'`

- [ ] **Step 6: Create the package**

Create `src/skill_eval/__init__.py`:

```python
"""skill-eval — run evaluations on Agent Skills."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("skill-eval")
except PackageNotFoundError:  # pragma: no cover - only when running from source tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv sync && uv run pytest tests/test_version.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore README.md src/skill_eval/__init__.py tests/test_version.py uv.lock
git commit -m "feat: scaffold skill-eval package with uv and pytest"
```

---

### Task 2: Core models

**Files:**
- Create: `src/skill_eval/models.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Skill(name: str, description: str, instructions: str, path: Path)`
  - `ToolCall(name: str, arguments: dict)`
  - `RunResult(output: str, tool_calls: list[ToolCall], transcript: list[dict], tokens: int, latency_ms: int, cost_usd: float, error: str | None)` with property `errored -> bool`
  - `EvalScore(evaluator: str, passed: bool, score: float, detail: str)`
  - `AssertionSpec(kind: str, value: str)`
  - `EvalCase(name: str, task: str, assertions: list[AssertionSpec], tags: list[str])`
  - `CaseOutcome(skill_name: str, case_name: str, runner: str, status: Literal["passed","failed","errored"], scores: list[EvalScore], result: RunResult | None)`
  - `RunReport(outcomes: list[CaseOutcome], skipped_skills: list[str])` with properties `total`, `passed`, `failed`, `errored`, `pass_rate`, and method `pass_rate_by_skill() -> dict[str, float]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
from pathlib import Path

from skill_eval.models import (
    CaseOutcome,
    EvalScore,
    RunReport,
    RunResult,
    Skill,
    ToolCall,
)


def _result(output="ok", error=None):
    return RunResult(output=output, error=error)


def test_skill_holds_metadata_and_body():
    skill = Skill(
        name="pdf",
        description="Work with PDFs",
        instructions="Do the thing.",
        path=Path("/skills/pdf"),
    )
    assert skill.name == "pdf"
    assert skill.instructions == "Do the thing."


def test_run_result_defaults_are_empty_and_not_errored():
    result = _result()
    assert result.tool_calls == []
    assert result.cost_usd == 0.0
    assert result.errored is False


def test_run_result_with_error_is_errored():
    assert _result(error="boom").errored is True


def test_tool_call_roundtrips_arguments():
    call = ToolCall(name="search", arguments={"q": "x"})
    assert call.arguments["q"] == "x"


def _outcome(skill, case, status):
    return CaseOutcome(
        skill_name=skill,
        case_name=case,
        runner="fake",
        status=status,
        scores=[EvalScore(evaluator="assertion", passed=status == "passed", score=1.0, detail="")],
        result=_result(),
    )


def test_run_report_aggregates_counts_and_pass_rate():
    report = RunReport(
        outcomes=[
            _outcome("a", "c1", "passed"),
            _outcome("a", "c2", "failed"),
            _outcome("b", "c3", "errored"),
            _outcome("b", "c4", "passed"),
        ]
    )
    assert report.total == 4
    assert report.passed == 2
    assert report.failed == 1
    assert report.errored == 1
    assert report.pass_rate == 0.5


def test_pass_rate_by_skill_groups_correctly():
    report = RunReport(
        outcomes=[
            _outcome("a", "c1", "passed"),
            _outcome("a", "c2", "failed"),
            _outcome("b", "c3", "passed"),
        ]
    )
    assert report.pass_rate_by_skill() == {"a": 0.5, "b": 1.0}


def test_empty_report_has_zero_pass_rate():
    report = RunReport()
    assert report.total == 0
    assert report.pass_rate == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.models'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/models.py`:

```python
"""Pydantic models shared across skill-eval."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

CaseStatus = Literal["passed", "failed", "errored"]


class Skill(BaseModel):
    """A skill under test, loaded from a SKILL.md file."""

    name: str
    description: str = ""
    instructions: str = ""
    path: Path


class ToolCall(BaseModel):
    """A single tool invocation made by an agent during a run."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    """The outcome of running one task against one skill with one runner."""

    output: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def errored(self) -> bool:
        """True when the runner itself failed (infra), not when a score was low."""
        return self.error is not None


class EvalScore(BaseModel):
    """One evaluator's verdict on one run."""

    evaluator: str
    passed: bool
    score: float = 0.0
    detail: str = ""


class AssertionSpec(BaseModel):
    """A declarative assertion from an eval YAML file."""

    kind: str
    value: str


class EvalCase(BaseModel):
    """A single eval case: a task prompt plus how to score it."""

    name: str
    task: str
    assertions: list[AssertionSpec] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CaseOutcome(BaseModel):
    """The fully-scored result of one (skill, case, runner) combination."""

    skill_name: str
    case_name: str
    runner: str
    status: CaseStatus
    scores: list[EvalScore] = Field(default_factory=list)
    result: RunResult | None = None


class RunReport(BaseModel):
    """Aggregated results for a whole run."""

    outcomes: list[CaseOutcome] = Field(default_factory=list)
    skipped_skills: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "errored")

    @property
    def pass_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return self.passed / self.total

    def pass_rate_by_skill(self) -> dict[str, float]:
        """Pass rate per skill name, for per-skill gating and reporting."""
        buckets: dict[str, list[CaseOutcome]] = {}
        for outcome in self.outcomes:
            buckets.setdefault(outcome.skill_name, []).append(outcome)
        return {
            name: sum(1 for o in items if o.status == "passed") / len(items)
            for name, items in buckets.items()
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/models.py tests/test_models.py
git commit -m "feat: add core Pydantic models for skills, runs, and reports"
```

---

### Task 3: Skill loader

**Files:**
- Create: `src/skill_eval/skills/__init__.py`, `src/skill_eval/skills/loader.py`, `tests/test_skill_loader.py`

**Interfaces:**
- Consumes: `Skill` from `skill_eval.models`
- Produces:
  - `load_skills(path: Path) -> list[Skill]` — walks for `SKILL.md`, sorted by name
  - `parse_skill_file(skill_md: Path) -> Skill`
  - `SkillParseError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_loader.py`:

```python
import pytest

from skill_eval.skills.loader import SkillParseError, load_skills

SKILL_MD = """---
name: pdf
description: Work with PDF files
---

Use pdfplumber to extract text.
"""


def _write_skill(root, name, body=SKILL_MD):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body)
    return skill_dir


def test_loads_a_single_skill_directory(tmp_path):
    _write_skill(tmp_path, "pdf")
    skills = load_skills(tmp_path / "pdf")
    assert len(skills) == 1
    assert skills[0].name == "pdf"
    assert skills[0].description == "Work with PDF files"
    assert "pdfplumber" in skills[0].instructions


def test_discovers_many_skills_under_a_parent_dir(tmp_path):
    _write_skill(tmp_path, "pdf")
    _write_skill(tmp_path, "xlsx", SKILL_MD.replace("name: pdf", "name: xlsx"))
    skills = load_skills(tmp_path)
    assert [s.name for s in skills] == ["pdf", "xlsx"]


def test_falls_back_to_directory_name_when_frontmatter_lacks_name(tmp_path):
    _write_skill(tmp_path, "fallback", "---\ndescription: no name here\n---\n\nBody.\n")
    skills = load_skills(tmp_path / "fallback")
    assert skills[0].name == "fallback"


def test_skill_without_frontmatter_uses_whole_file_as_instructions(tmp_path):
    _write_skill(tmp_path, "plain", "Just instructions, no frontmatter.\n")
    skills = load_skills(tmp_path / "plain")
    assert skills[0].name == "plain"
    assert "Just instructions" in skills[0].instructions


def test_missing_path_raises(tmp_path):
    with pytest.raises(SkillParseError, match="does not exist"):
        load_skills(tmp_path / "nope")


def test_path_without_any_skill_md_returns_empty(tmp_path):
    (tmp_path / "empty").mkdir()
    assert load_skills(tmp_path) == []


def test_malformed_frontmatter_raises_with_file_path(tmp_path):
    _write_skill(tmp_path, "bad", "---\nname: [unclosed\n---\n\nBody.\n")
    with pytest.raises(SkillParseError, match="bad/SKILL.md"):
        load_skills(tmp_path / "bad")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_skill_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.skills'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/skills/__init__.py`:

```python
```

(empty file)

Create `src/skill_eval/skills/loader.py`:

```python
"""Discover and parse SKILL.md files into Skill models."""

from __future__ import annotations

from pathlib import Path

import yaml

from skill_eval.models import Skill

SKILL_FILENAME = "SKILL.md"


class SkillParseError(Exception):
    """Raised when a skill path is missing or a SKILL.md cannot be parsed."""


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter, body). Missing frontmatter yields ({}, whole text)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    return yaml.safe_load(parts[1]) or {}, parts[2]


def parse_skill_file(skill_md: Path) -> Skill:
    """Parse one SKILL.md into a Skill, falling back to the dir name."""
    try:
        frontmatter, body = _split_frontmatter(skill_md.read_text())
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid frontmatter in {skill_md}: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillParseError(f"invalid frontmatter in {skill_md}: expected a mapping")
    return Skill(
        name=str(frontmatter.get("name") or skill_md.parent.name),
        description=str(frontmatter.get("description") or ""),
        instructions=body.strip(),
        path=skill_md.parent,
    )


def load_skills(path: Path) -> list[Skill]:
    """Walk `path` for SKILL.md files and return the skills, sorted by name."""
    path = Path(path)
    if not path.exists():
        raise SkillParseError(f"skill path does not exist: {path}")
    if path.is_file():
        return [parse_skill_file(path)]
    if (path / SKILL_FILENAME).is_file():
        return [parse_skill_file(path / SKILL_FILENAME)]
    skills = [parse_skill_file(md) for md in sorted(path.rglob(SKILL_FILENAME))]
    return sorted(skills, key=lambda s: s.name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_skill_loader.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/skills tests/test_skill_loader.py
git commit -m "feat: add skill loader with recursive SKILL.md discovery"
```

---

### Task 4: Eval case loader

**Files:**
- Create: `src/skill_eval/cases/__init__.py`, `src/skill_eval/cases/loader.py`, `tests/test_case_loader.py`

**Interfaces:**
- Consumes: `EvalCase`, `AssertionSpec` from `skill_eval.models`; `Skill`
- Produces:
  - `load_cases_for_skill(skill: Skill, evals_path: Path | None = None) -> list[EvalCase]`
  - `parse_cases_file(path: Path) -> list[EvalCase]`
  - `CaseParseError(Exception)`

**YAML contract:**

```yaml
cases:
  - name: extracts text
    task: Extract the text from report.pdf
    tags: [smoke]
    assertions:
      - kind: contains
        value: pdfplumber
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_case_loader.py`:

```python
from pathlib import Path

import pytest

from skill_eval.cases.loader import CaseParseError, load_cases_for_skill, parse_cases_file
from skill_eval.models import Skill

CASES_YAML = """cases:
  - name: extracts text
    task: Extract the text from report.pdf
    tags: [smoke]
    assertions:
      - kind: contains
        value: pdfplumber
  - name: handles missing file
    task: Extract from nope.pdf
    assertions:
      - kind: not_contains
        value: traceback
"""


def _skill(tmp_path, name="pdf"):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    return Skill(name=name, description="", instructions="", path=skill_dir)


def test_parses_cases_from_a_yaml_file(tmp_path):
    path = tmp_path / "x.eval.yaml"
    path.write_text(CASES_YAML)
    cases = parse_cases_file(path)
    assert [c.name for c in cases] == ["extracts text", "handles missing file"]
    assert cases[0].assertions[0].kind == "contains"
    assert cases[0].assertions[0].value == "pdfplumber"
    assert cases[0].tags == ["smoke"]
    assert cases[1].tags == []


def test_discovers_evals_directory_beside_skill(tmp_path):
    skill = _skill(tmp_path)
    evals = skill.path / "evals"
    evals.mkdir()
    (evals / "basic.yaml").write_text(CASES_YAML)
    assert len(load_cases_for_skill(skill)) == 2


def test_discovers_dot_eval_yaml_beside_skill(tmp_path):
    skill = _skill(tmp_path)
    (skill.path / "pdf.eval.yaml").write_text(CASES_YAML)
    assert len(load_cases_for_skill(skill)) == 2


def test_explicit_evals_path_overrides_convention(tmp_path):
    skill = _skill(tmp_path)
    (skill.path / "pdf.eval.yaml").write_text(CASES_YAML)
    other = tmp_path / "other.yaml"
    other.write_text("cases:\n  - name: only one\n    task: do it\n")
    cases = load_cases_for_skill(skill, evals_path=other)
    assert [c.name for c in cases] == ["only one"]


def test_skill_with_no_evals_returns_empty(tmp_path):
    assert load_cases_for_skill(_skill(tmp_path)) == []


def test_case_missing_task_raises_with_file_and_field(tmp_path):
    path = tmp_path / "bad.eval.yaml"
    path.write_text("cases:\n  - name: no task here\n")
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "bad.eval.yaml" in str(exc.value)
    assert "task" in str(exc.value)


def test_malformed_yaml_raises_with_path(tmp_path):
    path = tmp_path / "broken.eval.yaml"
    path.write_text("cases: [unclosed\n")
    with pytest.raises(CaseParseError, match="broken.eval.yaml"):
        parse_cases_file(path)


def test_missing_explicit_path_raises(tmp_path):
    with pytest.raises(CaseParseError, match="does not exist"):
        load_cases_for_skill(_skill(tmp_path), evals_path=Path(tmp_path / "nope.yaml"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_case_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.cases'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/cases/__init__.py`:

```python
```

(empty file)

Create `src/skill_eval/cases/loader.py`:

```python
"""Discover and parse eval case YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from skill_eval.models import EvalCase, Skill

EVALS_DIRNAME = "evals"
EVAL_SUFFIX = ".eval.yaml"


class CaseParseError(Exception):
    """Raised when an eval file is missing or cannot be parsed."""


def parse_cases_file(path: Path) -> list[EvalCase]:
    """Parse one YAML file into EvalCase models."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise CaseParseError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict) or "cases" not in data:
        raise CaseParseError(f"{path}: expected a top-level 'cases' list")
    raw_cases = data["cases"] or []
    cases: list[EvalCase] = []
    for index, raw in enumerate(raw_cases):
        try:
            cases.append(EvalCase.model_validate(raw))
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise CaseParseError(f"{path}: case #{index + 1} invalid ({fields}): {exc}") from exc
    return cases


def _discover_paths(skill: Skill) -> list[Path]:
    """Find eval files beside a skill: an evals/ dir, then *.eval.yaml."""
    evals_dir = skill.path / EVALS_DIRNAME
    if evals_dir.is_dir():
        return sorted(p for p in evals_dir.iterdir() if p.suffix in {".yaml", ".yml"})
    return sorted(skill.path.glob(f"*{EVAL_SUFFIX}"))


def load_cases_for_skill(skill: Skill, evals_path: Path | None = None) -> list[EvalCase]:
    """Load a skill's eval cases, honouring an explicit override path."""
    if evals_path is not None:
        evals_path = Path(evals_path)
        if not evals_path.exists():
            raise CaseParseError(f"evals path does not exist: {evals_path}")
        paths = (
            sorted(p for p in evals_path.iterdir() if p.suffix in {".yaml", ".yml"})
            if evals_path.is_dir()
            else [evals_path]
        )
    else:
        paths = _discover_paths(skill)
    cases: list[EvalCase] = []
    for path in paths:
        cases.extend(parse_cases_file(path))
    return cases
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_case_loader.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/cases tests/test_case_loader.py
git commit -m "feat: add eval case loader with convention-based discovery"
```

---

### Task 5: Runner protocol and FakeRunner

**Files:**
- Create: `src/skill_eval/runners/__init__.py`, `src/skill_eval/runners/base.py`, `src/skill_eval/runners/fake.py`, `tests/test_fake_runner.py`

**Interfaces:**
- Consumes: `Skill`, `RunResult`, `ToolCall` from `skill_eval.models`
- Produces:
  - `Runner` protocol with `name: str` and `run(skill: Skill, task: str) -> RunResult`
  - `FakeRunner(responses: dict[str, RunResult] | None = None, default: RunResult | None = None)` with `name = "fake"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fake_runner.py`:

```python
from pathlib import Path

from skill_eval.models import RunResult, Skill, ToolCall
from skill_eval.runners.fake import FakeRunner

SKILL = Skill(name="pdf", description="", instructions="Use pdfplumber.", path=Path("/s/pdf"))


def test_fake_runner_returns_scripted_response_for_task():
    runner = FakeRunner(responses={"extract": RunResult(output="used pdfplumber")})
    assert runner.run(SKILL, "extract").output == "used pdfplumber"


def test_fake_runner_is_deterministic():
    runner = FakeRunner(responses={"extract": RunResult(output="stable")})
    assert runner.run(SKILL, "extract") == runner.run(SKILL, "extract")


def test_unknown_task_returns_default():
    runner = FakeRunner(default=RunResult(output="fallback"))
    assert runner.run(SKILL, "anything").output == "fallback"


def test_unknown_task_without_default_echoes_skill_name():
    result = FakeRunner().run(SKILL, "anything")
    assert "pdf" in result.output
    assert result.errored is False


def test_scripted_error_result_is_errored():
    runner = FakeRunner(responses={"boom": RunResult(error="API down")})
    assert runner.run(SKILL, "boom").errored is True


def test_carries_tool_calls_through():
    runner = FakeRunner(
        responses={"t": RunResult(output="x", tool_calls=[ToolCall(name="read_pdf")])}
    )
    assert runner.run(SKILL, "t").tool_calls[0].name == "read_pdf"


def test_runner_exposes_name():
    assert FakeRunner().name == "fake"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fake_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.runners'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/runners/__init__.py`:

```python
```

(empty file)

Create `src/skill_eval/runners/base.py`:

```python
"""The Runner protocol — the seam every agent framework plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_eval.models import RunResult, Skill


@runtime_checkable
class Runner(Protocol):
    """Runs a task against a skill and reports what happened."""

    name: str

    def run(self, skill: Skill, task: str) -> RunResult:
        """Execute `task` with `skill` loaded, returning a RunResult.

        Implementations must not raise for provider failures; they set
        RunResult.error instead so the orchestrator can mark the case errored.
        """
        ...
```

Create `src/skill_eval/runners/fake.py`:

```python
"""A deterministic, offline runner used to test the whole pipeline."""

from __future__ import annotations

from skill_eval.models import RunResult, Skill


class FakeRunner:
    """Returns scripted RunResults. Never touches the network."""

    name = "fake"

    def __init__(
        self,
        responses: dict[str, RunResult] | None = None,
        default: RunResult | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default

    def run(self, skill: Skill, task: str) -> RunResult:
        if task in self._responses:
            return self._responses[task]
        if self._default is not None:
            return self._default
        return RunResult(output=f"[fake] {skill.name} handled: {task}")
```

> **⚠️ Superseded during review — do not copy this `run` body.** It returns the stored
> `RunResult` objects *by reference*. `RunResult` is a non-frozen Pydantic model with
> mutable list fields, so any caller that mutates a result it received corrupts the
> runner's scripted state and breaks determinism for later calls — which Tasks 7, 11 and
> 12 all depend on. The shipped implementation returns `.model_copy(deep=True)` of both
> the scripted response and the default. See `src/skill_eval/runners/fake.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fake_runner.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/runners tests/test_fake_runner.py
git commit -m "feat: add Runner protocol and deterministic FakeRunner"
```

---

### Task 6: Evaluator protocol and assertion evaluator

**Files:**
- Create: `src/skill_eval/evaluators/__init__.py`, `src/skill_eval/evaluators/base.py`, `src/skill_eval/evaluators/assertion.py`, `tests/test_assertion_evaluator.py`

**Interfaces:**
- Consumes: `EvalCase`, `RunResult`, `EvalScore`, `AssertionSpec`
- Produces:
  - `Evaluator` protocol with `name: str` and `evaluate(case: EvalCase, result: RunResult) -> EvalScore`
  - `AssertionEvaluator()` with `name = "assertion"`, supporting kinds `contains`, `not_contains`, `regex`, `equals`
  - `UnknownAssertionKind(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_assertion_evaluator.py`:

```python
import pytest

from skill_eval.evaluators.assertion import AssertionEvaluator, UnknownAssertionKind
from skill_eval.models import AssertionSpec, EvalCase, RunResult


def _case(*specs):
    return EvalCase(name="c", task="t", assertions=list(specs))


def test_contains_passes_when_substring_present():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="contains", value="pdfplumber")),
        RunResult(output="I used pdfplumber to extract."),
    )
    assert score.passed is True
    assert score.score == 1.0
    assert score.evaluator == "assertion"


def test_contains_fails_and_explains_when_missing():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="contains", value="pdfplumber")),
        RunResult(output="I used something else."),
    )
    assert score.passed is False
    assert "pdfplumber" in score.detail


def test_not_contains_passes_when_absent():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="not_contains", value="traceback")),
        RunResult(output="all good"),
    )
    assert score.passed is True


def test_not_contains_fails_when_present():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="not_contains", value="traceback")),
        RunResult(output="traceback: boom"),
    )
    assert score.passed is False


def test_regex_matches():
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="regex", value=r"\d+ pages")),
        RunResult(output="found 12 pages"),
    )
    assert score.passed is True


def test_equals_is_exact_after_strip():
    evaluator = AssertionEvaluator()
    assert evaluator.evaluate(
        _case(AssertionSpec(kind="equals", value="done")), RunResult(output="  done  ")
    ).passed is True
    assert evaluator.evaluate(
        _case(AssertionSpec(kind="equals", value="done")), RunResult(output="done!")
    ).passed is False


def test_all_assertions_must_pass():
    score = AssertionEvaluator().evaluate(
        _case(
            AssertionSpec(kind="contains", value="a"),
            AssertionSpec(kind="contains", value="zzz"),
        ),
        RunResult(output="a b c"),
    )
    assert score.passed is False
    assert score.score == 0.5


def test_case_with_no_assertions_passes_vacuously():
    assert AssertionEvaluator().evaluate(_case(), RunResult(output="x")).passed is True


def test_unknown_kind_raises():
    with pytest.raises(UnknownAssertionKind, match="nonsense"):
        AssertionEvaluator().evaluate(
            _case(AssertionSpec(kind="nonsense", value="x")), RunResult(output="y")
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_assertion_evaluator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.evaluators'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/evaluators/__init__.py`:

```python
```

(empty file)

Create `src/skill_eval/evaluators/base.py`:

```python
"""The Evaluator protocol — the seam every scoring strategy plugs into."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from skill_eval.models import EvalCase, EvalScore, RunResult


@runtime_checkable
class Evaluator(Protocol):
    """Scores a RunResult against an EvalCase."""

    name: str

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        """Return a pass/fail verdict with a numeric score and human detail."""
        ...
```

Create `src/skill_eval/evaluators/assertion.py`:

```python
"""Deterministic, rule-based scoring of a run's final output."""

from __future__ import annotations

import re

from skill_eval.models import AssertionSpec, EvalCase, EvalScore, RunResult


class UnknownAssertionKind(Exception):
    """Raised when an eval file uses an assertion kind we do not support."""


def _check(spec: AssertionSpec, output: str) -> bool:
    if spec.kind == "contains":
        return spec.value in output
    if spec.kind == "not_contains":
        return spec.value not in output
    if spec.kind == "regex":
        return re.search(spec.value, output) is not None
    if spec.kind == "equals":
        return output.strip() == spec.value
    raise UnknownAssertionKind(f"unknown assertion kind: {spec.kind!r}")


class AssertionEvaluator:
    """Every assertion must hold; the score is the fraction that held."""

    name = "assertion"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        if not case.assertions:
            return EvalScore(evaluator=self.name, passed=True, score=1.0, detail="no assertions")
        failures: list[str] = []
        for spec in case.assertions:
            if not _check(spec, result.output):
                failures.append(f"{spec.kind}({spec.value!r})")
        passed_count = len(case.assertions) - len(failures)
        detail = "all assertions held" if not failures else "failed: " + ", ".join(failures)
        return EvalScore(
            evaluator=self.name,
            passed=not failures,
            score=passed_count / len(case.assertions),
            detail=detail,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_assertion_evaluator.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/evaluators tests/test_assertion_evaluator.py
git commit -m "feat: add Evaluator protocol and assertion evaluator"
```

---

### Task 7: Orchestrator

**Files:**
- Create: `src/skill_eval/orchestrator.py`, `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `load_cases_for_skill`, `AssertionEvaluator`, `Runner`, all models
- Produces:
  - `run_evals(skills: list[Skill], runners: list[Runner], evals_path: Path | None = None, evaluators: list[Evaluator] | None = None, tag: str | None = None) -> RunReport`

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator.py`:

```python
from skill_eval.models import AssertionSpec, EvalCase, RunResult, Skill
from skill_eval.orchestrator import run_evals
from skill_eval.runners.fake import FakeRunner

CASES_YAML = """cases:
  - name: passes
    task: good
    tags: [smoke]
    assertions:
      - kind: contains
        value: yes
  - name: fails
    task: bad
    assertions:
      - kind: contains
        value: never-there
"""


def _skill_with_cases(tmp_path, name="pdf", yaml_text=CASES_YAML):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / f"{name}.eval.yaml").write_text(yaml_text)
    return Skill(name=name, description="", instructions="", path=skill_dir)


def _runner():
    return FakeRunner(
        responses={
            "good": RunResult(output="yes it worked"),
            "bad": RunResult(output="nope"),
            "explodes": RunResult(error="provider 500"),
        }
    )


def test_runs_all_cases_and_marks_pass_and_fail(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()])
    assert report.total == 2
    assert report.passed == 1
    assert report.failed == 1


def test_runner_error_is_marked_errored_not_failed(tmp_path):
    yaml_text = "cases:\n  - name: boom\n    task: explodes\n"
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.errored == 1
    assert report.failed == 0
    assert report.outcomes[0].status == "errored"


def test_skill_with_no_cases_is_reported_as_skipped(tmp_path):
    empty_dir = tmp_path / "bare"
    empty_dir.mkdir()
    skill = Skill(name="bare", description="", instructions="", path=empty_dir)
    report = run_evals([skill], [_runner()])
    assert report.skipped_skills == ["bare"]
    assert report.total == 0


def test_matrix_covers_every_skill_case_runner_combination(tmp_path):
    skills = [_skill_with_cases(tmp_path, "pdf"), _skill_with_cases(tmp_path, "xlsx")]
    runners = [_runner(), FakeRunner(default=RunResult(output="yes"))]
    report = run_evals(skills, runners)
    assert report.total == 8  # 2 skills x 2 cases x 2 runners


def test_outcome_records_skill_case_and_runner_names(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()])
    outcome = report.outcomes[0]
    assert outcome.skill_name == "pdf"
    assert outcome.case_name == "passes"
    assert outcome.runner == "fake"


def test_tag_filter_selects_matching_cases(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()], tag="smoke")
    assert report.total == 1
    assert report.outcomes[0].case_name == "passes"


def test_errored_case_still_records_the_result(tmp_path):
    yaml_text = "cases:\n  - name: boom\n    task: explodes\n"
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.outcomes[0].result.error == "provider 500"


def test_evaluator_is_not_run_for_errored_cases(tmp_path):
    yaml_text = (
        "cases:\n  - name: boom\n    task: explodes\n"
        "    assertions:\n      - kind: contains\n        value: never\n"
    )
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.outcomes[0].scores == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.orchestrator'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/orchestrator.py`:

```python
"""Build and run the skill x case x runner matrix."""

from __future__ import annotations

from pathlib import Path

from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.evaluators.assertion import AssertionEvaluator
from skill_eval.evaluators.base import Evaluator
from skill_eval.models import CaseOutcome, EvalCase, RunReport, Skill
from skill_eval.runners.base import Runner


def _run_one(
    skill: Skill, case: EvalCase, runner: Runner, evaluators: list[Evaluator]
) -> CaseOutcome:
    """Run a single combination and score it, keeping errored distinct from failed."""
    result = runner.run(skill, case.task)
    if result.errored:
        return CaseOutcome(
            skill_name=skill.name,
            case_name=case.name,
            runner=runner.name,
            status="errored",
            scores=[],
            result=result,
        )
    scores = [evaluator.evaluate(case, result) for evaluator in evaluators]
    status = "passed" if all(s.passed for s in scores) else "failed"
    return CaseOutcome(
        skill_name=skill.name,
        case_name=case.name,
        runner=runner.name,
        status=status,
        scores=scores,
        result=result,
    )


def run_evals(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None = None,
    evaluators: list[Evaluator] | None = None,
    tag: str | None = None,
) -> RunReport:
    """Run every (skill, case, runner) combination and aggregate the results."""
    evaluators = evaluators if evaluators is not None else [AssertionEvaluator()]
    outcomes: list[CaseOutcome] = []
    skipped: list[str] = []
    for skill in skills:
        cases = load_cases_for_skill(skill, evals_path=evals_path)
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
        if not cases:
            skipped.append(skill.name)
            continue
        for case in cases:
            for runner in runners:
                outcomes.append(_run_one(skill, case, runner, evaluators))
    return RunReport(outcomes=outcomes, skipped_skills=skipped)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add orchestrator for skill x case x runner matrix"
```

---

### Task 8: Gating

**Files:**
- Create: `src/skill_eval/gating.py`, `tests/test_gating.py`

**Interfaces:**
- Consumes: `RunReport`
- Produces:
  - `EXIT_OK = 0`, `EXIT_FAILED = 1`
  - `GateResult(passed: bool, exit_code: int, reasons: list[str])`
  - `evaluate_gate(report: RunReport, min_pass_rate: float = 1.0, fail_on_error: bool = True, per_skill_min: dict[str, float] | None = None) -> GateResult`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gating.py`:

```python
from skill_eval.gating import EXIT_FAILED, EXIT_OK, evaluate_gate
from skill_eval.models import CaseOutcome, RunReport


def _report(*statuses_by_skill):
    outcomes = [
        CaseOutcome(skill_name=skill, case_name=f"c{i}", runner="fake", status=status)
        for i, (skill, status) in enumerate(statuses_by_skill)
    ]
    return RunReport(outcomes=outcomes)


def test_all_passing_meets_the_gate():
    gate = evaluate_gate(_report(("a", "passed"), ("a", "passed")))
    assert gate.passed is True
    assert gate.exit_code == EXIT_OK


def test_pass_rate_below_threshold_fails():
    gate = evaluate_gate(_report(("a", "passed"), ("a", "failed")), min_pass_rate=0.9)
    assert gate.passed is False
    assert gate.exit_code == EXIT_FAILED
    assert any("pass rate" in r for r in gate.reasons)


def test_pass_rate_at_threshold_passes():
    assert evaluate_gate(
        _report(("a", "passed"), ("a", "failed")), min_pass_rate=0.5
    ).passed is True


def test_errored_case_fails_the_gate_by_default():
    gate = evaluate_gate(_report(("a", "errored")), min_pass_rate=0.0)
    assert gate.passed is False
    assert any("errored" in r for r in gate.reasons)


def test_fail_on_error_can_be_disabled():
    assert evaluate_gate(
        _report(("a", "errored")), min_pass_rate=0.0, fail_on_error=False
    ).passed is True


def test_per_skill_threshold_fails_only_the_offending_skill():
    gate = evaluate_gate(
        _report(("a", "passed"), ("b", "failed")),
        min_pass_rate=0.0,
        per_skill_min={"b": 1.0},
    )
    assert gate.passed is False
    assert any("b" in r for r in gate.reasons)


def test_empty_report_passes_and_is_not_an_error():
    gate = evaluate_gate(RunReport())
    assert gate.passed is True
    assert gate.exit_code == EXIT_OK
```

> **⚠️ Superseded during review — this test encodes the opposite of the shipped contract.**
> Letting an empty report pass means a mistyped path, a renamed directory, or a `--tag`
> matching nothing reports success forever — and it made the CI dogfood step vacuous (it
> passed even with the example eval file deleted). The shipped behaviour is that a run
> which executed **zero cases fails the gate**, with a reason distinguishing "no skills
> found" / "all skipped (no eval cases)" / "all filtered out by `--tag`". The test was
> renamed to `test_empty_report_fails_the_gate`. See `src/skill_eval/gating.py` and §7 of
> the design doc.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gating.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.gating'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/gating.py`:

```python
"""Turn a RunReport into a pass/fail decision and a process exit code."""

from __future__ import annotations

from pydantic import BaseModel, Field

from skill_eval.models import RunReport

EXIT_OK = 0
EXIT_FAILED = 1


class GateResult(BaseModel):
    """Whether a run met the configured bar, and why not if it did not."""

    passed: bool
    exit_code: int
    reasons: list[str] = Field(default_factory=list)


def evaluate_gate(
    report: RunReport,
    min_pass_rate: float = 1.0,
    fail_on_error: bool = True,
    per_skill_min: dict[str, float] | None = None,
) -> GateResult:
    """Apply thresholds to a report. Errored cases fail the gate by default."""
    reasons: list[str] = []

    if report.total and report.pass_rate < min_pass_rate:
        reasons.append(
            f"pass rate {report.pass_rate:.0%} is below the required {min_pass_rate:.0%}"
        )

    if fail_on_error and report.errored:
        reasons.append(f"{report.errored} case(s) errored")

    for skill_name, minimum in (per_skill_min or {}).items():
        actual = report.pass_rate_by_skill().get(skill_name)
        if actual is not None and actual < minimum:
            reasons.append(
                f"skill {skill_name!r} pass rate {actual:.0%} is below its required {minimum:.0%}"
            )

    passed = not reasons
    return GateResult(
        passed=passed, exit_code=EXIT_OK if passed else EXIT_FAILED, reasons=reasons
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gating.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/gating.py tests/test_gating.py
git commit -m "feat: add threshold gating with exit codes"
```

---

### Task 9: Console and JSON reporters

**Files:**
- Create: `src/skill_eval/reporters/__init__.py`, `src/skill_eval/reporters/console.py`, `src/skill_eval/reporters/json_reporter.py`, `tests/test_reporters.py`

**Interfaces:**
- Consumes: `RunReport`, `GateResult`
- Produces:
  - `render_console(report: RunReport, gate: GateResult | None = None) -> str`
  - `render_json(report: RunReport, gate: GateResult | None = None) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporters.py`:

```python
import json

from skill_eval.gating import evaluate_gate
from skill_eval.models import CaseOutcome, EvalScore, RunReport, RunResult
from skill_eval.reporters.console import render_console
from skill_eval.reporters.json_reporter import render_json


def _report():
    return RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="pdf",
                case_name="extracts",
                runner="fake",
                status="passed",
                scores=[EvalScore(evaluator="assertion", passed=True, score=1.0, detail="ok")],
                result=RunResult(output="yes", tokens=10, cost_usd=0.01, latency_ms=5),
            ),
            CaseOutcome(
                skill_name="pdf",
                case_name="handles missing",
                runner="fake",
                status="failed",
                scores=[EvalScore(evaluator="assertion", passed=False, score=0.0, detail="nope")],
                result=RunResult(output="no"),
            ),
        ],
        skipped_skills=["xlsx"],
    )


def test_console_shows_case_names_and_statuses():
    text = render_console(_report())
    assert "extracts" in text
    assert "handles missing" in text
    assert "pdf" in text


def test_console_reports_totals_and_pass_rate():
    text = render_console(_report())
    assert "1 passed" in text
    assert "1 failed" in text
    assert "50%" in text


def test_console_lists_skipped_skills():
    assert "xlsx" in render_console(_report())


def test_console_includes_failure_detail():
    assert "nope" in render_console(_report())


def test_console_shows_gate_reasons_when_gate_fails():
    gate = evaluate_gate(_report(), min_pass_rate=1.0)
    text = render_console(_report(), gate=gate)
    assert "pass rate" in text


def test_console_handles_empty_report():
    text = render_console(RunReport())
    assert "0 passed" in text


def test_json_is_valid_and_carries_summary():
    data = json.loads(render_json(_report()))
    assert data["summary"]["total"] == 2
    assert data["summary"]["passed"] == 1
    assert data["summary"]["pass_rate"] == 0.5
    assert data["skipped_skills"] == ["xlsx"]


def test_json_carries_per_case_detail_and_cost():
    data = json.loads(render_json(_report()))
    first = data["outcomes"][0]
    assert first["case_name"] == "extracts"
    assert first["status"] == "passed"
    assert first["cost_usd"] == 0.01
    assert first["tokens"] == 10


def test_json_includes_gate_when_supplied():
    gate = evaluate_gate(_report(), min_pass_rate=1.0)
    data = json.loads(render_json(_report(), gate=gate))
    assert data["gate"]["passed"] is False
    assert data["gate"]["reasons"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_reporters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.reporters'`

- [ ] **Step 3: Write the implementations**

Create `src/skill_eval/reporters/__init__.py`:

```python
```

(empty file)

Create `src/skill_eval/reporters/console.py`:

```python
"""Human-readable run summary."""

from __future__ import annotations

from skill_eval.gating import GateResult
from skill_eval.models import RunReport

_MARKS = {"passed": "PASS", "failed": "FAIL", "errored": "ERROR"}


def render_console(report: RunReport, gate: GateResult | None = None) -> str:
    """Render a report as plain text suitable for a terminal or CI log."""
    lines: list[str] = []
    for outcome in report.outcomes:
        mark = _MARKS[outcome.status]
        lines.append(f"[{mark}] {outcome.skill_name} :: {outcome.case_name} ({outcome.runner})")
        for score in outcome.scores:
            if not score.passed:
                lines.append(f"        {score.evaluator}: {score.detail}")
        if outcome.result is not None and outcome.result.error:
            lines.append(f"        error: {outcome.result.error}")

    if report.skipped_skills:
        lines.append("")
        lines.append(f"Skipped (no eval cases): {', '.join(report.skipped_skills)}")

    lines.append("")
    lines.append(
        f"{report.passed} passed, {report.failed} failed, "
        f"{report.errored} errored — pass rate {report.pass_rate:.0%}"
    )

    total_cost = sum(o.result.cost_usd for o in report.outcomes if o.result)
    if total_cost:
        lines.append(f"Total cost: ${total_cost:.4f}")

    if gate is not None and not gate.passed:
        lines.append("")
        lines.append("Gate FAILED:")
        lines.extend(f"  - {reason}" for reason in gate.reasons)

    return "\n".join(lines)
```

Create `src/skill_eval/reporters/json_reporter.py`:

```python
"""Machine-readable run report."""

from __future__ import annotations

import json

from skill_eval.gating import GateResult
from skill_eval.models import RunReport


def render_json(report: RunReport, gate: GateResult | None = None) -> str:
    """Render a report as indented JSON for CI artifacts and tooling."""
    payload: dict = {
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "errored": report.errored,
            "pass_rate": report.pass_rate,
            "pass_rate_by_skill": report.pass_rate_by_skill(),
        },
        "skipped_skills": report.skipped_skills,
        "outcomes": [
            {
                "skill_name": o.skill_name,
                "case_name": o.case_name,
                "runner": o.runner,
                "status": o.status,
                "scores": [s.model_dump() for s in o.scores],
                "output": o.result.output if o.result else "",
                "error": o.result.error if o.result else None,
                "tokens": o.result.tokens if o.result else 0,
                "latency_ms": o.result.latency_ms if o.result else 0,
                "cost_usd": o.result.cost_usd if o.result else 0.0,
            }
            for o in report.outcomes
        ],
    }
    if gate is not None:
        payload["gate"] = gate.model_dump()
    return json.dumps(payload, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reporters.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/reporters tests/test_reporters.py
git commit -m "feat: add console and JSON reporters"
```

---

### Task 10: Config loading

**Files:**
- Create: `src/skill_eval/config.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Config(default_runner: str = "fake", min_pass_rate: float = 1.0, fail_on_error: bool = True, per_skill_min: dict[str, float] = {}, reporters: list[str] = ["console"])`
  - `find_config_file(start: Path) -> Path | None` — searches upward
  - `load_config(path: Path | None = None, start: Path | None = None) -> Config`
  - `ConfigError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import pytest

from skill_eval.config import Config, ConfigError, find_config_file, load_config

TOML = """
default_runner = "fake"
min_pass_rate = 0.8
fail_on_error = false
reporters = ["console", "json"]

[per_skill_min]
pdf = 1.0
"""


def test_defaults_when_no_config_file(tmp_path):
    config = load_config(start=tmp_path)
    assert config == Config()
    assert config.min_pass_rate == 1.0
    assert config.fail_on_error is True
    assert config.default_runner == "fake"


def test_loads_values_from_an_explicit_path(tmp_path):
    path = tmp_path / "skill-eval.toml"
    path.write_text(TOML)
    config = load_config(path=path)
    assert config.min_pass_rate == 0.8
    assert config.fail_on_error is False
    assert config.reporters == ["console", "json"]
    assert config.per_skill_min == {"pdf": 1.0}


def test_discovers_config_by_searching_upward(tmp_path):
    (tmp_path / "skill-eval.toml").write_text(TOML)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config_file(nested) == tmp_path / "skill-eval.toml"
    assert load_config(start=nested).min_pass_rate == 0.8


def test_find_returns_none_when_absent(tmp_path):
    assert find_config_file(tmp_path) is None


def test_explicit_missing_path_raises(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(path=tmp_path / "nope.toml")


def test_malformed_toml_raises_with_path(tmp_path):
    path = tmp_path / "skill-eval.toml"
    path.write_text("min_pass_rate = [unclosed\n")
    with pytest.raises(ConfigError, match="skill-eval.toml"):
        load_config(path=path)


def test_unknown_keys_are_rejected(tmp_path):
    path = tmp_path / "skill-eval.toml"
    path.write_text('mistyped_key = "x"\n')
    with pytest.raises(ConfigError, match="mistyped_key"):
        load_config(path=path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.config'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/config.py`:

```python
"""Load skill-eval.toml. Secrets never live here — only env vars."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

CONFIG_FILENAME = "skill-eval.toml"


class ConfigError(Exception):
    """Raised when a config file is missing or invalid."""


class Config(BaseModel):
    """Run defaults; every field is overridable by a CLI flag."""

    model_config = {"extra": "forbid"}

    default_runner: str = "fake"
    min_pass_rate: float = 1.0
    fail_on_error: bool = True
    per_skill_min: dict[str, float] = Field(default_factory=dict)
    reporters: list[str] = Field(default_factory=lambda: ["console"])


def find_config_file(start: Path) -> Path | None:
    """Search `start` and its parents for skill-eval.toml."""
    start = Path(start).resolve()
    for directory in [start, *start.parents]:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None, start: Path | None = None) -> Config:
    """Load config from an explicit path, else by upward discovery, else defaults."""
    if path is not None:
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"config file does not exist: {path}")
    else:
        path = find_config_file(start or Path.cwd())
        if path is None:
            return Config()

    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {path}: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/skill_eval/config.py tests/test_config.py
git commit -m "feat: add skill-eval.toml config loading with upward discovery"
```

---

### Task 11: CLI wiring

**Files:**
- Create: `src/skill_eval/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above
- Produces: Typer `app` with commands `run` and `list`, plus `--version`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import json

from typer.testing import CliRunner

from skill_eval.cli import app

runner = CliRunner()

SKILL_MD = """---
name: pdf
description: Work with PDFs
---

Use pdfplumber.
"""

CASES_YAML = """cases:
  - name: mentions the skill
    task: anything
    assertions:
      - kind: contains
        value: pdf
"""

FAILING_CASES_YAML = """cases:
  - name: cannot pass
    task: anything
    assertions:
      - kind: contains
        value: definitely-not-in-output
"""


def _make_skill(tmp_path, name="pdf", cases=CASES_YAML):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD.replace("name: pdf", f"name: {name}"))
    if cases is not None:
        (skill_dir / f"{name}.eval.yaml").write_text(cases)
    return skill_dir


def test_version_flag_prints_a_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_run_exits_zero_when_all_cases_pass(tmp_path):
    _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 passed" in result.stdout


def test_run_exits_one_when_a_case_fails(tmp_path):
    _make_skill(tmp_path, cases=FAILING_CASES_YAML)
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == 1
    assert "Gate FAILED" in result.stdout


def test_min_pass_rate_flag_can_tolerate_failures(tmp_path):
    _make_skill(tmp_path, cases=FAILING_CASES_YAML)
    result = runner.invoke(app, ["run", str(tmp_path), "--min-pass-rate", "0"])
    assert result.exit_code == 0


def test_json_report_is_written_to_file(tmp_path):
    _make_skill(tmp_path)
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["run", str(tmp_path), "--json-output", str(out)])
    assert result.exit_code == 0
    assert json.loads(out.read_text())["summary"]["total"] == 1


def test_run_on_missing_path_exits_with_error(tmp_path):
    result = runner.invoke(app, ["run", str(tmp_path / "nope")])
    assert result.exit_code != 0
    assert "does not exist" in result.stdout


def test_skill_without_cases_is_reported_as_skipped(tmp_path):
    _make_skill(tmp_path, cases=None)
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert "Skipped" in result.stdout
    assert result.exit_code == 0


def test_list_command_shows_skills_and_case_counts(tmp_path):
    _make_skill(tmp_path)
    result = runner.invoke(app, ["list", str(tmp_path)])
    assert result.exit_code == 0
    assert "pdf" in result.stdout
    assert "1" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skill_eval.cli'`

- [ ] **Step 3: Write the implementation**

Create `src/skill_eval/cli.py`:

```python
"""The skill-eval command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from skill_eval import __version__
from skill_eval.cases.loader import CaseParseError, load_cases_for_skill
from skill_eval.config import ConfigError, load_config
from skill_eval.gating import evaluate_gate
from skill_eval.orchestrator import run_evals
from skill_eval.reporters.console import render_console
from skill_eval.reporters.json_reporter import render_json
from skill_eval.runners.fake import FakeRunner
from skill_eval.skills.loader import SkillParseError, load_skills

app = typer.Typer(help="Run evaluations on Agent Skills (SKILL.md).", no_args_is_help=True)

_RUNNERS = {"fake": FakeRunner}


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    """skill-eval — evaluate Agent Skills."""


@app.command()
def run(
    path: Annotated[Path, typer.Argument(help="A skill directory, or a directory of skills.")],
    evals: Annotated[Path | None, typer.Option(help="Explicit eval file or directory.")] = None,
    runner: Annotated[str | None, typer.Option(help="Runner to use.")] = None,
    tag: Annotated[str | None, typer.Option(help="Only run cases with this tag.")] = None,
    min_pass_rate: Annotated[float | None, typer.Option(help="Required pass rate.")] = None,
    json_output: Annotated[Path | None, typer.Option(help="Write a JSON report here.")] = None,
    config: Annotated[Path | None, typer.Option(help="Path to skill-eval.toml.")] = None,
) -> None:
    """Discover skills, run their eval cases, and gate on the results."""
    try:
        settings = load_config(path=config)
        skills = load_skills(path)
        runner_name = runner or settings.default_runner
        if runner_name not in _RUNNERS:
            raise typer.BadParameter(f"unknown runner: {runner_name}")
        report = run_evals(skills, [_RUNNERS[runner_name]()], evals_path=evals, tag=tag)
    except (SkillParseError, CaseParseError, ConfigError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    gate = evaluate_gate(
        report,
        min_pass_rate=min_pass_rate if min_pass_rate is not None else settings.min_pass_rate,
        fail_on_error=settings.fail_on_error,
        per_skill_min=settings.per_skill_min,
    )

    typer.echo(render_console(report, gate=gate))
    if json_output is not None:
        json_output.write_text(render_json(report, gate=gate))

    raise typer.Exit(code=gate.exit_code)


@app.command("list")
def list_skills(
    path: Annotated[Path, typer.Argument(help="A skill directory, or a directory of skills.")],
    evals: Annotated[Path | None, typer.Option(help="Explicit eval file or directory.")] = None,
) -> None:
    """Show the skills that would be evaluated and how many cases each has."""
    try:
        skills = load_skills(path)
        for skill in skills:
            count = len(load_cases_for_skill(skill, evals_path=evals))
            typer.echo(f"{skill.name}\t{count} case(s)\t{skill.path}")
    except (SkillParseError, CaseParseError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the whole suite and lint**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`
Expected: all tests PASS, ruff reports no issues. Fix anything ruff flags before committing.

- [ ] **Step 6: Commit**

```bash
git add src/skill_eval/cli.py tests/test_cli.py
git commit -m "feat: wire up skill-eval run and list commands"
```

---

### Task 12: Example skill and CI workflow

**Files:**
- Create: `examples/greeting/SKILL.md`, `examples/greeting/greeting.eval.yaml`, `.github/workflows/ci.yml`, `tests/test_examples.py`

**Interfaces:**
- Consumes: the CLI
- Produces: a real example skill that the test suite runs end-to-end, and CI that runs lint + tests

- [ ] **Step 1: Write the failing test**

Create `tests/test_examples.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from skill_eval.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).parent.parent / "examples"


def test_examples_directory_exists():
    assert EXAMPLES.is_dir()


def test_example_skills_are_discoverable():
    result = runner.invoke(app, ["list", str(EXAMPLES)])
    assert result.exit_code == 0
    assert "greeting" in result.stdout


def test_examples_run_green_end_to_end():
    result = runner.invoke(app, ["run", str(EXAMPLES)])
    assert result.exit_code == 0, result.stdout
    assert "0 failed" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_examples.py -v`
Expected: FAIL — `assert EXAMPLES.is_dir()` fails because `examples/` does not exist.

- [ ] **Step 3: Create the example skill**

Create `examples/greeting/SKILL.md`:

```markdown
---
name: greeting
description: Greet a user warmly and by name
---

When greeting someone, address them by name and keep it to one short sentence.
```

Create `examples/greeting/greeting.eval.yaml`:

```yaml
cases:
  - name: fake runner echoes the skill name
    task: greet Ada
    tags: [smoke]
    assertions:
      - kind: contains
        value: greeting
      - kind: not_contains
        value: traceback
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_examples.py -v`
Expected: PASS (3 tests)

Note: the assertions match `FakeRunner`'s default output (`[fake] greeting handled: greet Ada`). Task M2 replaces these with real-agent assertions.

- [ ] **Step 5: Create the CI workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install 3.11

      - name: Install dependencies
        run: uv sync --all-extras --dev

      - name: Lint
        run: uv run ruff check .

      - name: Check formatting
        run: uv run ruff format --check .

      - name: Test
        run: uv run pytest -v

      - name: Self-check (dogfood the CLI on examples/)
        run: uv run skill-eval run ./examples
```

- [ ] **Step 6: Verify the dogfood step works locally**

Run: `uv run skill-eval run ./examples`
Expected: exit code 0, output contains `1 passed`

- [ ] **Step 7: Run the full suite one final time**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`
Expected: all tests PASS (approximately 66 tests), ruff clean

- [ ] **Step 8: Commit**

```bash
git add examples .github/workflows/ci.yml tests/test_examples.py
git commit -m "feat: add example skill and CI workflow with dogfooding step"
```

---

## Self-Review

**1. Spec coverage (M0 + M1 scope only):**

| Spec requirement | Task |
|---|---|
| uv project, `src/skill_eval/` layout, ruff + pytest | Task 1 |
| Commitizen configured, `version_provider = "uv"` | Task 1 (`pyproject.toml`) |
| Single-source version via `importlib.metadata` | Task 1 |
| Pydantic models (`Skill`, `EvalCase`, `RunResult`, `EvalScore`, `RunReport`) | Task 2 |
| Skill loader with multi-skill recursive discovery | Task 3 |
| YAML case loader, convention-based discovery | Task 4 |
| `Runner` protocol + `FakeRunner` | Task 5 |
| `Evaluator` protocol + Assertion evaluator | Task 6 |
| Orchestrator, skill × case × runner matrix | Task 7 |
| Errored vs. failed distinction | Tasks 2, 7, 8 |
| Skills with no cases reported as skipped | Tasks 7, 9, 11 |
| Gating + exit code, per-skill thresholds | Task 8 |
| Console + JSON reporters | Task 9 |
| Cost/latency captured and reported | Tasks 2, 9 |
| `skill-eval.toml` upward discovery, CLI overrides config | Tasks 10, 11 |
| CLI `run` / `list` | Task 11 |
| CI workflow runs lint + pipeline-tier tests | Task 12 |
| Pipeline tier tested with zero API cost | All tasks (FakeRunner only) |

Deferred by design (M2+, not gaps): PydanticAI runner, trajectory evaluator, LLM-judge, cassettes, JUnit/Markdown reporters, `skill-eval init`, release workflow.

**2. Placeholder scan:** No TBDs, no "add error handling", no "similar to Task N". Every code step contains complete, runnable code.

**3. Type consistency:** Verified — `RunResult.errored` (property) used in Tasks 5/7; `Skill.path` is the skill *directory* everywhere (set by `parse_skill_file` from `skill_md.parent`, consumed by `_discover_paths`); `load_cases_for_skill(skill, evals_path=...)` keyword matches across Tasks 4/7/11; `EvalScore` field names (`evaluator`, `passed`, `score`, `detail`) consistent in Tasks 2/6/9; `GateResult.exit_code` used in Tasks 8/11; `FakeRunner.name = "fake"` matches the `_RUNNERS` registry key in Task 11 and the orchestrator test expectations in Task 7.
