# skill-lens M6 Part 2 — Design

**Date:** 2026-09-12
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§3, §9 M6); Part 1 is
`2026-09-10-skill-lens-m6-design.md`, whose §16 sketched this milestone.

## 1. Scope

Part 1 gave a case a contained workspace and three file tools. Part 2 lets the agent use
the files a skill ships *with*: read the reference material bundled beside `SKILL.md`, and
run the scripts bundled there. The Agent Skills layout defines three directories for that
material — `scripts/`, `references/`, `assets/` — and the loader ignores all three today.

This milestone delivers:

- A read-only view of a skill's bundle (`bundle.py`), and `Skill.bundle_root` so the
  loader and the baseline resolver can say where one is.
- Two read tools, `list_skill_files` and `read_skill_file`, offered whenever a skill has a
  bundle. Reading needs no opt-in.
- One execution tool, `run_script`, offered only when the skill has scripts **and** the
  run turned execution on (`allow_scripts = true` / `--allow-scripts`).
- Script execution (`scripts.py`) with portable guards on every platform — a wall-clock
  timeout that kills the whole process tree, capped output with a visible cut, a rebuilt
  environment that carries no secret, a scratch directory for temporary files — and an
  OS sandbox on top where one exists: `sandbox-exec` on macOS, `bwrap` on Linux.
- A per-run preflight that resolves every interpreter the candidate scripts need and
  probes the sandbox once, before any case runs and before any money is spent.
- `--baseline previous` materialising the *previous* bundle from git, so the old
  instructions are never paired with the new scripts.
- The run report saying, on every run that enabled scripts, which sandbox was used.
- `examples/log-triage`, a skill whose eval can only pass by running its script — which
  also covers the `references/` example M7 deferred here.

### Explicitly deferred

- **An `init` scaffold case for script-bearing skills.** `init` detecting `scripts/` and
  adding a `trajectory: called: [run_script]` case is small, but it is a DX feature that
  belongs with the next `init` pass, not with the trust decision this milestone is about.
- **Standard input to scripts.** No example needs it; arguments cover every case we have.
- **A per-case timeout.** `script_timeout_seconds` is a runaway guard, set once per
  repository like the workspace caps. A case that needs longer is a slow script, not a
  different policy.
- **An `unshare`-only Linux fallback.** It would isolate the network and nothing else,
  and a third backend is a third thing to keep working in CI.
- **Denying reads outside the workspace.** The interpreter and its libraries live outside
  the workspace, and an allowlist of "what an interpreter needs" is a losing game across
  platforms. §6 states the residual risk instead of pretending to close it.
- **Binary reads through `read_skill_file`.** `assets/` may hold binary files; the tool
  returns the same "not valid UTF-8 text" message `read_file` does. A skill that needs the
  agent to *copy* a binary asset into the workspace is a case for a later `copy_asset` tool
  that no example needs yet.

## 2. Decisions

Four questions the Part 1 sketch left open, each answered before the design was written.

1. **Execution is opt-in per run, off by default.** A `SKILL.md` under evaluation is, by
   construction, code nobody has vetted, and skill-lens is built to run in CI, where the
   process may hold repository credentials. Running that code is a decision the operator of
   the run states — `allow_scripts = true` in `skill-lens.toml`, or `--allow-scripts` —
   never something the eval file can turn on, because the person who writes the eval is
   usually the person who wrote the skill. Reading the bundle needs no opt-in: reading a
   file the repository already contains changes nothing.
2. **The bundle is the three Agent Skills directories and nothing else.** `*.eval.yaml` and
   `evals/` sit beside `SKILL.md` and hold the expected answers; "any file beside
   `SKILL.md`" would hand the agent its answer key.
3. **Portable guards everywhere, an OS sandbox where one exists, and the report says which.**
   `script_sandbox = "auto"` (the default) uses the sandbox when the probe succeeds and the
   guards alone otherwise; `"required"` refuses to run at all without one; `"off"` never
   probes. The choice is visible on every run, so an operator can tell from a report
   whether the isolation they expected was applied.
4. **Interpreters come from a configurable extension map, resolved on `PATH`.** The default
   is `py → python3`, `sh → bash`. `python3` is looked up on `PATH`, not taken from
   `sys.executable`: a `uv tool install` puts skill-lens in a venv that has none of the
   skill's dependencies, and the operator's `PATH` is the one place they already control.
   A script's dependencies are the eval's problem — the CI job installs them — and the
   docs say so.

