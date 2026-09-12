# skill-lens M8 Part 2 — Runner Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one invocation run every case through more than one runner — `--runner pydantic-ai --runner langchain`, or `default_runner = ["pydantic-ai", "langchain"]` — and produce one report.

**Architecture:** The orchestrator has taken `runners: list[Runner]` since M1 and every reporter keys on `CaseOutcome.runner` since M5, so the matrix is the CLI, the config model, the composite action, their tests and their docs. `--runner` becomes a repeatable Typer option resolved by one helper that refuses duplicates and unknown names; `Config.default_runner` accepts a string or a validated list; the action's `runner` input is split on commas by a bash helper. Nothing in `orchestrator.py`, `gating.py`, `comparison.py` or `reporters/` changes.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, bash (the action step), pytest.

**Spec:** `docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md` (§7, plus the matrix rows of §2, §8, §9, §10, §11). Part 1 (the adapter) must be merged first only for the docs examples to be runnable; the code here does not depend on it.

## Global Constraints

- Conventional Commits for every commit and the PR title.
- Exit codes are the CI contract: a duplicate or unknown runner name is a user error, exit 2, raised as `typer.BadParameter`.
- `--runner` replaces `default_runner` wholesale; it never appends.
- Every candidate `(skill, case, runner)` outcome counts toward the gate; no outcome may count twice.
- `extra="forbid"` on `Config` stays; `ConfigError` messages name the file and field.
- `tests/test_action.py::test_every_cli_backed_input_is_actually_forwarded_to_the_command` must keep passing: the new bash helper is a third accepted forwarding form.
- Docs ship with the change; `uv run mkdocs build --strict` and `uv run pytest tests/test_docs.py` pass at the end.
- `uv run ruff check . && uv run ruff format --check .` before every commit.

---

### Task 1: `Config.default_runner` accepts a list

**Files:**
- Modify: `src/skill_lens/config.py:83` (field) and the class docstring
- Modify: `tests/test_config.py` (append)
- Modify: `docs/configuration.md`

**Interfaces:**
- Produces: `Config.default_runner: str | list[str]` — a non-empty, duplicate-free list or a string. Task 2's `_resolve_runners` consumes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python


def test_default_runner_accepts_a_list(tmp_path):
    config = tmp_path / "skill-lens.toml"
    config.write_text('default_runner = ["fake", "pydantic-ai"]\n', encoding="utf-8")
    assert load_config(path=config).default_runner == ["fake", "pydantic-ai"]


def test_default_runner_still_accepts_a_string(tmp_path):
    config = tmp_path / "skill-lens.toml"
    config.write_text('default_runner = "pydantic-ai"\n', encoding="utf-8")
    assert load_config(path=config).default_runner == "pydantic-ai"


