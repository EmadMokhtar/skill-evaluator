# skill-lens `file_glob` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an assertion and a judge artifact name a workspace file by a one-level glob pattern that must match exactly one file.

**Architecture:** One matcher, `Workspace.match(pattern)`, plus one load-time check, `check_glob_pattern(pattern)`, both in `workspace.py`. `AssertionSpec` gains `file_glob`; `JudgeSpec.artifacts` entries may be `ArtifactGlob`. The assertion evaluator and the judge's artifact loader resolve the pattern at check time, then read the single match through the existing `Workspace.resolve` / `Workspace.read` path.

**Tech Stack:** Python 3.11+, Pydantic v2, `fnmatch.fnmatchcase`, `os.scandir`, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-14-skill-lens-file-glob-design.md`

## Global Constraints

- `file:` keeps its meaning exactly: a literal path. Nothing in an existing eval file changes what it checks.
- Wildcards: `*`, `?`, `[...]`, matched per segment with `fnmatch.fnmatchcase` (case-sensitive everywhere). A wildcard never crosses `/`. `**` is refused.
- Exactly one match. Zero or several is a **failed** check, never an error and never a pass.
- A pattern problem the author made (`**`, no wildcard, `..`, absolute, empty, unclosed `[`, a backslash) is an authoring error: `CaseParseError` at load time (exit 2), `InvalidAssertionValue` for a programmatic case.
- The matched path never reaches the judge prompt. The artifact is named by its pattern.
- Sentinels never consume the judge's artifact content budget.
- Check ids stay `{kind}[{index}]`.
- `extra="forbid"` on every new user-authored model.
- All tests offline and free. Tests are written before the code.
- Commits use Conventional Commits and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit with `uv run git commit` so the `pre-commit` hook finds its tools.

Two rules were decided while planning, beyond the spec text. They follow its reasoning:

- **A backslash in a pattern is refused.** `fnmatch` has no escape character, so `.planning\*\x.md` would be one segment that never matches on POSIX. Refusing it names the mistake.
- **A plain artifact and a glob artifact with the same text are refused.** Both would reach the prompt under one name for two different things.

## Review Focus

1. A pattern written with a `./` prefix (`./.planning/*/task_plan.md`) should match exactly like the same pattern without it. Task 1 tests this.
2. A pattern written with Windows separators (`.planning\*\task_plan.md`) should be refused at load with a message saying to use `/`, not silently match nothing. Tasks 1 and 3 test this.
3. A wildcard segment that matches both a file and a directory (`out/*` over `out/a.md` and `out/sub/`) should match only the file. Task 1 tests this.
4. A matched name that is a symlink to a regular file inside the workspace should be read like any other match; one pointing outside should be a failed check whose evidence is the refusal. Tasks 1 and 4 test this.
5. The same text used as both a plain artifact and a glob artifact should be refused at load, not shown to the judge as one name. Task 3 tests this.

---

### Task 1: The matcher and the pattern check

**Files:**
- Modify: `src/skill_lens/workspace.py` (imports; new helpers after `stat_regular`; new method on `Workspace` after `listing`)
- Test: `tests/test_workspace.py` (append)

**Interfaces:**
- Consumes: `check_relative_path(candidate: str) -> None` and `PathRefused` (existing, `workspace.py`).
- Produces:
  - `check_glob_pattern(pattern: str) -> None`: raises `PathRefused` for an unusable pattern.
  - `Workspace.match(self, pattern: str) -> list[str]`: sorted root-relative POSIX paths. It calls `check_glob_pattern` first, so it raises `PathRefused` for a bad pattern and never for anything the run wrote.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workspace.py`:

```python
# --- file_glob: matching by pattern ------------------------------------------

from skill_lens.workspace import check_glob_pattern  # noqa: E402

PLAN = ".planning/*/task_plan.md"


def test_match_finds_the_one_file_a_run_put_in_a_directory_it_named(tmp_path):
    ws = _workspace(tmp_path)
    ws.write(".planning/2026-10-11-todo/task_plan.md", "x")
    assert ws.match(PLAN) == [".planning/2026-10-11-todo/task_plan.md"]


def test_match_returns_nothing_when_nothing_matches(tmp_path):
    ws = _workspace(tmp_path)
    ws.write("task_plan.md", "x")
    assert ws.match(PLAN) == []


def test_match_returns_every_match_sorted(tmp_path):
    ws = _workspace(tmp_path)
    ws.write(".planning/b/task_plan.md", "x")
    ws.write(".planning/a/task_plan.md", "x")
    assert ws.match(PLAN) == [".planning/a/task_plan.md", ".planning/b/task_plan.md"]


def test_a_wildcard_never_crosses_a_slash(tmp_path):
    ws = _workspace(tmp_path)
    ws.write("a/b/x.md", "x")
    assert ws.match("*/x.md") == []


def test_a_directory_is_never_a_match(tmp_path):
    ws = _workspace(tmp_path)
    ws.write("out/a.md", "x")
    (tmp_path / "out" / "sub").mkdir()
    assert ws.match("out/*") == ["out/a.md"]


def test_matching_is_case_sensitive_on_every_platform(tmp_path):
    ws = _workspace(tmp_path)
    ws.write("Task_Plan.md", "x")
    assert ws.match("task_*.md") == []


def test_a_dot_slash_prefix_changes_nothing(tmp_path):
    ws = _workspace(tmp_path)
    ws.write(".planning/run/task_plan.md", "x")
    assert ws.match("./" + PLAN) == [".planning/run/task_plan.md"]


@posix_only
def test_a_fifo_is_never_a_match_and_is_never_opened(tmp_path):
    ws = _workspace(tmp_path)
    os.mkfifo(tmp_path / "report.md")
    assert promptly(lambda: ws.match("*.md")) == []


@posix_only
def test_a_symlinked_directory_is_not_followed(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "x.md").write_text("secret", encoding="utf-8")
    inside = tmp_path / "ws"
    inside.mkdir()
    os.symlink(outside, inside / "link")
    ws = _workspace(inside)
    assert ws.match("*/x.md") == []


@posix_only
def test_a_symlink_to_a_file_is_a_candidate_and_the_read_decides(tmp_path):
    ws = _workspace(tmp_path)
    ws.write("real.md", "body")
    os.symlink(tmp_path / "real.md", tmp_path / "alias.md")
    assert ws.match("alias*.md") == ["alias.md"]
    assert ws.read("alias.md") == "body"


@pytest.mark.parametrize(
    ("pattern", "fragment"),
    [
        ("**/task_plan.md", "'\\*\\*'"),
        (".planning/**/task_plan.md", "'\\*\\*'"),
        ("task_plan.md", "no wildcard"),
        ("../*.md", "'\\.\\.'"),
        ("/abs/*.md", "not a relative path"),
        ("", "must not be empty"),
        ("report[1.md", "no closing"),
        (".planning\\*\\task_plan.md", "backslash"),
    ],
)
def test_check_glob_pattern_refuses_what_could_never_work(pattern, fragment):
    with pytest.raises(PathRefused, match=fragment):
        check_glob_pattern(pattern)


@pytest.mark.parametrize(
    "pattern", [PLAN, "out/report-??.md", "logs/[0-9]*.log", "x[]]*.md", "[!.]*.md"]
)
def test_check_glob_pattern_accepts_one_level_patterns(pattern):
    check_glob_pattern(pattern)


def test_match_refuses_a_bad_pattern_before_touching_the_disk(tmp_path):
    with pytest.raises(PathRefused, match="'\\*\\*'"):
        _workspace(tmp_path).match("**/x.md")
```

Move the `from skill_lens.workspace import check_glob_pattern` line into the existing top-of-file import from `skill_lens.workspace` if ruff reports `E402`. `promptly` and `posix_only` already exist in this file.

- [ ] **Step 2: Run the tests and see them fail**

Run: `uv run pytest tests/test_workspace.py -k "match or glob" -v`
Expected: collection error, `ImportError: cannot import name 'check_glob_pattern'`.

- [ ] **Step 3: Implement**

In `src/skill_lens/workspace.py`, add `import fnmatch` to the standard-library imports and change `from pathlib import Path` to `from pathlib import Path, PurePosixPath`.

After `stat_regular`, add:

```python
# The characters that make a `file_glob` a pattern. A pattern with none of
# them is a `file:` spelled longer, so `check_glob_pattern` refuses it.
_WILDCARDS = frozenset("*?[")


def _has_unclosed_bracket(segment: str) -> bool:
    """True when a `[` in `segment` has no closing `]`.

    Reads a set the way `fnmatch.translate` does: an optional `!`, then a `]`
    that is a member of the set rather than its end. `fnmatch` treats an
    unclosed `[` as a literal character, so `report[1.md` would silently look
    for a file with a bracket in its name.
    """
    index = 0
    while index < len(segment):
        if segment[index] != "[":
            index += 1
            continue
        end = index + 1
        if end < len(segment) and segment[end] == "!":
            end += 1
        if end < len(segment) and segment[end] == "]":
            end += 1
        close = segment.find("]", end)
        if close == -1:
            return True
        index = close + 1
    return False


def check_glob_pattern(pattern: str) -> None:
    """Raise PathRefused unless `pattern` is a usable `file_glob`.

    Root-independent, like `check_relative_path`, so the loader calls it at
    load time. Every refusal is the author's mistake: a pattern that can
    never match what its author meant.
    """
    check_relative_path(pattern)
    if "\\" in pattern:
        raise PathRefused(
            f"refused: {pattern!r} contains a backslash; separate directories with '/'"
        )
    segments = PurePosixPath(pattern.strip()).parts
    if any("**" in segment for segment in segments):
        raise PathRefused(
            f"refused: {pattern!r} uses '**'; a wildcard matches within one "
            "directory, so write one '*' for each level"
        )
    if not any(_WILDCARDS & set(segment) for segment in segments):
        raise PathRefused(
            f"refused: {pattern!r} has no wildcard (*, ?, [...]); use 'file:' for a literal path"
        )
    for segment in segments:
        if _has_unclosed_bracket(segment):
            raise PathRefused(f"refused: {pattern!r} has a '[' with no closing ']'")


def _entries(directory: Path) -> list[os.DirEntry[str]]:
    """The entries of one directory, or none if it cannot be listed."""
    try:
        with os.scandir(directory) as iterator:
            return list(iterator)
    except OSError:
        return []


def _is_real_directory(entry: os.DirEntry[str]) -> bool:
    """A directory the walk may enter. A symlinked directory is not one."""
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _is_file_candidate(entry: os.DirEntry[str]) -> bool:
    """A name a glob may match: a regular file, or a symlink to anything but a directory.

    A FIFO, a device or a socket is never a match, decided from `lstat`
    without an `open`. A symlink is a candidate even when it dangles or
    loops; `Workspace.read` then refuses it, so the check fails with the
    refusal as its evidence, as it would for the same link named by `file:`.
    """
    try:
        if entry.is_symlink():
            return not entry.is_dir()
        return entry.is_file(follow_symlinks=False)
    except OSError:
        return True
```

On `Workspace`, after `listing`, add:

```python
    def match(self, pattern: str) -> list[str]:
        """Every file `pattern` matches, relative to the root, sorted.

        One directory listing per segment, never a recursive walk, and a
        symlinked directory is never entered, so the walk cannot leave the
        root. Matching is `fnmatch.fnmatchcase`, per segment, so a run gives
        the same answer on macOS and Linux. Deciding what one match, none or
        several *means* is the caller's job.
        """
        check_glob_pattern(pattern)
        *parents, last = PurePosixPath(pattern.strip()).parts
        directories = [self.root]
        for segment in parents:
            directories = [
                Path(entry.path)
                for directory in directories
                for entry in _entries(directory)
                if fnmatch.fnmatchcase(entry.name, segment) and _is_real_directory(entry)
            ]
        return sorted(
            Path(entry.path).relative_to(self.root).as_posix()
            for directory in directories
            for entry in _entries(directory)
            if fnmatch.fnmatchcase(entry.name, last) and _is_file_candidate(entry)
        )
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/test_workspace.py -v`
Expected: all PASS, the existing tests included.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/skill_lens/workspace.py tests/test_workspace.py
uv run ruff format src/skill_lens/workspace.py tests/test_workspace.py
git add src/skill_lens/workspace.py tests/test_workspace.py
uv run git commit -m "feat: match workspace files by a one-level glob pattern

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The models

**Files:**
- Modify: `src/skill_lens/models.py` (`AssertionSpec` near line 202; a new `ArtifactGlob` and `artifact_name` just before `JudgeSpec` near line 405; `JudgeSpec.artifacts`)
- Test: `tests/test_models.py` (append)

**Interfaces:**
- Produces:
  - `AssertionSpec.file_glob: str | None = None`.
  - `class ArtifactGlob(BaseModel)` with one field, `glob: str`. It is `extra="forbid"` and `frozen=True`, so it hashes.
  - `JudgeSpec.artifacts: list[str | ArtifactGlob]`.
  - `artifact_name(entry: str | ArtifactGlob) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py` (add `ArtifactGlob` and `artifact_name` to its `from skill_lens.models import (...)` block):

```python
def test_an_artifact_entry_is_a_path_or_a_glob():
    spec = JudgeSpec.model_validate(
        {"rubric": ["r"], "artifacts": ["a.md", {"glob": ".planning/*/b.md"}]}
    )
    assert spec.artifacts == ["a.md", ArtifactGlob(glob=".planning/*/b.md")]


def test_an_artifact_glob_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        JudgeSpec.model_validate({"rubric": ["r"], "artifacts": [{"glob": "*.md", "path": "x"}]})


def test_an_artifact_glob_hashes_so_duplicates_collapse():
    assert len({ArtifactGlob(glob="a*"), ArtifactGlob(glob="a*")}) == 1


def test_an_artifact_is_named_by_what_the_author_wrote():
    assert artifact_name("report.md") == "report.md"
    assert artifact_name(ArtifactGlob(glob=".planning/*/progress.md")) == ".planning/*/progress.md"


def test_an_assertion_carries_a_file_glob():
    spec = AssertionSpec(kind="file-produced", file_glob=".planning/*/task_plan.md")
    assert spec.file_glob == ".planning/*/task_plan.md"
    assert spec.file is None
```

If `ValidationError` is not already imported in this file, add `from pydantic import ValidationError`.

- [ ] **Step 2: Run the tests and see them fail**

Run: `uv run pytest tests/test_models.py -v -k "artifact or file_glob"`
Expected: `ImportError: cannot import name 'ArtifactGlob'`.

- [ ] **Step 3: Implement**

In `AssertionSpec`, add the field after `file` and extend the docstring's first paragraph:

```python
    `file_glob` is the pattern form of `file`: a one-level glob that must
    match exactly one workspace file. A spec carries one of the two, never
    both; `cases/loader.py` enforces that and treats `file_glob` as `file`
    in its per-kind table.
```

```python
    file: str | None = None
    file_glob: str | None = None
```

Just before `class JudgeSpec`, add:

```python
class ArtifactGlob(BaseModel):
    """A judge artifact named by a pattern rather than a path.

    The pattern, not the file it matches, is the artifact's name in the
    prompt: the author wrote the pattern, the model chose the path. Frozen
    so it hashes, because `_artifacts` deduplicates entries with
    `dict.fromkeys`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    glob: str


def artifact_name(entry: str | ArtifactGlob) -> str:
    """What an artifact is called in the judge prompt: the text its author wrote."""
    return entry if isinstance(entry, str) else entry.glob
```

In `JudgeSpec`, change the field:

```python
    artifacts: list[str | ArtifactGlob] = Field(default_factory=list)
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/test_models.py tests/test_judge_evaluator.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/skill_lens/models.py tests/test_models.py && uv run ruff format src/skill_lens/models.py tests/test_models.py
git add src/skill_lens/models.py tests/test_models.py
uv run git commit -m "feat: add file_glob to assertions and a glob form for judge artifacts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The load-time rules

**Files:**
- Modify: `src/skill_lens/cases/loader.py`. That is `_ASSERTION_FIELD_NAMES` near line 218, `_validate_assertions` near line 221, `_validate_workspace` near line 278, and the judge-artifacts message in `_validate_cross_references` near line 378.
- Test: `tests/test_case_loader.py` (append)

**Interfaces:**
- Consumes: `check_glob_pattern` (Task 1); `ArtifactGlob`, `artifact_name` (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_case_loader.py`. `_write` already exists in this file, and `parse_cases_file` and `CaseParseError` are already imported.

```python
# --- file_glob ---------------------------------------------------------------

_GLOB_CASE = "cases:\n  - name: n\n    task: t\n    workspace: {{}}\n    assertions:\n{assertions}"


def _glob_case(tmp_path, assertions: str):
    return _write(tmp_path, _GLOB_CASE.format(assertions=assertions))


def test_file_produced_accepts_a_file_glob(tmp_path):
    path = _glob_case(
        tmp_path, "      - kind: file-produced\n        file_glob: .planning/*/task_plan.md\n"
    )
    [case] = parse_cases_file(path)
    assert case.assertions[0].file_glob == ".planning/*/task_plan.md"


def test_a_content_kind_accepts_a_file_glob(tmp_path):
    path = _glob_case(
        tmp_path,
        "      - kind: regex\n        value: '(?m)^## Goal'\n"
        "        file_glob: .planning/*/task_plan.md\n",
    )
    [case] = parse_cases_file(path)
    assert case.assertions[0].file_glob == ".planning/*/task_plan.md"


def test_file_and_file_glob_together_are_refused(tmp_path):
    path = _glob_case(
        tmp_path,
        "      - kind: file-produced\n        file: task_plan.md\n"
        "        file_glob: .planning/*/task_plan.md\n",
    )
    with pytest.raises(CaseParseError, match="both file and file_glob"):
        parse_cases_file(path)


def test_file_produced_with_neither_names_both_keys(tmp_path):
    path = _glob_case(tmp_path, "      - kind: file-produced\n")
    with pytest.raises(CaseParseError, match=r"requires file \(or file_glob\)"):
        parse_cases_file(path)


def test_a_file_glob_with_no_workspace_is_refused(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    assertions:\n"
        "      - kind: file-produced\n        file_glob: .planning/*/task_plan.md\n",
    )
    with pytest.raises(CaseParseError, match="targets file_glob"):
        parse_cases_file(path)


@pytest.mark.parametrize(
    ("pattern", "fragment"),
    [
        ("'**/task_plan.md'", "'\\*\\*'"),
        ("task_plan.md", "no wildcard"),
        ("'../*.md'", "'\\.\\.'"),
        ("'report[1.md'", "no closing"),
        ("'.planning\\*\\task_plan.md'", "backslash"),
    ],
)
def test_an_unusable_file_glob_is_refused_at_load(tmp_path, pattern, fragment):
    path = _glob_case(tmp_path, f"      - kind: file-produced\n        file_glob: {pattern}\n")
    with pytest.raises(CaseParseError, match=fragment):
        parse_cases_file(path)


_ARTIFACT_CASE = (
    "cases:\n  - name: n\n    task: t\n{workspace}    assertions:\n"
    "      - kind: contains\n        value: x\n"
    "    judge:\n      rubric: [it has a total]\n      artifacts:\n{artifacts}"
)


def _artifact_case(tmp_path, artifacts: str, workspace: str = "    workspace: {}\n"):
    return _write(tmp_path, _ARTIFACT_CASE.format(workspace=workspace, artifacts=artifacts))


def test_a_glob_artifact_loads(tmp_path):
    from skill_lens.models import ArtifactGlob

    path = _artifact_case(tmp_path, "        - glob: .planning/*/progress.md\n")
    [case] = parse_cases_file(path)
    assert case.judge.artifacts == [ArtifactGlob(glob=".planning/*/progress.md")]


def test_a_glob_artifact_with_no_workspace_is_refused_naming_the_pattern(tmp_path):
    path = _artifact_case(tmp_path, "        - glob: .planning/*/progress.md\n", workspace="")
    with pytest.raises(CaseParseError, match=r"names judge artifacts \['\.planning/\*/progress\.md'\]"):
        parse_cases_file(path)


def test_an_unusable_glob_artifact_is_refused(tmp_path):
    path = _artifact_case(tmp_path, "        - glob: '**/progress.md'\n")
    with pytest.raises(CaseParseError, match="names judge artifact"):
        parse_cases_file(path)


def test_a_glob_artifact_with_an_unknown_key_is_refused(tmp_path):
    path = _artifact_case(tmp_path, "        - glob: '*/progress.md'\n          path: x\n")
    with pytest.raises(CaseParseError):
        parse_cases_file(path)


def test_the_same_text_as_a_path_and_as_a_glob_is_refused(tmp_path):
    path = _artifact_case(
        tmp_path, "        - '*/progress.md'\n        - glob: '*/progress.md'\n"
    )
    with pytest.raises(CaseParseError, match="once as a path and once as a glob"):
        parse_cases_file(path)
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `uv run pytest tests/test_case_loader.py -v -k "glob"`
Expected: FAIL. `file_produced_accepts_a_file_glob` fails with `requires file, which is missing`. The pattern tests fail with "DID NOT RAISE".

- [ ] **Step 3: Implement**

Imports in `loader.py`: add `check_glob_pattern` to the `from skill_lens.workspace import ...` line. Add `ArtifactGlob, artifact_name` to the `from skill_lens.models import ...` block.

Replace `_ASSERTION_FIELD_NAMES`, and update the comment above `_ASSERTION_FIELDS` to say `file` there means "`file` or `file_glob`":

```python
_ASSERTION_FIELD_NAMES = ("value", "file", "file_glob", "json_schema")
```

In `_validate_assertions`, replace the block from `present = ...` through the workspace check with:

```python
        if spec.file is not None and spec.file_glob is not None:
            raise CaseParseError(
                f"{where} sets both file and file_glob. Use file for a literal path "
                f"or file_glob for a pattern, not both."
            )
        present = {name for name in _ASSERTION_FIELD_NAMES if getattr(spec, name) is not None}
        # file_glob is the pattern form of file: the table says `file` and means either.
        if "file_glob" in present:
            present = (present - {"file_glob"}) | {"file"}
        missing = sorted(required - present)
        if missing:
            named = ["file (or file_glob)" if name == "file" else name for name in missing]
            raise CaseParseError(
                f"{where} ({spec.kind}) requires {', '.join(named)}, which is missing."
            )
        extra = sorted(present - required - optional)
        if extra:
            raise CaseParseError(
                f"{where} ({spec.kind}) does not accept {', '.join(extra)}. "
                f"It would assert nothing, so it is a mistake rather than a check."
            )
        key, target = ("file", spec.file) if spec.file is not None else ("file_glob", spec.file_glob)
        if target is not None and case.workspace is None:
            raise CaseParseError(
                f"{where} targets {key} {target!r}, but the case declares no "
                f"'workspace:' block. There would be no file to look at, so the "
                f"assertion could never hold."
            )
```

In `_validate_workspace`, replace the `judge.artifacts` loop:

```python
    if case.judge is not None:
        kinds: dict[str, str] = {}
        for entry in case.judge.artifacts:
            name = artifact_name(entry)
            kind = "glob" if isinstance(entry, ArtifactGlob) else "path"
            if kinds.setdefault(name, kind) != kind:
                raise CaseParseError(
                    f"{path}: case {case.name!r} names judge artifact {name!r} "
                    f"once as a path and once as a glob. The judge would see one "
                    f"name for two different things; keep one."
                )
            try:
                if isinstance(entry, ArtifactGlob):
                    check_glob_pattern(entry.glob)
                else:
                    check_relative_path(entry)
            except PathRefused as exc:
                raise CaseParseError(
                    f"{path}: case {case.name!r} names judge artifact {name!r}: {exc}"
                ) from exc
```

In the same function's assertion loop, check the pattern before the existing `file` check:

```python
    for position, spec in enumerate(case.assertions, start=1):
        if spec.file_glob is not None:
            try:
                check_glob_pattern(spec.file_glob)
            except PathRefused as exc:
                raise CaseParseError(
                    f"{path}: case {case.name!r} assertion #{position} targets file_glob "
                    f"{spec.file_glob!r}: {exc}"
                ) from exc
        if spec.file is None:
            continue
        # existing check_relative_path block, unchanged
```

In `_validate_cross_references`, make the no-workspace message print names, not models:

```python
        if case.judge.artifacts and case.workspace is None:
            raise CaseParseError(
                f"{path}: case {case.name!r} names judge artifacts "
                f"{[artifact_name(entry) for entry in case.judge.artifacts]}, but declares "
                f"no 'workspace:' block. There would be no files to read."
            )
```

Add one sentence to `_validate_workspace`'s docstring: "A `file_glob` and a glob artifact get `check_glob_pattern`, which also refuses `**` and a pattern with no wildcard."

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/test_case_loader.py tests/test_assertion_evaluator.py tests/test_orchestrator.py -v`
Expected: all PASS, the existing tests included.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/skill_lens/cases/loader.py tests/test_case_loader.py && uv run ruff format src/skill_lens/cases/loader.py tests/test_case_loader.py
git add src/skill_lens/cases/loader.py tests/test_case_loader.py
uv run git commit -m "feat: refuse an unusable file_glob or glob artifact at load time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The assertion evaluator

**Files:**
- Modify: `src/skill_lens/evaluators/assertion.py` (`_describe`, `_authored_path`, `_subject_text`, `_check`)
- Test: `tests/test_assertion_evaluator.py` (append)

**Interfaces:**
- Consumes: `Workspace.match`, `check_glob_pattern` (Task 1); `AssertionSpec.file_glob` (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_assertion_evaluator.py` (`_case` exists; `json`, `os`, `pytest` are imported):

```python
# --- file_glob ---------------------------------------------------------------

PLAN = ".planning/*/task_plan.md"


def _tree(tmp_path, files: dict[str, str]) -> RunResult:
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return RunResult(output="chat output", workspace=tmp_path.resolve())


@pytest.mark.parametrize(
    "spec",
    [
        AssertionSpec(kind="file-produced", file_glob=PLAN),
        AssertionSpec(kind="contains", value="## Goal", file_glob=PLAN),
        AssertionSpec(kind="not_contains", value="pickle", file_glob=PLAN),
        AssertionSpec(kind="regex", value="(?m)^## Goal", file_glob=PLAN),
        AssertionSpec(kind="equals", value="## Goal", file_glob=PLAN),
    ],
)
def test_a_file_glob_reads_the_one_file_it_matches(tmp_path, spec):
    result = _tree(tmp_path, {".planning/2026-10-11-todo/task_plan.md": "## Goal\n"})
    assert AssertionEvaluator().evaluate(_case(spec), result).passed is True


def test_json_schema_reads_the_one_file_a_glob_matches(tmp_path):
    result = _tree(tmp_path, {"out/run-7.json": json.dumps({"total": 3})})
    spec = AssertionSpec(
        kind="json-schema",
        file_glob="out/*.json",
        json_schema={"type": "object", "required": ["total"]},
    )
    assert AssertionEvaluator().evaluate(_case(spec), result).passed is True


def test_a_glob_matching_nothing_fails_and_shows_the_workspace(tmp_path):
    result = _tree(tmp_path, {"todo.py": "x"})
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="file-produced", file_glob=PLAN)), result
    )
    [check] = score.checks
    assert check.passed is False
    assert check.id == "file-produced[0]"
    assert f"no file matched {PLAN}; workspace held todo.py" in check.evidence


def test_a_glob_matching_several_files_fails_and_names_them(tmp_path):
    result = _tree(
        tmp_path,
        {".planning/a/task_plan.md": "## Goal", ".planning/b/task_plan.md": "## Goal"},
    )
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="contains", value="## Goal", file_glob=PLAN)), result
    )
    [check] = score.checks
    assert check.passed is False
    assert (
        f"{PLAN} matched 2 files: .planning/a/task_plan.md, .planning/b/task_plan.md; "
        "exactly one is required"
    ) in check.evidence


def test_the_evidence_says_a_pattern_was_used(tmp_path):
    result = _tree(tmp_path, {".planning/x/task_plan.md": "y"})
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="regex", value="Goal", file_glob=PLAN)), result
    )
    assert f"file_glob='{PLAN}'" in score.checks[0].evidence


@pytest.mark.skipif(os.name == "nt", reason="symlinks are a POSIX feature here")
def test_a_matched_symlink_pointing_outside_is_a_failed_check(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("## Goal", encoding="utf-8")
    (tmp_path / "out").mkdir()
    os.symlink(outside, tmp_path / "out" / "report.md")
    result = RunResult(output="o", workspace=tmp_path.resolve())
    score = AssertionEvaluator().evaluate(
        _case(AssertionSpec(kind="contains", value="Goal", file_glob="out/*.md")), result
    )
    assert score.passed is False
    assert "resolves outside" in score.checks[0].evidence


def test_a_programmatic_case_with_both_file_and_file_glob_is_an_authoring_error(tmp_path):
    spec = AssertionSpec(kind="file-produced", file="a.md", file_glob="*.md")
    with pytest.raises(InvalidAssertionValue, match="both file and file_glob"):
        AssertionEvaluator().evaluate(_case(spec), _tree(tmp_path, {}))


def test_a_programmatic_case_with_a_recursive_glob_is_an_authoring_error(tmp_path):
    spec = AssertionSpec(kind="file-produced", file_glob="**/x.md")
    with pytest.raises(InvalidAssertionValue, match="'\\*\\*'"):
        AssertionEvaluator().evaluate(_case(spec), _tree(tmp_path, {}))
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `uv run pytest tests/test_assertion_evaluator.py -v -k "glob"`
Expected: FAIL. With no `file`, a file-produced check reads `file=""` and a content check reads the chat output, so the results are wrong.

- [ ] **Step 3: Implement**

In `assertion.py`, change the workspace import to `from skill_lens.workspace import PathRefused, Workspace, check_glob_pattern, check_relative_path`.

Replace `_describe`:

```python
def _describe(spec: AssertionSpec) -> str:
    if spec.kind == "file-produced":
        if spec.file_glob is not None:
            return f"file-produced(file_glob={spec.file_glob!r})"
        return f"file-produced({spec.file!r})"
    if spec.kind == "json-schema":
        if spec.file_glob is not None:
            return f"json-schema(file_glob={spec.file_glob!r})"
        return f"json-schema({spec.file!r})" if spec.file else "json-schema(output)"
    if spec.file_glob is not None:
        target = f", file_glob={spec.file_glob!r}"
    else:
        target = f", file={spec.file!r}" if spec.file else ""
    return f"{spec.kind}({spec.value!r}{target})"
```

Replace `_authored_path` with two functions:

```python
def _authored_path(spec: AssertionSpec) -> str:
    """The `file:` or `file_glob:` the author wrote, or raise if it could never work.

    An empty, absolute or `..`-bearing path, a recursive or wildcard-free
    pattern, or both keys at once is a mistake in the eval file, so it
    aborts the run as an authoring error. Everything *about the target* --
    what the run actually put there -- is judged later, by `Workspace`, and
    is the skill's doing rather than the author's.
    """
    if spec.file is not None and spec.file_glob is not None:
        raise InvalidAssertionValue("an assertion sets both file and file_glob; use one")
    try:
        if spec.file_glob is not None:
            check_glob_pattern(spec.file_glob)
            return spec.file_glob
        check_relative_path(spec.file or "")
    except PathRefused as exc:
        raise InvalidAssertionValue(str(exc)) from exc
    return spec.file or ""


def _locate(spec: AssertionSpec, workspace: Workspace) -> tuple[str, str]:
    """(the path to check, '') or ('', why no single file was found).

    A pattern must match exactly one file. None and several both fail the
    check: two plan directories must never pass by luck.
    """
    authored = _authored_path(spec)
    if spec.file_glob is None:
        return authored, ""
    matches = workspace.match(authored)
    if not matches:
        return "", f"no file matched {authored}; {_listing(workspace)}"
    if len(matches) > 1:
        shown = ", ".join(matches[:_LISTING_LIMIT])
        if len(matches) > _LISTING_LIMIT:
            shown = f"{shown}, +{len(matches) - _LISTING_LIMIT} more"
        return "", f"{authored} matched {len(matches):,} files: {shown}; exactly one is required"
    return matches[0], ""
```

In `_subject_text`, replace the lines from `if spec.file is None:` through `file = _authored_path(spec)`:

```python
    if spec.file is None and spec.file_glob is None:
        return result.output, ""
    workspace = _workspace_of(result)
    file, missing = _locate(spec, workspace)
    if missing:
        return "", missing
```

The `try:` block that follows stays unchanged.

In `_check`, the `file-produced` branch becomes:

```python
    if spec.kind == "file-produced":
        workspace = _workspace_of(result)
        file, missing = _locate(spec, workspace)
        if missing:
            return False, missing
        try:
            target = workspace.resolve(file)
        except PathRefused as exc:
            return False, f"expected {file}; {exc}"
        if target.is_file():
            return True, ""
        return False, f"expected {file}; {_listing(workspace)}"
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/test_assertion_evaluator.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/skill_lens/evaluators/assertion.py tests/test_assertion_evaluator.py && uv run ruff format src/skill_lens/evaluators/assertion.py tests/test_assertion_evaluator.py
git add src/skill_lens/evaluators/assertion.py tests/test_assertion_evaluator.py
uv run git commit -m "feat: score file assertions against the one file a file_glob matches

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Judge artifacts

**Files:**
- Modify: `src/skill_lens/evaluators/judge.py` (imports, a new `several_matches`, `_artifacts`)
- Modify: `src/skill_lens/judges/prompt.py` (the `_artifact_block` docstring line about where the name comes from)
- Test: `tests/test_judge_evaluator.py` (append)

**Interfaces:**
- Consumes: `Workspace.match` (Task 1); `ArtifactGlob`, `artifact_name` (Task 2).
- Produces: `several_matches(count: int) -> str`, the sentinel shown when a glob matches more than one file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_judge_evaluator.py` (add `several_matches` to the `from skill_lens.evaluators.judge import (...)` block, and `ArtifactGlob` to the models import; `_case(*artifacts)` exists):

```python
# --- glob artifacts ----------------------------------------------------------

from skill_lens.judges.prompt import render_request  # noqa: E402

PROGRESS = ArtifactGlob(glob=".planning/*/progress.md")


def _put(tmp_path, name: str, content: str) -> None:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_a_glob_artifact_is_read_from_its_one_match_and_named_by_its_pattern(tmp_path):
    _put(tmp_path, ".planning/2026-10-11-secret-slug/progress.md", "Session 2: done")
    request = build_request(_case(PROGRESS), RunResult(output="o", workspace=tmp_path))
    assert request.artifacts == {".planning/*/progress.md": "Session 2: done"}


def test_the_matched_path_never_reaches_the_prompt(tmp_path):
    _put(tmp_path, ".planning/2026-10-11-secret-slug/progress.md", "Session 2: done")
    prompt = render_request(
        build_request(_case(PROGRESS), RunResult(output="o", workspace=tmp_path))
    )
    assert ".planning/*/progress.md" in prompt
    assert "Session 2: done" in prompt
    assert "secret-slug" not in prompt


def test_a_glob_artifact_matching_nothing_is_not_produced(tmp_path):
    request = build_request(_case(PROGRESS), RunResult(output="o", workspace=tmp_path))
    assert request.artifacts == {".planning/*/progress.md": NOT_PRODUCED}


def test_a_glob_artifact_with_no_workspace_is_not_produced():
    request = build_request(_case(PROGRESS), RunResult(output="o"))
    assert request.artifacts == {".planning/*/progress.md": NOT_PRODUCED}


def test_a_glob_artifact_matching_several_files_says_so(tmp_path):
    _put(tmp_path, ".planning/a/progress.md", "a")
    _put(tmp_path, ".planning/b/progress.md", "b")
    request = build_request(_case(PROGRESS), RunResult(output="o", workspace=tmp_path))
    assert request.artifacts == {".planning/*/progress.md": several_matches(2)}
    assert several_matches(2) == "(matched 2 files; exactly one is required)"


def test_the_several_matches_sentinel_spends_no_content_budget(tmp_path):
    # Three full-size files fill the total budget exactly. If the sentinel
    # spent any of it, the third file would come back truncated.
    assert MAX_ARTIFACTS_TOTAL_BYTES == 3 * MAX_ARTIFACT_BYTES
    _put(tmp_path, ".planning/a/progress.md", "a")
    _put(tmp_path, ".planning/b/progress.md", "b")
    full = "x" * MAX_ARTIFACT_BYTES
    for name in ("one.md", "two.md", "three.md"):
        _put(tmp_path, name, full)
    request = build_request(
        _case(PROGRESS, "one.md", "two.md", "three.md"),
        RunResult(output="o", workspace=tmp_path),
    )
    assert request.artifacts["three.md"] == full


def test_a_path_and_a_glob_naming_one_file_are_two_artifacts(tmp_path):
    _put(tmp_path, "run/progress.md", "body")
    request = build_request(
        _case("run/progress.md", ArtifactGlob(glob="*/progress.md")),
        RunResult(output="o", workspace=tmp_path),
    )
    assert request.artifacts == {"run/progress.md": "body", "*/progress.md": "body"}
```

Move the `render_request` import to the top of the file if ruff reports `E402`.

- [ ] **Step 2: Run the tests and see them fail**

Run: `uv run pytest tests/test_judge_evaluator.py -v -k "glob or several or matched_path"`
Expected: `ImportError: cannot import name 'several_matches'`.

- [ ] **Step 3: Implement**

In `judge.py`, add `ArtifactGlob` and `artifact_name` to the `from skill_lens.models import (...)` block. Below `BUDGET_EXHAUSTED`, add:

```python
def several_matches(count: int) -> str:
    """Rendered in place of content when a glob artifact matched more than one file.

    Fixed harness text like every sentinel above, so it never spends the
    content budget. It names a count, never the paths: those were chosen by
    the model and stay out of the prompt.
    """
    return f"(matched {count:,} files; exactly one is required)"
```

In `_artifacts`, replace everything from `names = list(dict.fromkeys(spec.artifacts))` down to `content = workspace.read(name)` (keep the `try:` header and its `except` arms) with:

```python
    entries = list(dict.fromkeys(spec.artifacts))
    if not entries:
        return {}
    if result.workspace is None:
        return {artifact_name(entry): NOT_PRODUCED for entry in entries}
    workspace = Workspace(root=result.workspace)
    remaining = MAX_ARTIFACTS_TOTAL_BYTES
    artifacts: dict[str, str] = {}
    for entry in entries:
        # The prompt names an artifact by what its author wrote. For a glob
        # that is the pattern, never the path the model chose.
        name = artifact_name(entry)
        if isinstance(entry, ArtifactGlob):
            try:
                matches = workspace.match(entry.glob)
            except PathRefused:
                artifacts[name] = NOT_PRODUCED
                continue
            if not matches:
                artifacts[name] = NOT_PRODUCED
                continue
            if len(matches) > 1:
                artifacts[name] = several_matches(len(matches))
                continue
            target = matches[0]
        else:
            target = entry
        try:
            content = workspace.read(target)
```

The `except` arms and the budget block after them stay as they are; they already assign `artifacts[name]`. Add to the docstring's opening line: "A glob entry is read from its one match; none is `NOT_PRODUCED`, several is `several_matches`."

In `judges/prompt.py`, update the docstring sentence in `_artifact_block` to read: "The name is interpolated unfenced because it comes from `case.judge.artifacts` (a path or a glob pattern), which the eval author wrote -- only the content is untrusted, and a glob's matched path never reaches here."

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/test_judge_evaluator.py tests/test_judge_prompt.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/skill_lens/evaluators/judge.py src/skill_lens/judges/prompt.py tests/test_judge_evaluator.py && uv run ruff format src/skill_lens/evaluators/judge.py src/skill_lens/judges/prompt.py tests/test_judge_evaluator.py
git add src/skill_lens/evaluators/judge.py src/skill_lens/judges/prompt.py tests/test_judge_evaluator.py
uv run git commit -m "feat: read a judge artifact from the one file its glob matches

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: End to end through the orchestrator

**Files:**
- Test: `tests/test_orchestrator.py` (append)

**Interfaces:**
- Consumes: everything above. `_skill`, `_case` and `_evals` exist in this file, and `_evals` round-trips the case through the real loader.

- [ ] **Step 1: Write the tests**

```python
def test_a_file_glob_finds_a_file_in_a_directory_the_run_named(tmp_path):
    runner = FakeRunner(
        default=RunResult(output="done"),
        writes={"t": {".planning/2026-10-14-todo/task_plan.md": "## Goal\nship it\n"}},
    )
    case = _case(
        workspace=WorkspaceSpec(),
        assertions=[
            AssertionSpec(kind="file-produced", file_glob=".planning/*/task_plan.md"),
            AssertionSpec(kind="regex", value="(?m)^## Goal", file_glob=".planning/*/task_plan.md"),
        ],
    )
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.passed == 1


def test_a_run_that_starts_two_plans_fails_a_file_glob(tmp_path):
    runner = FakeRunner(
        default=RunResult(output="done"),
        writes={"t": {".planning/a/task_plan.md": "x", ".planning/b/task_plan.md": "x"}},
    )
    case = _case(
        workspace=WorkspaceSpec(),
        assertions=[AssertionSpec(kind="file-produced", file_glob=".planning/*/task_plan.md")],
    )
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.failed == 1
```

Add `AssertionSpec`, `RunResult` and `WorkspaceSpec` to the file's models import if any is missing.

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_orchestrator.py -v -k "file_glob"`
Expected: both PASS. They pin the whole path; Tasks 1–5 already made them pass. If one fails, the bug is in an earlier task, so fix it there.

- [ ] **Step 3: Commit**

```bash
uv run ruff format tests/test_orchestrator.py
git add tests/test_orchestrator.py
uv run git commit -m "test: pin file_glob end to end through the orchestrator

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation and invariants

**Files:**
- Modify: `docs/eval-files.md` (section "## Assertion kinds", and the `artifacts` part of "## Judging output quality")
- Modify: `plugins/skill-lens/skills/writing-skill-evals/references/eval-file-syntax.md` (after the `file` modifier example)
- Modify: `docs/invariants.md` (section "## Workspaces and files", after "### `file:` or `judge.artifacts` in a case with no `workspace:` block is an authoring error")
- Modify: `CLAUDE.md` (invariant list, after the `**`file:` or `judge.artifacts` in a case with no `workspace:` block is an authoring error**` bullet)
- Test: `tests/test_docs.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_docs.py`:

```python
def test_file_glob_is_documented_for_users_and_in_the_skill():
    assert "`file_glob`" in _page("eval-files.md")
    syntax = (
        REPO_ROOT / "plugins/skill-lens/skills/writing-skill-evals/references/eval-file-syntax.md"
    ).read_text(encoding="utf-8")
    assert "file_glob" in syntax
```

If `REPO_ROOT` is not defined in `tests/test_docs.py`, use `Path(__file__).resolve().parents[1]`.

Run: `uv run pytest tests/test_docs.py -v -k file_glob`
Expected: FAIL.

- [ ] **Step 2: `docs/eval-files.md`**

In "## Assertion kinds", after the paragraph that ends "…for the read rules.", insert:

````markdown
### Matching a file by pattern

Some skills write to a path the model chooses: `planning-with-files` puts its plan in
`.planning/<date>-<slug>/`. `file_glob` is the pattern form of `file`, accepted on every
kind that accepts `file`:

```yaml
    assertions:
      - kind: file-produced
        file_glob: .planning/*/task_plan.md
      - kind: regex
        value: "(?m)^## Goal"
        file_glob: .planning/*/task_plan.md
```

- `*`, `?` and `[...]` match within one directory level; a wildcard never crosses `/`, and
  `*` matches a leading dot. `**` is refused. Matching is case-sensitive on every platform.
- The pattern must match **exactly one** file. No match fails the check and lists the
  workspace; two or more fails it and names the matches. Neither is an error, and neither
  is a pass.
- A directory is never a match, and a symbolic link to a directory is never entered.
- A pattern with no wildcard, `**`, `..`, an absolute path, a backslash, an unclosed `[`,
  or `file` and `file_glob` on one assertion is an authoring error (exit `2`).
- The single match is read under the same rules as `file:`. A match that is a symbolic
  link pointing outside the workspace fails the check, with the refusal as evidence.
- A pattern can match the skill's own files, because a product runner delivers the skill
  into the workspace (`.claude/skills/<name>/`, `.agents/skills/<name>/`). With one-level
  wildcards that happens only if you write those directory names yourself.
````

In "## Judging output quality", after the paragraph that introduces `artifacts:`, insert:

````markdown
An entry may be a pattern instead of a path, under the same exactly-one rule as
[`file_glob`](#matching-a-file-by-pattern):

```yaml
    judge:
      artifacts:
        - glob: .planning/*/progress.md
      rubric:
        - progress.md has an entry for this session
```

The judge sees the artifact under its pattern. The path the agent chose never enters the
prompt. No match is shown as `(not produced)`; several as
`(matched N files; exactly one is required)`. The same text as a path and as a glob in one
list is an authoring error.
````

- [ ] **Step 3: The skill's syntax reference**

In `eval-file-syntax.md`, after the `file` modifier example block, insert:

````markdown
For a file whose path the model chooses, use `file_glob` instead of `file`: one-level
wildcards (`*`, `?`, `[...]`, no `**`), and it must match exactly one file, or the check
fails. A judge artifact takes the same pattern as `- glob: <pattern>`.

```yaml
      - kind: file-produced
        file_glob: .planning/*/task_plan.md
```
````

- [ ] **Step 4: Invariants, both places, the same words**

In `docs/invariants.md`, after the "### `file:` or `judge.artifacts` in a case with no `workspace:` block is an authoring error" block, insert:

```markdown
### A `file_glob` must match exactly one regular file

`Workspace.match` is the one matcher, used by `AssertionEvaluator` and the judge's
`_artifacts`. No match and several matches are both failed checks, never errors and never
passes: two plan directories must not pass by luck, and a missing file is the skill's
doing. `**`, a pattern with no wildcard, a backslash and an unclosed `[` are authoring
errors from `check_glob_pattern`, at load time. A FIFO or a device is never a match, and
a symlinked directory is never entered. The single match is then read through
`Workspace.read`, so every existing read rule applies to it unchanged.

### A matched path never reaches the judge prompt

A glob artifact is named in the prompt by its pattern, which the author wrote. The path it
matched was chosen by the model, so it is untrusted, and it is never interpolated into the
prompt as a name or anywhere else. Several matches render `several_matches(n)`, a fixed
sentinel naming a count, which, like every sentinel, spends none of the artifact content
budget.
```

In `CLAUDE.md`, after the bullet that starts `- **\`file:\` or \`judge.artifacts\` in a case with no \`workspace:\` block is an authoring error**`, insert:

```markdown
- **A `file_glob` must match exactly one regular file.** Zero or several is a failed check,
  never an error and never a pass; `**`, no wildcard, a backslash and an unclosed `[` are
  authoring errors from `check_glob_pattern`. `Workspace.match` is the one matcher; the match
  is then read through `Workspace.read`, so every read rule still applies.
- **A matched path never reaches the judge prompt.** A glob artifact is named by its
  pattern; the model-chosen path is untrusted, and several matches render a count-only
  sentinel that spends no content budget.
```

- [ ] **Step 5: Run every docs check**

```bash
uv run pytest tests/test_docs.py tests/test_invariants_sync.py tests/test_shipped_skill.py tests/test_plugin.py -v
uv sync --group docs
uv run mkdocs build --strict
```

Expected: all PASS; mkdocs builds with no warnings.

- [ ] **Step 6: Commit**

```bash
git add docs/eval-files.md plugins/skill-lens/skills/writing-skill-evals/references/eval-file-syntax.md docs/invariants.md CLAUDE.md tests/test_docs.py
uv run git commit -m "docs: document file_glob and its two invariants

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Whole-branch verification

- [ ] **Step 1: Run everything CI runs**

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run skill-lens list ./examples
uv run mkdocs build --strict
```

Expected: every test passes, ruff reports nothing, and the examples list exactly as on `main`.

- [ ] **Step 2: Re-read the docs as a set**

Read `docs/eval-files.md` ("Assertion kinds", "Judging output quality"), `docs/invariants.md` ("Workspaces and files") and the skill's `eval-file-syntax.md` one after another. Check that they say the same thing in the same words: the exactly-one rule, the refused patterns, and what the judge sees. Fix any drift in a `docs:` commit.

- [ ] **Step 3: Stop**

Do not push, and do not open the issue or the PR. Those happen after the user reviews the issue text.