## 3. The bundle — `src/skill_lens/bundle.py`

Framework-neutral, mirrors `workspace.py` in shape and in discipline: **methods raise,
tools catch.**

```python
BUNDLE_DIRS: tuple[str, ...] = ("scripts", "references", "assets")
SCRIPTS_DIR = "scripts"

@dataclass(frozen=True)
class SkillBundle:
    root: Path   # always already resolved, like Workspace.root

    def listing(self) -> list[str]: ...
    def read(self, candidate: str) -> str: ...
    def script(self, candidate: str, interpreters: Mapping[str, Sequence[str]]) -> Path: ...
```

- `listing()` — every file under the three directories, recursive, as sorted POSIX paths
  relative to the root (`references/style.md`). A symbolic link whose resolved target lies
  outside the root is left out, so the listing never advertises a file `read` would refuse.
- `read(candidate)` — `check_relative_path` (reused from `workspace.py`) first, so the
  messages for `..`, absolute paths, null bytes and surrogates are the ones the model has
  already seen from `read_file`; then containment against the resolved root; then a rule
  of this module's own: the first path component must be one of `BUNDLE_DIRS`, refused as
  `refused: 'SKILL.md' is not under scripts/, references/ or assets/`. Returns the file's
  text as UTF-8; a missing file or a directory raises `OSError`, a binary file
  `UnicodeDecodeError`, exactly as `Workspace.read` does.
- `script(candidate, interpreters)` — the same checks, then: the first component must be
  `scripts`; the file must exist (`refused: no such script 'scripts/x.py'; bundled scripts:
  scripts/a.py, scripts/b.sh` — the listing is in the message so the model's next call can
  be right); and its extension, lower-cased and without the dot, must be a key of
  `interpreters` (`refused: 'scripts/x.rb' has no configured interpreter;
  script_interpreters allows: py, sh`). Returns the absolute path.

`PathRefused` is imported from `workspace.py` rather than duplicated: the tools already
know how to render it, and one exception type means one `except` clause per tool.

## 4. Loader changes — `skills/loader.py`

`Skill` gains `bundle_root: Path | None = None`. `parse_skill_file` sets it to the resolved
skill directory when at least one of `BUNDLE_DIRS` is a directory there, else leaves it
`None`. `parse_skill_text` (also used by the baseline resolver) does not touch it: text has
no directory.

`None` is the safe default on purpose. A `Skill` built by hand in a `FakeRunner` test has no
bundle; the `--baseline none` skill the orchestrator builds has no bundle; and a runner that
keys the bundle tools on `bundle_root` therefore cannot leak the candidate's scripts into
either. Keying on `Skill.path` would leak into both — `path` is set on every `Skill`.

## 5. Script execution — `src/skill_lens/scripts.py`

Framework-neutral. Nothing here knows about tools or agents; it turns a policy and a path
into a `ScriptResult`.

### Policy and runtime

```python
# In models.py, beside CaseStatus and the other Literal aliases -- models.py must not
# import scripts.py, because scripts.py imports workspace.py, which imports models.py.
SandboxMode = Literal["auto", "required", "off"]
SandboxBackend = Literal["sandbox-exec", "bwrap", "none"]

@dataclass(frozen=True)
class ScriptPolicy:                      # built from Config by the CLI
    sandbox: SandboxMode = "auto"
    timeout_seconds: float = 30.0
    max_output_bytes: int = 20_000
    interpreters: Mapping[str, tuple[str, ...]] = DEFAULT_INTERPRETERS

@dataclass(frozen=True)
class SandboxStatus:
    backend: SandboxBackend
    detail: str                          # "sandbox-exec probe succeeded", "bwrap not found on PATH", ...

@dataclass(frozen=True)
class ScriptRuntime:                     # what reaches a runner
    policy: ScriptPolicy
    sandbox: SandboxStatus

class ScriptSetupError(Exception): ...   # exit 2, added to cli._AUTHORING_ERRORS
```

`DEFAULT_INTERPRETERS = {"py": ("python3",), "sh": ("bash",)}`. Values are argv prefixes,
so a repository can say `py = ["python3", "-X", "utf8"]`; the script path and the model's
arguments are appended.

### Preflight — `preflight(skills, policy) -> ScriptRuntime`