def test_an_empty_runner_list_is_a_config_error_naming_the_field(tmp_path):
    # Zero runners would mean zero cases ran, which the gate fails -- but as
    # "nothing ran", far from the cause. Catch it where the field is.
    config = tmp_path / "skill-lens.toml"
    config.write_text("default_runner = []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="default_runner") as excinfo:
        load_config(path=config)
    assert "names no runner" in str(excinfo.value)


def test_a_duplicate_runner_in_the_list_is_a_config_error(tmp_path):
    # The same (skill, case) would enter the pass rate twice.
    config = tmp_path / "skill-lens.toml"
    config.write_text('default_runner = ["fake", "fake"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="default_runner") as excinfo:
        load_config(path=config)
    assert "'fake' twice" in str(excinfo.value)
```

(`pytest`, `ConfigError` and `load_config` are already imported at the top of that file; check and add if not.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: the list test fails with a validation error (`Input should be a valid string`); the two error tests fail because no error is raised for a list at all.

- [ ] **Step 3: Change the field and add the validator**

In `src/skill_lens/config.py`:

1. Add `field_validator` to the pydantic import: `from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator`.
2. Replace `default_runner: str = "fake"` with `default_runner: str | list[str] = "fake"`.
3. Add this method to `Config`, after the field declarations:

```python
    @field_validator("default_runner")
    @classmethod
    def _runner_list_is_well_formed(cls, value: str | list[str]) -> str | list[str]:
        """A list names every runner once and names at least one.

        A duplicate is refused rather than collapsed: the same (skill, case)
        would enter the pass rate twice, weighting one framework's vote double
        under --repeat and --baseline. An empty list would run nothing, which
        the gate fails -- but as "no cases ran", far from the cause.
        """
        if isinstance(value, str):
            return value
        if not value:
            raise ValueError("names no runner; give one name or a non-empty list")
        seen: set[str] = set()
        for name in value:
            if name in seen:
                raise ValueError(f"names {name!r} twice; each runner runs every case once")
            seen.add(name)
        return value
```

4. In the class docstring, replace the first sentence's `default_runner` (`--runner`) with `default_runner` (`--runner`, a string or a list of names — every case runs through each; the flag, repeated, replaces the whole list)`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -q`
Expected: all pass (the CLI still receives a string in every existing test).

- [ ] **Step 5: Document the key**

In `docs/configuration.md`, table row for `default_runner`, change the Default cell to `` `"fake"` (a string, or a list of names) `` and add after the table's following paragraph (the one ending `matching flag.`):

```markdown
`default_runner` may be a list, in which case every case runs through each runner named and
the report shows one outcome per `(skill, case, runner)`:

```toml
default_runner = ["pydantic-ai", "langchain"]
```

An empty list, or a name given twice, is a config error (exit 2) naming the field. A
`--runner` flag on the command line — repeatable — replaces the whole list; it never appends
to it. See [Runners](runners.md) for what the matrix measures and
[Gating](gating.md) for how it is gated.
```

- [ ] **Step 6: Commit**

```bash
git add src/skill_lens/config.py tests/test_config.py docs/configuration.md
git commit -m "feat: let default_runner name more than one runner"
```

---

### Task 2: `--runner` is repeatable

**Files:**
- Modify: `src/skill_lens/cli.py` (option type; `_resolve_runners`; construction loop; plan line; `run_evals` call)
- Modify: `tests/test_cli.py` (append)
- Modify: `docs/cli.md`, `docs/gating.md`

**Interfaces:**
- Consumes: `Config.default_runner: str | list[str]` (Task 1); `_RUNNERS`.
- Produces: `cli._resolve_runners(flag: list[str] | None, configured: str | list[str]) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python


# --- the runner matrix -------------------------------------------------------

from skill_lens import cli as cli_module  # noqa: E402
from skill_lens.runners.fake import FakeRunner  # noqa: E402


class SecondFake(FakeRunner):
    """A second offline runner, so a matrix can be exercised with no key."""

    name = "fake-2"


def test_the_same_runner_twice_is_a_user_error(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake", "--runner", "fake"])
    assert result.exit_code == 2
    assert "fake given twice" in plain(result.output)


def test_an_unknown_runner_in_a_list_is_named(tmp_path):
    skill_dir = _make_skill(tmp_path)
    result = runner.invoke(app, ["run", str(skill_dir), "--runner", "fake", "--runner", "nope"])
    assert result.exit_code == 2
    assert "unknown runner: nope" in plain(result.output)


def test_a_runner_list_in_config_is_honoured(tmp_path, monkeypatch):
    # The keyed runner in the list trips preflight, proving the list reached
    # the CLI and every runner in it is constructed.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    config = tmp_path / "skill-lens.toml"
    config.write_text('default_runner = ["fake", "pydantic-ai"]\n', encoding="utf-8")
    result = runner.invoke(app, ["run", str(skill_dir), "--config", str(config)])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.output


def test_the_runner_flag_replaces_the_config_list_rather_than_appending(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    skill_dir = _make_skill(tmp_path)
    config = tmp_path / "skill-lens.toml"
    config.write_text('default_runner = ["fake", "pydantic-ai"]\n', encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(skill_dir), "--config", str(config), "--runner", "fake"]
    )
    assert result.exit_code in (0, 1)  # a gate verdict, not a preflight refusal
    assert "OPENAI_API_KEY" not in result.output


def test_every_case_runs_through_every_runner_and_each_outcome_names_its_runner(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(cli_module._RUNNERS, "fake-2", SecondFake)
    skill_dir = _make_skill(tmp_path)
    out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "run",
            str(skill_dir),
            "--runner",
            "fake",
            "--runner",
            "fake-2",
            "--json-output",
            str(out),
            "--min-pass-rate",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text(encoding="utf-8"))
    outcomes = report["outcomes"]
    assert len(outcomes) == 2  # one case x two runners
    assert sorted(o["runner"] for o in outcomes) == ["fake", "fake-2"]
    assert {o["case_name"] for o in outcomes} == {"mentions the skill"}


def test_the_run_plan_multiplies_by_the_runner_count(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-parsing")
    _make_skill(tmp_path, cases=None)
    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path),
            "--runner",
            "pydantic-ai",
            "--runner",
            "langchain",
            "--baseline",
            "none",
            "--repeat",
            "2",
        ],
    )
    assert "Plan: up to 2 arm(s) x 2 repeat(s) x 2 runner(s) x 0 case(s) = 0 runs" in plain(
        result.stdout
    )


def test_a_single_runner_plan_line_still_states_the_runner_factor(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-parsing")
    _make_skill(tmp_path, cases=None)
    result = runner.invoke(app, ["run", str(tmp_path), "--runner", "pydantic-ai"])
    assert "x 1 runner(s) x" in plain(result.stdout)
```

`json` is already imported in `tests/test_cli.py`; check, and add `import json` if not. If `--runner langchain` is not registered yet (Part 1 unmerged), use `--runner pydantic-ai --runner fake` in the plan-line test — the factor is what is asserted.

Then, in the existing `test_the_run_plan_is_a_ceiling_not_a_forecast`, change the asserted text to `"Plan: up to 2 arm(s) x 2 repeat(s) x 1 runner(s)"`, and in `test_the_run_plan_counts_only_cases_the_tag_filter_keeps` (and any other test asserting a `Plan:` line — `grep -n "Plan:" tests/test_cli.py`) update the expected text to include `x 1 runner(s)` before the case count.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k "runner or plan"`
Expected: the duplicate test fails because Typer rejects the second `--runner` (or takes the last one); the matrix test fails; the plan-line tests fail on the missing factor.

- [ ] **Step 3: Implement**

In `src/skill_lens/cli.py`:

1. Change the option to:

```python
    runner: Annotated[
        list[str] | None,
        typer.Option(
            "--runner",
            help="Runner to use; repeat the flag to run every case through more than one.",
        ),
    ] = None,
```

2. Add this helper after `_require_a_model`:

```python
def _resolve_runners(flag: list[str] | None, configured: str | list[str]) -> list[str]:
    """The runner names one invocation uses; the flag replaces the file wholesale.

    A duplicate is refused rather than de-duplicated: the same (skill, case)
    would enter the pass rate twice, weighting one framework's vote double
    under --repeat and --baseline. An unknown name is named in the message.
    """
    if flag:
        names = list(flag)
    else:
        names = [configured] if isinstance(configured, str) else list(configured)
    seen: set[str] = set()
    for name in names:
        if name not in _RUNNERS:
            raise typer.BadParameter(f"unknown runner: {name}")
        if name in seen:
            raise typer.BadParameter(f"--runner {name} given twice; each runner runs every case once")
        seen.add(name)
    return names
```

3. In `run`, replace the block from `runner_name = runner if runner is not None else settings.default_runner` down to the `else: active_runner = runner_class()` line with:

```python
        runner_names = _resolve_runners(runner, settings.default_runner)
        runner_classes = [_RUNNERS[name] for name in runner_names]
        needs_key = any(getattr(cls, "needs_api_key", False) for cls in runner_classes)
        model_name = model if model is not None else settings.model
        if needs_key:
            # Once for the whole matrix: every keyed runner shares one model.
            _require_a_model("--model", model_name)
            check_api_key(model_name, os.environ)
        active_runners = [
            cls(
                model=model_name,
                temperature=settings.temperature,
                retries=settings.retries,
                retry_backoff_seconds=settings.retry_backoff_seconds,
            )
            if getattr(cls, "needs_api_key", False)
            else cls()
            for cls in runner_classes
        ]
```

4. Replace the later `if getattr(runner_class, "needs_api_key", False):` (the one guarding the `Plan:` line) with `if needs_key:`, and replace the two `typer.echo(...)` lines of the plan with:

```python
            runners_count = len(active_runners)
            typer.echo(
                f"Plan: up to {arms} arm(s) x {resolved_repeat} repeat(s) x "
                f"{runners_count} runner(s) x {case_count} case(s) = "
                f"{arms * resolved_repeat * runners_count * case_count} runs"
            )
```

5. In the `run_evals(` call, replace `[active_runner],` with `active_runners,`.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q`
Expected: all pass. Ruff clean.

- [ ] **Step 5: Document the flag and the gate**

`docs/cli.md`: change the usage line (line 4) `[--runner <name>]` to `[--runner <name>]...` and the table row to:

```markdown
| `--runner <name>` | `fake` | `fake`, `pydantic-ai` or `langchain`; repeat the flag to run every case through more than one — see [Runners](runners.md) |
```

After the table, add:

```markdown
`--runner` may be given more than once. Every case then runs through every runner named,
each `(skill, case, runner)` is its own outcome, and the same name twice is a user error
(exit 2) — an outcome must never count twice. The flag replaces a `default_runner` list in
`skill-lens.toml`; it never appends to it. A two-runner run spends twice: the `Plan:` line
printed before the first request includes the runner factor.
```

`docs/gating.md`: after the first section's explanation of the pass rate (before `## Gating on the delta`), add:

```markdown
## More than one runner

When a run names several runners, every candidate `(skill, case, runner)` outcome counts
toward the pass rate: a case that fails under one framework fails the gate, whatever it
did under the other. `per_skill_min` is per skill across runners, and `--min-delta` is
measured over the whole matrix. There is no per-runner threshold.
```

Run: `uv run pytest tests/test_docs.py -q` — passes.

- [ ] **Step 6: Commit**

```bash
git add src/skill_lens/cli.py tests/test_cli.py docs/cli.md docs/gating.md
git commit -m "feat: run every case through more than one runner in one invocation"
```

---

### Task 3: The action's `runner` input accepts a comma-separated list

**Files:**
- Modify: `action.yml` (`runner` input description; `add_list` helper; the `--runner` line)
- Modify: `tests/test_action.py` (accept `add_list` as a forwarding form; three split tests)
- Modify: `docs/ci.md`

- [ ] **Step 1: Write the failing tests**

In `tests/test_action.py`, add `import subprocess` to the imports, then add the helper and tests at the end:

```python


def _bash_helper(script: str, name: str) -> str:
    """One function definition out of the step's script, brace to brace.

    The `run: |` block is dedented by YAML, so helpers start at column 0 and
    close with a `}` on its own line.
    """
    start = script.index(f"{name}() {{")
    end = script.index("\n}", start) + len("\n}")
    return script[start:end]


def _split_by_the_action(value: str) -> list[str]:
    step = next(s for s in _action()["runs"]["steps"] if s.get("id") == "run")
    helper = _bash_helper(step["run"], "add_list")
    probe = f'{helper}\nargs=()\nadd_list --runner "$1"\nprintf "%s\\n" "${{args[@]}}"'
    completed = subprocess.run(
        ["bash", "-c", probe, "bash", value], capture_output=True, text=True, check=True
    )
    return completed.stdout.split("\n")[:-1]


def test_a_comma_separated_runner_input_becomes_one_flag_per_entry():
    assert _split_by_the_action("pydantic-ai, langchain") == [
        "--runner",
        "pydantic-ai",
        "--runner",
        "langchain",
    ]


def test_a_single_runner_input_is_forwarded_as_before():
    assert _split_by_the_action("fake") == ["--runner", "fake"]


def test_an_empty_runner_input_adds_nothing():
    assert _split_by_the_action("") == []
```

And in `test_every_cli_backed_input_is_actually_forwarded_to_the_command`, extend the accepted forms:

```python
        assert (
            f'add --{name} "${variable}"' in script
            or f'add_flag --{name} "${variable}"' in script
            or f'add_list --{name} "${variable}"' in script
        ), f"input {name!r} reaches the step as ${variable} but is never passed to skill-lens"
```

and extend its docstring's last sentence: `A list input (`runner`) goes through `add_list`, which emits the flag once per comma-separated entry.`

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_action.py -q`
Expected: the three split tests fail with `ValueError: substring not found` (no `add_list` in the script).

- [ ] **Step 3: Change the action**

In `action.yml`:

1. The `runner` input description becomes: `Runner to use — fake, pydantic-ai or langchain. Comma-separate names to run every case through each.`
2. After the `add_flag() { ... }` helper, add:

```bash
        add_list() {
          # A comma-separated value becomes one flag per entry, so
          # `runner: pydantic-ai,langchain` reaches the CLI as
          # `--runner pydantic-ai --runner langchain`. Blank entries are skipped.
          local IFS=','
          local item
          for item in ${2:-}; do
            item="${item// /}"
            if [ -n "$item" ]; then args+=("$1" "$item"); fi
          done
        }
```

3. Replace `add --runner "$SE_RUNNER"` with `add_list --runner "$SE_RUNNER"`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_action.py tests/test_release_config.py -q`
Expected: all pass.

- [ ] **Step 5: Document the matrix in CI**

In `docs/ci.md`, in the composite action's inputs table, change the `runner` row's description to `Runner name, or a comma-separated list to run every case through each (`pydantic-ai,langchain`). Install every framework named: `install-spec: skill-lens[pydantic-ai,langchain]==…`.` Then add a section before `## Without the action`:

```markdown
## Running the matrix

One job, one report, every case through both frameworks:

```yaml
      - uses: EmadMokhtar/skill-evaluator@v0.4.0
        with:
          path: ./skills
          install-spec: "skill-lens[pydantic-ai,langchain]==0.4.0"
          runner: pydantic-ai,langchain
          model: openai:gpt-4o-mini
          markdown-output: skill-lens.md
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

A case failing under either framework fails the job, and the summary names the runner on
every failing line. A two-runner job spends twice.

The alternative is a GitHub `strategy.matrix` over `runner: [pydantic-ai, langchain]`, one
job per framework with the single-name `runner:` input. It needs no list support and shows
one check per framework in the pull request, at the cost of one summary and one JUnit
file per job rather than one for the whole matrix.
```

(Use the current version from `action.yml`'s `install-spec` default in place of `0.4.0`; `tests/test_docs.py` checks links, not versions.)

- [ ] **Step 6: Commit**

```bash
git add action.yml tests/test_action.py docs/ci.md
git commit -m "feat: accept a comma-separated runner list in the action"
```

---

### Task 4: Roadmap, invariants, architecture

**Files:**
- Modify: `docs/roadmap.md`, `CLAUDE.md`, `ARCHITECTURE.md`

- [ ] **Step 1: `docs/roadmap.md`**

Change the M8 row to `| M8 | LangChain runner and judge (`[langchain]` extra); runner matrix | shipped |`. Rename `## What M8 part 1 shipped` to `## What M8 shipped`, and append to its paragraph:

```markdown
Part 2 made the matrix a single invocation: `--runner` is repeatable, `default_runner`
accepts a list, and the action's `runner` input splits on commas. Every `(skill, case,
runner)` outcome counts toward the gate; a duplicate runner is a user error so no outcome
counts twice. Deferred: a per-runner gate threshold, a judge list, and normalising
provider-prefix spellings across frameworks.
```

- [ ] **Step 2: `CLAUDE.md`**

1. Status paragraph: after the M8 part 1 sentence add: `M8 part 2 makes `--runner` repeatable and `default_runner` a string or list, so one invocation runs every case through every named framework.`
2. Add two bullets to the invariants list, after the `--case` bullet:

```markdown
- **Every candidate `(skill, case, runner)` outcome counts toward the gate, and none counts
  twice.** A runner named twice — on the flag or in `default_runner` — is a user error (exit
  2), not de-duplicated: under `--repeat` and `--baseline` a duplicate would weight one
  framework's vote double. An empty `default_runner` list is a config error naming the field.
- **`--runner` replaces `default_runner` wholesale; it never appends.** Every other flag
  replaces its key, and an appending flag would make "only LangChain, this once" impossible
  from a repository whose file names both.
```

- [ ] **Step 3: `ARCHITECTURE.md`**

In the invariants section (near the `--case` entry, around the text `A `--case` matching nothing fails the gate`), add:

```markdown
**Every candidate `(skill, case, runner)` outcome counts toward the gate, and none counts
twice.** The orchestrator has run a `skill × case × runner` matrix since M1 and every
reporter has keyed on `CaseOutcome.runner` since M5; M8 part 2 only let the CLI and the
config name more than one runner. The one new rule is that a name given twice is refused
rather than collapsed — `cli._resolve_runners` and `Config.default_runner`'s validator both
enforce it — because under `--repeat` and `--baseline` a duplicate would weight one
framework's vote double. `--runner` replaces the configured list; it never appends.
```

- [ ] **Step 4: Build and test the docs**

Run: `uv run mkdocs build --strict && uv run pytest tests/test_docs.py tests/test_naming.py -q`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add docs/roadmap.md CLAUDE.md ARCHITECTURE.md
git commit -m "docs: describe the runner matrix and its gate"
```

---

### Task 5: Final verification and the pull request

- [ ] **Step 1: The whole gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q && uv run skill-lens list ./examples && uv audit --preview-features audit --locked
```

Expected: every command exits 0.

- [ ] **Step 2: Open the PR**

```bash
git push -u origin HEAD
gh pr create --assignee @EmadMokhtar --title "feat: run every case through more than one runner" --body "$(cat <<'BODY'
## Summary

- `--runner` is repeatable and `default_runner` accepts a list: one invocation runs every case through every named framework and produces one report.
- A runner named twice is a user error (exit 2); an empty list is a config error naming the field; the flag replaces the list, never appends.
- The action's `runner` input splits on commas (`runner: pydantic-ai,langchain`).
- Nothing in the orchestrator, reporters or gate changes — they have keyed on `CaseOutcome.runner` since M1/M5.

Spec: `docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md` §7.

## Test plan

- [ ] `uv run pytest -q`
- [ ] `uv run mkdocs build --strict`
- [ ] A matrix run from a checkout: `uv run skill-lens run examples --runner pydantic-ai --runner langchain --model openai:gpt-4o-mini` (needs a key)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```