Runs once per run, in `run_evals`, after discovery and before any case executes, and only
when execution is enabled. Two checks:

1. **Interpreters.** For every candidate skill with a bundle, every file under `scripts/`
   whose extension is a key of `policy.interpreters` needs `shutil.which(argv[0])` to
   succeed. A miss raises `ScriptSetupError("examples/log-triage/scripts/count_levels.py
   needs python3, which is not on PATH")`. A file under `scripts/` with an unmapped
   extension (a `data.json`, a `helper.txt`) is not an error; it is simply not runnable.
   Baseline bundles are not preflighted — they are resolved later, per case — so an
   interpreter is also looked up at call time, and a miss there is a refusal the model
   reads, not an error.
2. **Sandbox.** `probe(mode) -> SandboxStatus`:
   - `"off"` → `none`, detail `script_sandbox = "off"`. No probe.
   - macOS → `sandbox-exec` on `PATH` and `sandbox-exec -p '(version 1)(allow default)(deny
     network*)' /usr/bin/true` exits 0 → `sandbox-exec`. Otherwise `none` with the failing
     step in the detail.
   - Linux → `bwrap` on `PATH` and `bwrap --ro-bind / / --dev /dev --proc /proc
     --unshare-net --unshare-pid --die-with-parent -- /bin/true` exits 0 → `bwrap`.
     Otherwise `none` with the detail (`bwrap not found on PATH`, or the probe's first
     stderr line — on Ubuntu 24.04 that is the AppArmor refusal of unprivileged user
     namespaces, which is exactly what an operator needs to read).
   - Anything else → `none`, `no sandbox backend on <platform>`.
   - `"required"` and `none` → `ScriptSetupError("script_sandbox = \"required\" but no
     sandbox is available: <detail>")`.

Both checks are empirical on purpose: whether `bwrap` *works* on a given runner is a
question only running it answers.

### One call — `run_script(bundle, workspace, candidate, args, runtime) -> ScriptResult`

```python
@dataclass(frozen=True)
class ScriptResult:
    refused: str | None = None      # a PathRefused-style message; nothing ran
    exit_code: int | None = None    # None when timed out
    timed_out: bool = False
    stdout: str = ""                # already truncated, marker included
    stderr: str = ""
    workspace_warning: str | None = None
```

Steps, in order:

1. `bundle.script(candidate, runtime.policy.interpreters)` → absolute path, or a
   `ScriptResult(refused=...)`. `shutil.which(argv[0])` for the extension's interpreter; a
   miss → `refused: scripts/x.py needs python3, which is not on PATH`.
2. `scratch = mkdtemp(prefix="skill-lens-scratch-")`, resolved, **outside the workspace**.
   The script's temporary files go here, not into `list_files`, `file-produced` or the
   judge's artifacts. Deleted in a `finally`, `ignore_errors=True`, like
   `Workspace.cleanup`.
3. **Environment, rebuilt from an allowlist — never `os.environ` with keys removed.**
   Kept from the parent, when present: `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`;
   on Windows additionally `SystemRoot`, `COMSPEC`, `PATHEXT`, `USERPROFILE` (Python does
   not start without `SystemRoot`). Set: `TMPDIR`, `TMP`, `TEMP` = scratch;
   `PYTHONDONTWRITEBYTECODE=1` (the bundle is read-only under the sandbox and would
   otherwise fill stderr with `__pycache__` errors); `PYTHONIOENCODING=utf-8`. Everything
   else skill-lens holds — `OPENAI_API_KEY` first among them — is absent by construction.
4. argv = interpreter prefix + script path + `[str(a) for a in args]`. `shell=False`,
   always; nothing the model sends is ever joined into a command line. Under a sandbox,
   argv is wrapped (§6) with the workspace, the scratch directory, the temporary directory
   and the bundle root.
5. `cwd` = the workspace root. stdout and stderr go to two files in the scratch
   directory, opened by the harness `w+b` **before** the process starts and read back
   through those same descriptors — never re-opened by path, because the script owns
   that directory and could put a symlink or a FIFO at the path — and **not** captured
   into memory: a script printing gigabytes inside the timeout must not take the harness
   down with it.
6. `Popen(..., start_new_session=True)` on POSIX, so the child leads its own process
   group; `creationflags=CREATE_NEW_PROCESS_GROUP` on Windows. `wait(timeout)`. On
   expiry: `os.killpg(pgid, SIGKILL)` on POSIX, `taskkill /T /F /PID` on Windows, then
   reap; `timed_out=True`, `exit_code=None`. A script that spawns `sleep 1000` and exits
   must not leave the sleeper behind — the group kill is what guarantees that.
7. Read the first `max_output_bytes` of each output file, decoded as UTF-8 with
   `errors="replace"`; if the file is longer, append `... [truncated, N bytes omitted]`
   with the exact count. Same rule as judge artifacts: a cut is never silent.
8. `workspace.over_limit()` — a new public method returning a message when `max_files` or
   `max_total_bytes` is exceeded, else `None`. A script writes to disk directly, so
   `Workspace.write`'s projection cannot refuse it beforehand. The message goes on
   `workspace_warning` and the tool renders it as the last line
   (`warning: the working directory now holds 12,345,678 bytes; max_total_bytes is
   5,000,000`); every later `write_file` is already refused by the existing projection.
   The timeout is the real bound on what one script can write, and `docs/runners.md`
   says so.

**`run_script` never raises.** `OSError` or `ValueError` from `Popen` (an interpreter that
exists but will not execute; a NUL byte in a model-supplied argument) becomes
`refused: cannot start python3: <error>`, and a capture file that cannot be created is
`refused: cannot create the capture files: <error>`. Nothing about a script
belongs in `RunResult.error`: an unrunnable script is a fact about the skill's bundle,
and the model reading that fact is the eval signal.

## 6. Sandbox backends

### What the sandbox guarantees, and what it does not

With a backend active, a script **cannot**: open a network connection; write anywhere but
the workspace and its scratch directory; read anything under the system temporary
directory except the workspace, the scratch directory and the skill's own bundle (so under
`--concurrency N` a script cannot read the baseline arm's workspace or another case's
scratch). The bundle is re-allowed explicitly because a `--baseline previous` bundle is
extracted *under* the temporary directory (§12) — without that allowance the baseline
arm's own scripts would be unreadable.

A script **can** still read every other file the CI user can read — the interpreter and its
libraries live there, and §1 defers closing that. What it reads it can print, and what it
prints reaches the model and the run report. So the sandbox is defence in depth (a second
layer that limits damage), and the opt-in is the decision: **do not enable scripts for a
skill you would not run by hand.** `docs/security.md` says this in those words.

Without a backend (`none`), the portable guards of §5 still apply: no inherited secret,
a timeout, capped output, a scratch directory. The network is open and writes are bounded
only by filesystem permissions; the report shows `sandbox: none` with the reason.

### macOS — `sandbox-exec`

argv becomes `["sandbox-exec", "-p", profile, *argv]`. The profile is the style Bazel and
Chromium use: allow by default, then deny the two things that matter, then re-allow the
places the script must reach. Later rules win.

```scheme
(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "<workspace>") (subpath "<scratch>"))
(allow file-write-data (literal "/dev/null"))
(deny file-read* (subpath "<tempdir>"))
(allow file-read* (subpath "<workspace>") (subpath "<scratch>") (subpath "<bundle>"))
```

`<tempdir>` is `tempfile.gettempdir()`, resolved — on macOS that is under
`/private/var/folders/...`, where every workspace and scratch directory lives. Paths are
embedded resolved and quoted; a `"` in the temporary directory's own path cannot be
expressed safely, so the probe reports `none` with a detail naming it — fail-closed and
visible, and `required` then exits 2. A `subpath` denial, not a regex: a regex literal in
a profile is a raw string, and an escaped temp path would silently stop matching for any
path containing a regex metacharacter — a fail-open the blanket `subpath` cannot have.

`sandbox-exec` is marked deprecated in Apple's documentation and remains present and
functional on current macOS; Bazel, Chromium and Claude Code rely on it. The probe in §5
is what turns "present" into "works"; the docs note the deprecation.

### Linux — `bwrap`

```
bwrap --ro-bind / / --dev /dev --proc /proc
      --tmpfs <tempdir>
      --bind <workspace> <workspace> --bind <scratch> <scratch>
      --ro-bind <bundle> <bundle>
      --unshare-net --unshare-pid --die-with-parent --new-session
      -- <argv>
```

The whole filesystem read-only, the temp directory replaced by an empty `tmpfs` so sibling
workspaces vanish, the two writable directories bound back in, the bundle bound back
read-only (it may live under the temp directory — §12), no network namespace, and
`--die-with-parent` so a killed harness takes the script with it. `bwrap` needs
unprivileged user namespaces or a setuid install; the probe reports which is missing.

### Windows

No backend. `auto` runs with the portable guards and reports `none`; `required` refuses.

## 7. The tools — `runners/tools.py`

`build_bundle_tools(bundle, workspace, runtime) -> list[AgentTool]`:

| Tool | Schema | Registered when |
| --- | --- | --- |
| `list_skill_files` | no arguments | `skill.bundle_root` is set |
| `read_skill_file` | `path: string` | `skill.bundle_root` is set |
| `run_script` | `path: string`, `args: array of string` (optional) | the bundle has a file under `scripts/` **and** `runtime is not None` |

Descriptions name no skill: *"Read a file bundled with the loaded skill. `path` is relative
to the skill's directory, for example `references/style.md`."* and *"Run a script bundled
with the loaded skill, with the working directory as its current directory. `path` is
relative to the skill's directory, for example `scripts/count.py`; `args` are passed as
command-line arguments."*

Every callable catches, as the file tools do: `PathRefused` → its message; `ScriptResult`
→ rendered text:

```
exit code: 0            | stopped after 30.0 s (script_timeout_seconds)
stdout:
<content or (empty)>
stderr:
<content or (empty)>
warning: ...            (only when the workspace is over a cap)
```

Arguments are positional-optional and coerced (`path: Any = ""`, `args: Any = ()`), and
`args` that is not a list is wrapped as one element — a model may send the wrong shape,
and that is an eval signal, not an exception.

`BUILTIN_TOOL_NAMES` grows to six. The case loader's collision check and the trajectory
name check read it and need no change: a case tool named `run_script` is refused in any
workspace case, bundle or not — reserved is reserved, and a name that is sometimes free
would be a name nobody can rely on.

**Bundle tools require a `workspace:` block**, like the file tools: the workspace is the
script's working directory and the sandbox's only writable area. A skill with a bundle
running a case with no workspace gets no bundle tools, and a trajectory naming one in such
a case is the same authoring error the file tools already raise.

**Offered mode.** The read tools are registered in `offered` cases too. The agent that
declines the skill has no reason to call them, and an agent that triggers the skill then
needs them exactly as a `loaded` case does. `run_script` follows the same rule.

### The runner adapter — `runners/pydantic_ai.py`

`Runner.run` gains one keyword, additive with a default:

```python
def run(self, skill, case, workspace=None, scripts: ScriptRuntime | None = None) -> RunResult
```

`_build_agent` adds `build_bundle_tools(SkillBundle(skill.bundle_root), workspace, scripts)`
when `skill.bundle_root` and `workspace` are both set. `FakeRunner` accepts and ignores the
keyword; a third-party runner written against Part 1 keeps working.

**No preamble change.** `WORKSPACE_PREAMBLE` stays byte-identical in both arms. The bundle
tools describe themselves, and "run `scripts/count.py`" comes from `SKILL.md` — which is the
thing under measurement.

## 8. Model changes — all additive

```python
class Skill(BaseModel):
    ...
    bundle_root: Path | None = None

class ScriptStatus(BaseModel):
    sandbox: SandboxBackend
    detail: str

class ScriptNote(BaseModel):
    skill_name: str
    script_count: int

class RunReport(BaseModel):
    ...
    scripts: ScriptStatus | None = None            # None: execution was off
    script_notes: list[ScriptNote] = Field(default_factory=list)
```

`ScriptStatus` and `ScriptNote` live in `models.py` like every other data shape;
`scripts.py`'s frozen dataclasses (`ScriptPolicy`, `SandboxStatus`, `ScriptRuntime`,
`ScriptResult`) are runtime values that never reach a report, the way `WorkspaceLimits` is.
`RunResult` is unchanged: script output lives in the transcript, where the failure context
already finds tool calls.

## 9. Loader validation — `cases/loader.py`

Nothing new to validate: the six-name `BUILTIN_TOOL_NAMES` flows through the existing
collision and trajectory checks. The one message that changes is the trajectory hint
*"Built-in file tools only exist in a case with a 'workspace:' block"*, which becomes
*"Built-in workspace and bundle tools only exist in a case with a 'workspace:' block"*.

## 10. Config & CLI

```toml
# skill-lens.toml
allow_scripts = false            # run scripts bundled under scripts/; --allow-scripts overrides
script_sandbox = "auto"          # "auto": OS sandbox when available; "required"; "off"
script_timeout_seconds = 30.0    # wall clock, per call; the process tree is killed at expiry
max_script_output_bytes = 20000  # per stream; anything beyond is cut with a visible marker

[script_interpreters]            # file extension -> argv prefix; anything else is refused
py = ["python3"]
sh = ["bash"]
```

`Config` fields: `allow_scripts: bool = False`, `script_sandbox: SandboxMode = "auto"`,
`script_timeout_seconds: float = Field(30.0, gt=0)`, `max_script_output_bytes: int =
Field(20_000, gt=0)`, `script_interpreters: dict[str, list[str]] = {"py": ["python3"],
"sh": ["bash"]}`, each `list[str]` validated non-empty, and each key normalised to
lower case with any leading dot removed (`".PY"` and `"py"` are the same key), so the
lower-cased extension `SkillBundle.script` looks up always matches what the author wrote. `20_000` equals
`MAX_ARTIFACT_BYTES` — one number for "how much untrusted output reaches a model".

Only `allow_scripts` gets a flag: `--allow-scripts` / `--no-allow-scripts`, three-state like
`keep_workspace`, so a committed `allow_scripts = true` can be overridden off for one run.
The rest is repository policy with no per-run reason to vary, so it is config-only with
validation on the model — the same reasoning as the three workspace caps.

A config that sets a `script_*` key while `allow_scripts` is false is accepted, not
warned about: it is the normal state of a repository that turns execution on only in a
particular CI job.

## 11. Orchestrator, options, lifetime and concurrency

Part 1 threaded `keep_workspace` and `limits` as two loose parameters and said a bundle
could come when there was a third thing. There is now a third:

```python
@dataclass(frozen=True)
class RunOptions:
    keep_workspace: bool = False
    limits: WorkspaceLimits = DEFAULT_LIMITS
    scripts: ScriptPolicy | None = None     # None: execution off
```

`run_evals(..., options: RunOptions | None = None)`; `None` means defaults, so every
existing caller and test is unchanged. `_execute`, `_run_item` and `_run_one` take
`options` and a `runtime: ScriptRuntime | None` in place of the two loose parameters.

In `run_evals`, after discovery and before `_execute`:

- `options.scripts is None` → `runtime = None`; for every discovered skill whose bundle has
  scripts, append `ScriptNote(skill_name, count)` to the report. That is the "N scripts
  found, execution is off" notice, in the report so reporters only render.
- otherwise → `runtime = preflight(skills, options.scripts)`; `ScriptSetupError`
  propagates like every other authoring-class error, and `RunReport.scripts =
  ScriptStatus(runtime.sandbox.backend, runtime.sandbox.detail)`.

`_run_one` passes `scripts=runtime` to `runner.run`. Scripts run inside the runner's tool
call, on the work item's thread; the scratch directory is per call and the workspace per
work item, so nothing here is shared across the executor. `ScriptRuntime` is frozen and
holds no mutable state, keeping the concurrency rule.

## 12. `--baseline previous` with a bundle — `skills/baseline.py`

`resolve_previous` finds the commit today and reads only `SKILL.md`. Pairing the old
instructions with the *current* scripts would measure a mismatch nobody wrote, so the
previous bundle is materialised too:

1. `git archive --format=tar <sha> -- .` with `cwd` = the skill directory. Verified: this
   yields the subtree with paths relative to the skill directory (`SKILL.md`,
   `scripts/x.py`), whereas `<sha>:./` yields an empty archive.
2. `tarfile` over the bytes; extract only members whose first path component is in
   `BUNDLE_DIRS`, with `filter="data"` (safe extraction: no absolute paths, no `..`, no
   links escaping the target; present since 3.11.4, below what the lockfile resolves),
   into `mkdtemp(prefix="skill-lens-baseline-<name>-")`.
3. `bundle_root` = that directory if anything was extracted, else `None` and the directory
   is removed immediately — a commit with no bundle gets a baseline with no bundle tools,
   never the candidate's.
4. Any failure — `git archive` non-zero, a tar that will not parse, a member the filter
   rejects, an `OSError` writing — is `BaselineUnavailable` with the reason, the module's
   existing discipline. A half-extracted directory is removed before returning.

`resolve_previous(skill, *, into: Path)` gains one keyword: the directory the orchestrator
owns for this run's baseline bundles, made once per run with
`mkdtemp(prefix="skill-lens-baselines-")` and created lazily on the first `previous`
resolution. Each skill's bundle is extracted into `into / sanitise_label(skill.name)` and
that path becomes `bundle_root`. One directory to remove, in a `finally` in `run_evals`,
after every case has run, however the run ended. `--keep-workspace` does not keep it: a
baseline bundle is an input, not an output, and the commit it came from is in the report
already. The return type is unchanged (`Skill | BaselineUnavailable`), so
`_baseline_skill` and its tests need no reshaping.

## 13. Reporters

- **JSON:** `scripts` and `script_notes` serialise as-is.
- **Console:** when `report.scripts` is set, one line at the top of the run — `scripts: on,
  sandbox: bwrap` or `scripts: on, sandbox: none (bwrap not found on PATH)`. Each
  `ScriptNote` renders as `skill log-triage bundles 1 script; execution is off
  (allow_scripts = true or --allow-scripts)`, next to where kept workspaces are printed.
- **Markdown:** the same two, under the verdict, in an optional block dropped first under
  truncation like every other optional block.
- **JUnit:** a `<property name="skill-lens.scripts.sandbox" value="bwrap"/>` on the suite
  when scripts were on; properties are where JUnit puts run-level facts, and a testcase
  is the wrong place for something true of the whole run.
- **Failure context:** unchanged. A non-passing case already shows its tool calls, so a
  `run_script` call shows its `path` and `args`; the rendered output is in the transcript.

## 14. Testing

All in the zero-cost tier unless noted; the pipeline stays offline and deterministic.
Tests that need a script use `sys.executable` injected as the `py` interpreter, so no test
depends on a `python3` on `PATH`.

**`tests/test_bundle.py`** — `listing` covers the three directories and nothing else;
`read` refuses `..`, absolute paths, `SKILL.md`, `x.eval.yaml`, `evals/x.yaml`, a symlink
out of the root; a symlinked-out file is absent from `listing`; `script` refuses a path
outside `scripts/`, a missing script (message lists the bundled scripts), an unmapped
extension (message lists the allowed ones), and accepts `scripts/nested/x.py`.

**`tests/test_skill_loader.py`** — `bundle_root` is set when any of the three directories
exists, `None` otherwise, `None` from `parse_skill_text`.

**`tests/test_scripts.py`** — exit code and stdout/stderr round-trip; `args` reach
`sys.argv`; `cwd` is the workspace; `TMPDIR` is inside a scratch directory that is gone
afterwards; a planted `OPENAI_API_KEY` in `os.environ` is absent from a script that prints
its environment; a script that starts a sleeping grandchild and exits leaves nothing
behind, and one that itself sleeps past the timeout is reported `timed_out` and its
group is gone; output beyond `max_output_bytes` is cut with the exact omitted count; a
script that writes past `max_total_bytes` yields `workspace_warning`; an interpreter
that will not start is `refused`, never raised. Sandbox builders: the macOS profile and
the `bwrap` argv are asserted as strings, including quoting of a path with a space.
`preflight`: a missing interpreter and `required` without a backend raise
`ScriptSetupError`; `auto` without one returns `none` with a detail; `off` never probes
(asserted by monkeypatching `shutil.which` to fail).

**`tests/test_sandbox_live.py`** — `skipif` the backend probe fails on this machine. With
the real backend: a script that connects to `127.0.0.1:9` fails; a write to
`tmp_path / "outside"` fails while a write into the workspace succeeds; a read of a
sibling `skill-lens-` directory fails. These run on the developer's macOS and on a Linux
runner that has `bwrap`, and skip elsewhere, which is why the string-level builder tests
exist too.

**`tests/test_builtin_tools.py`** — the three tools never raise for a wrong-shaped
argument; `run_script` is absent without a runtime and present with one; descriptions
name no skill.

**`tests/test_baseline_resolution.py`** — a two-commit fixture where `scripts/` changed:
the resolved bundle matches the older commit; a commit with no bundle yields
`bundle_root=None`; the directory is deleted after `run_evals` returns, including when a
case errors.

**`tests/test_orchestrator.py`** — `RunOptions()` reproduces today's behaviour; a
`ScriptNote` per script-bearing skill when execution is off; `ScriptSetupError` aborts
before any runner call (a `FakeRunner` that records calls sees none).

**`tests/test_config.py`, `test_cli.py`** — new keys validate; `--allow-scripts` /
`--no-allow-scripts` override the file; `script_interpreters` rejects an empty argv.

**`tests/test_case_loader.py`** — a case tool named `run_script` in a workspace case is
refused; `trajectory: called: [run_script]` is accepted with a workspace and refused
without.

**Reporters** — one test each for the console line, the markdown block and its
truncation order, the JUnit property, and the JSON fields.

**`tests/test_framework_isolation.py`** — passes unchanged; `bundle.py` and `scripts.py`
import no framework.

**Cassette tier** — one recorded case against `examples/log-triage` in which the model
calls `run_script`. Replay runs the script locally, so the recording stays deterministic;
the cassette test is `skipif` no `python3` is on `PATH`.

## 15. Example — `examples/log-triage`

```
examples/log-triage/
  SKILL.md                     # "run scripts/count_levels.py, then write triage.md per references/report-format.md"
  scripts/count_levels.py      # stdlib only: reads app.log, prints one "LEVEL: n" line per level
  references/report-format.md  # the exact layout triage.md must follow
  log-triage.eval.yaml
```

The case seeds `app.log` with a few dozen lines, asserts `file-produced: triage.md`,
`contains` (file: `triage.md`) the exact counts, and `trajectory: called: [run_script]`.
The counts are what make the script necessary: a model that guesses gets them wrong.
`examples/skill-lens.toml` gains the new keys, annotated, with `allow_scripts = true` and a
comment saying why it is on here and off by default. `uv run skill-lens list ./examples`
keeps passing as the CI self-check; `tests/test_examples.py` runs the case through
`FakeRunner`.

## 16. Documentation

| Change | Page |
| --- | --- |
| `--allow-scripts` | `docs/cli.md` |
| The five config keys | `docs/configuration.md` |
| Bundle tools, `run_script`'s output format, the sandbox and its guarantees, the timeout as the real bound on writes | `docs/runners.md` |
| Six built-in names in `trajectory`, bundle tools need a workspace | `docs/eval-files.md` |
| `scripts` and `script_notes` in the JSON report | `docs/gating.md` |
| The trust model: opt-in, what the sandbox does and does not do, the residual read risk, the deprecation of `sandbox-exec` | `docs/security.md` |
| `bundle.py`, `scripts.py`, `RunOptions`, the new invariants | `ARCHITECTURE.md` and the `CLAUDE.md` list |
| M6 part 2 shipped; the `references/` example M7 deferred | `docs/roadmap.md` |
| A pointer from §16 of the Part 1 spec to this one | `docs/superpowers/specs/2026-09-10-skill-lens-m6-design.md` |

## 17. Invariants this milestone adds

1. **Script execution is off unless the run turned it on.** `allow_scripts` /
   `--allow-scripts` is the only switch; nothing in an eval file or a `SKILL.md` can
   enable it.
2. **The bundle is `scripts/`, `references/`, `assets/` and nothing else.** An eval file
   beside `SKILL.md` is never readable by the agent.
3. **`Skill.bundle_root` defaults to `None`, and only the loader and the baseline resolver
   set it.** The `--baseline none` skill has no bundle, so the candidate's scripts never
   reach the "no skill" arm.
4. **A script's environment is built from an allowlist, never inherited.** The key that
   pays for the run is not in it.
5. **A script runs with `shell=False`, its arguments as argv, always.**
6. **A timeout kills the process group, not just the child.**
7. **Script output is read from files, capped, and a cut is never silent.**
8. **`run_script` never raises**, and an unrunnable script is never `RunResult.error`.
9. **The sandbox decision is made once per run and appears on the report.** `required`
   without a backend, and a missing interpreter, abort before any case runs.
10. **A previous baseline carries its own bundle**, materialised from the same commit as
    its `SKILL.md`, or none if that commit had none — never the candidate's.
11. **Baseline bundle directories are deleted when the run ends, however it ends.**
12. **Bundle tools require a `workspace:` block**, and their names are reserved in every
    workspace case.
13. **The workspace preamble is unchanged** — still byte-identical in both arms, still
    naming no skill.
