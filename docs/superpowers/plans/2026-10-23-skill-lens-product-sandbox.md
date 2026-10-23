# Product sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run every product runner and the product judge under a once-per-run
`product_sandbox` decision — home isolation, plus `sandbox-exec`/`bwrap` where it works — and
state exactly what applied on every report.

**Architecture:** A new module, `src/skill_lens/runners/product_sandbox.py`, owns the
decision (`ProductSandbox`), the built environment and the per-work-item temporary home
(`isolate` → `Isolation`). `runners/product.py` and `judges/product.py` take one
`ProductSandbox` at construction and start every product process through an `Isolation`. The
CLI makes the decision once and hands it to every product it builds. The product profile and
`bwrap` argv are built in `scripts.py` beside the script ones, from the same parts.

**Tech Stack:** Python 3.11+, Pydantic, Typer, pytest; `sandbox-exec` (macOS), `bwrap` (Linux).

**Spec:** `docs/superpowers/specs/2026-10-23-skill-lens-product-sandbox-design.md`

## Global Constraints

- Default `product_sandbox = "auto"`. Modes `auto | required | off` (`SandboxMode`).
- `off` is today's behaviour byte for byte: `os.environ.copy()`, no wrapper, trust note
  `TRUST_NOTE` unchanged.
- `required` needs a real backend; home isolation alone never satisfies it.
- Library defaults: `ProductRunner(product, *, sandbox=PRODUCT_SANDBOX_OFF)`,
  `ProductJudge(product, *, sandbox=PRODUCT_SANDBOX_OFF)` — earlier callers run unchanged.
- Temporary home prefix `skill-lens-home-`; layout `<root>/home`, `<root>/tmp`.
- `pass_env` holds names only, matching `^[A-Za-z_][A-Za-z0-9_]*$`; the controlled names are
  `HOME USERPROFILE APPDATA LOCALAPPDATA TMPDIR TMP TEMP XDG_CONFIG_HOME XDG_CACHE_HOME
  XDG_DATA_HOME XDG_STATE_HOME CLAUDE_CONFIG_DIR COPILOT_HOME`.
- `ProductRunner.run` and `ProductJudge.judge` never raise.
- Every test is offline and costs nothing; live sandbox tests skip where no backend works,
  except under `SKILL_LENS_REQUIRE_SANDBOX`.
- Keep `runners/product.py` edits small (siblings #96 and PR #115 edit it): no change to
  `PRESETS` entries.
- Docs ship in the task whose deliverable needs them; invariant headings are byte-identical
  in `docs/invariants.md` and `CLAUDE.md`.
- Commits: Conventional Commits, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; commit through `uv run git
  commit` (the hook needs the venv).

## Review Focus

- An auth variable that is set but empty (`CLAUDE_CODE_OAUTH_TOKEN=""`) must not satisfy the
  check — a person exporting an empty token expects exit 2, not errored cases (Task 4).
- `--keep-workspace` keeps the workspace but never the temporary home (Task 3).
- A product that times out or is killed still leaves no temporary home behind (Task 3).
- A product that starts outside the sandbox but not inside it is exit 2 naming
  `product_sandbox = "off"`, never a silent fallback (Task 4).
- The same product as runner and judge lists once, with equal `sandbox` fields (Task 5).

---

### Task 1: The decision and the trust notes

**Files:**
- Create: `src/skill_lens/runners/product_sandbox.py`
- Modify: `src/skill_lens/scripts.py` (`probe_sandbox`), `src/skill_lens/models.py` (new
  `ProductSandboxStatus`), `src/skill_lens/runners/product.py` (move `ProductSetupError` and
  `TRUST_NOTE` out, re-import them)
- Test: `tests/test_product_sandbox.py`

**Interfaces:**
- Produces:
  - `models.ProductSandboxStatus(BaseModel, extra="forbid")`: `mode: SandboxMode`,
    `backend: SandboxBackend`, `home_isolated: bool`, `detail: str`.
  - `product_sandbox.ProductSetupError` (moved verbatim; `runners.product` re-imports it, so
    `from skill_lens.runners.product import ProductSetupError` keeps working).
  - `product_sandbox.TRUST_NOTE` (moved verbatim; re-imported by `runners.product`).
  - `@dataclass(frozen=True) class ProductSandbox`: `mode`, `backend`, `detail`;
    `home_isolated -> bool` (property, `mode != "off"`); `trust() -> str`;
    `status() -> ProductSandboxStatus`.
  - `PRODUCT_SANDBOX_OFF = ProductSandbox("off", "none", 'product_sandbox = "off"')`.
  - `decide_product_sandbox(mode: SandboxMode, *, system: str | None = None,
    which=shutil.which, run=subprocess.run) -> ProductSandbox`; raises `ProductSetupError`.
  - `scripts.probe_sandbox(mode, *, system=None, which=..., run=..., product: bool = False)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_product_sandbox.py`:
  - `test_off_probes_nothing`: `run` that raises if called; result `== PRODUCT_SANDBOX_OFF`.
  - `test_auto_on_macos_uses_sandbox_exec_without_a_network_denial`: fake `which`/`run`
    recording argv; backend `"sandbox-exec"`; the probe profile contains
    `(deny file-write*)` and not `network`.
  - `test_auto_on_linux_uses_bwrap_without_unshare_net`: backend `"bwrap"`; probe argv has no
    `--unshare-net`.
  - `test_auto_with_no_backend_keeps_home_isolation`: `system="Windows"` → backend `"none"`,
    `home_isolated is True`, detail `"no sandbox backend on Windows"`.
  - `test_required_with_no_backend_raises`: `ProductSetupError` whose message starts
    `product_sandbox = "required" but no sandbox is available:` and ends with the detail.
  - `test_a_failed_probe_reads_as_none` (bwrap exits 1 with stderr) → backend `"none"`,
    detail `"bwrap probe failed: <first stderr line>"`.
  - `test_the_three_trust_notes` — exact strings:
    - off: `TRUST_NOTE`;
    - auto/none with detail `"bwrap not found on PATH"`:
      `"runs with permission prompts disabled, a temporary home directory and only an
      allowlisted environment; that is not a sandbox: no skill-lens sandbox applies (bwrap
      not found on PATH), and the product can read and write anything you can; bundled
      scripts are reachable through the product's own tools"`;
    - sandbox-exec: `"runs with permission prompts disabled, a temporary home directory and
      only an allowlisted environment, under sandbox-exec: it cannot change files outside
      its working directory and its own temporary directories, or read other skill-lens
      temporary directories; it can read anything else you can, and reach the network;
      bundled scripts are reachable through the product's own tools"`.
  - `test_the_home_only_note_never_says_sandboxed`: the home-only note contains
    `"that is not a sandbox"` and not `"under "`.
  - `test_status_mirrors_the_decision`.
  - In `tests/test_scripts.py`: `test_the_script_probe_is_unchanged` — `probe_sandbox("auto")`
    without `product=True` still sends `(deny network*)` / `--unshare-net`.

- [ ] **Step 2: Run** `uv run pytest tests/test_product_sandbox.py tests/test_scripts.py -q` —
  expect import errors / failures.

- [ ] **Step 3: Implement.** `probe_sandbox(..., product=True)`: macOS probe profile
  `"(version 1)(allow default)(deny file-write*)"`, Linux probe argv as `_bwrap_probe_argv`
  minus `--unshare-net`, off detail `'product_sandbox = "off"'`. Keep the `"` temp-path check
  for both. `decide_product_sandbox` calls it and raises on `required` + `"none"`.

- [ ] **Step 4: Run** the same command; all pass. Then `uv run pytest -q` — nothing else
  broke from moving `ProductSetupError`/`TRUST_NOTE`.

- [ ] **Step 5: Commit** `feat: decide a product sandbox once per run`.

---

### Task 2: The built environment and the product OS wrappers

**Files:**
- Modify: `src/skill_lens/runners/product_sandbox.py`, `src/skill_lens/scripts.py`,
  `src/skill_lens/runners/product.py` (two `Product` fields only)
- Test: `tests/test_product_sandbox.py`, `tests/test_scripts.py`

**Interfaces:**
- Consumes: `ProductSandbox` (Task 1).
- Produces:
  - `Product` gains, at the end: `pass_env: tuple[str, ...] | None = None` (from
    `[runners.<name>] pass_env`, `None` = not set) and `wrapper: bool = False` (True when a
    preset's `command` names another executable).
  - `CONTROLLED_ENV: frozenset[str]` (the 13 names in Global Constraints).
  - `KEPT_ENV`, `KEPT_ENV_WINDOWS` (the spec's table: `PATH LANG LC_ALL LC_CTYPE TZ SHELL
    USER LOGNAME`, `HTTPS_PROXY HTTP_PROXY NO_PROXY ALL_PROXY` and lower-case forms,
    `SSL_CERT_FILE SSL_CERT_DIR NODE_EXTRA_CA_CERTS`; Windows `SystemRoot COMSPEC PATHEXT`).
  - `@dataclass(frozen=True) class ProductEnv`: `auth: tuple[str, ...]`, `passed:
    tuple[str, ...] = ()`, `fixed: tuple[tuple[str, str], ...] = ()`.
  - `PRODUCT_ENV = {"claude-code": ProductEnv(auth=("CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"), passed=("ANTHROPIC_BASE_URL",),
    fixed=(("DISABLE_AUTOUPDATER", "1"),)), "copilot": ProductEnv(auth=("COPILOT_GITHUB_TOKEN",
    "GH_TOKEN", "GITHUB_TOKEN"))}` — keyed by product name; `cli` has no entry.
  - `product_environment(product: Product, root: Path, *, parent: Mapping[str, str] | None =
    None, windows: bool | None = None) -> dict[str, str]`.
  - `scripts.product_macos_profile(writable: Sequence[Path], tempdir: Path) -> str` and
    `scripts.product_bwrap_argv(writable: Sequence[Path], tempdir: Path, argv:
    Sequence[str]) -> list[str]`.
  - `ProductSandbox.wrap(argv: Sequence[str], writable: Sequence[Path]) -> list[str]`
    (unchanged argv for `"none"`).

- [ ] **Step 1: Write the failing tests:**
  - `test_the_environment_is_built_not_inherited`: parent `{PATH, OPENAI_API_KEY, LANG,
    CLAUDE_CONFIG_DIR}` → no `OPENAI_API_KEY`, no `CLAUDE_CONFIG_DIR`, `PATH`/`LANG` kept,
    `HOME == root/"home"`, `TMPDIR == TMP == TEMP == root/"tmp"`, `XDG_CACHE_HOME ==
    root/"home/.cache"` (and the other three `XDG_*`).
  - `test_the_presets_pass_their_own_variables`: claude-code gets the three auth names,
    `ANTHROPIC_BASE_URL`, `DISABLE_AUTOUPDATER == "1"`; copilot the three GitHub names and no
    `DISABLE_AUTOUPDATER`; `cli` none of them.
  - `test_pass_env_names_pass_when_set` (an unset name is simply absent).
  - `test_a_wrapper_keeps_the_preset_variables` (`wrapper=True` still passes them).
  - `test_windows_points_userprofile_and_appdata_into_the_home` (`windows=True`).
  - `test_the_product_profile_allows_the_network_and_confines_writes`: no `network` token;
    `(deny file-write*)`; one `(allow file-write* …)` with exactly the given paths; the
    temp-dir read denial and the parent metadata literals as in `macos_profile`.
  - `test_the_product_bwrap_argv_shares_the_network`: no `--unshare-net`; `--bind p p` per
    writable path after `--tmpfs tempdir`; `--unshare-pid --die-with-parent --new-session --`
    before argv.
  - `test_wrap_under_none_is_the_identity`.
  - Existing `macos_profile` / `bwrap_argv` tests stay green unchanged.

- [ ] **Step 2: Run** `uv run pytest tests/test_product_sandbox.py tests/test_scripts.py -q` —
  fail.

- [ ] **Step 3: Implement.** Extract the parent-metadata computation from `macos_profile`
  into a helper both profiles use; `macos_profile` output stays byte-identical. Order in the
  product profile: `(version 1)`, `(allow default)`, `(deny file-write*)`, the write allow,
  `(allow file-write-data (literal "/dev/null"))`, the temp-dir read deny, the read allow, the
  metadata literals.

- [ ] **Step 4: Run** the same command; pass.

- [ ] **Step 5: Commit** `feat: build a product's environment and sandbox profile`.

---

### Task 3: Run a product inside an isolation

**Files:**
- Modify: `src/skill_lens/runners/product_sandbox.py`, `src/skill_lens/runners/product.py`
  (`invoke`, `ProductRunner.__init__`, `ProductRunner.run`)
- Test: `tests/test_product_runner.py` (new section `# --- product sandbox ---`)

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `@dataclass(frozen=True) class Isolation`: `sandbox: ProductSandbox`, `root: Path |
    None`, `env: dict[str, str]`; `launch(argv: Sequence[str], cwd: Path, writable:
    Sequence[Path] = ()) -> list[str]` — wraps with writable `(cwd, root, *writable)`.
  - `@contextmanager isolate(sandbox: ProductSandbox, product: Product) -> Iterator[Isolation]`
    — under `off`: `root=None`, `env=os.environ.copy()`, no wrap; otherwise `mkdtemp(prefix=
    "skill-lens-home-")` resolved, `home/` and `tmp/` made, removed in a `finally`.
  - `invoke(product, prompt, cwd, extra_args=(), isolation: Isolation | None = None,
    writable: Sequence[Path] = ()) -> Invocation` (`None` = today's behaviour).
  - `ProductRunner(product, *, sandbox: ProductSandbox = PRODUCT_SANDBOX_OFF)`; `.sandbox`
    property.

- [ ] **Step 1: Write the failing tests** (fake product; its `FAKE_PRODUCT_*` variables reach
  it through `pass_env`). Extend `tests/fake_product.py` to record `os.environ.get("HOME")`,
  `TMPDIR` and a sorted list of environment keys in the record.
  - `test_home_isolation_gives_the_product_a_temporary_home`: sandbox
    `ProductSandbox("auto", "none", "test")`; recorded `HOME` ends with `/home` and its parent
    name starts with `skill-lens-home-`; `OPENAI_API_KEY` (set with monkeypatch) absent.
  - `test_the_temporary_home_is_removed_after_the_run`, `..._after_a_timeout` (mode
    `sleep`, short timeout) and `..._under_keep_workspace` (workspace kept, home gone).
  - `test_two_work_items_get_two_homes` (two runs, two different recorded `HOME`s).
  - `test_off_passes_the_whole_environment` (default constructor: `OPENAI_API_KEY` present,
    `HOME` is the real one).
  - `test_the_bridge_reaches_the_product_under_isolation`: a `tools:` case under
    `ProductSandbox("auto", "none", "test")` still connects (`bridge.connected()`), i.e. no
    `RunResult.error`.
  - `test_launch_passes_cwd_root_and_bridge_as_writable`: `ProductSandbox("auto",
    "sandbox-exec", ...)`, monkeypatched `scripts.product_macos_profile` capturing
    `writable`.

- [ ] **Step 2: Run** `uv run pytest tests/test_product_runner.py -q` — fail.

- [ ] **Step 3: Implement.** In `run`, enter `isolate(...)` in the existing `try` (an
  `OSError` from `mkdtemp` is already a `RunResult.error`), pass the bridge directory as
  `writable`. `invoke` uses `isolation.env` and `isolation.launch(argv, cwd, writable)` when
  given.

- [ ] **Step 4: Run** the file, then `uv run pytest -q`; pass.

- [ ] **Step 5: Commit** `feat: run a product in a temporary home under the sandbox`.

---

### Task 4: Preflight under the decision

**Files:**
- Modify: `src/skill_lens/runners/product.py` (`probe_version`, `ProductRunner.preflight`,
  `_check_bridge`), `src/skill_lens/runners/mcp.py` (`probe_bridge`),
  `src/skill_lens/runners/product_sandbox.py`
- Test: `tests/test_product_preflight.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `environment_refusal(product: Product, sandbox: ProductSandbox, role: str, *, parent:
    Mapping[str, str] | None = None) -> str | None` — the message, or None.
  - `probe_version(product, executable, role="runner", isolation: Isolation | None = None)`.
  - `probe_bridge(isolation: Isolation | None = None) -> None`.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_preset_with_no_auth_variable_is_refused`: isolated sandbox, parent env without
    the three names → `ProductSetupError`, message exactly the spec's (`runner claude-code:
    product_sandbox = "auto" gives the product a temporary home directory, so it cannot use
    a login stored in your home directory or keychain; export CLAUDE_CODE_OAUTH_TOKEN (from
    `claude setup-token`) or ANTHROPIC_API_KEY, name the variable your setup uses in
    [runners.claude-code] pass_env, or set product_sandbox = "off"`); copilot names
    `COPILOT_GITHUB_TOKEN`.
  - `test_an_empty_auth_variable_does_not_count`.
  - `test_a_pass_env_variable_satisfies_the_check` (Bedrock-style name set).
  - `test_a_wrapper_is_not_auth_checked`.
  - `test_cli_needs_pass_env_under_isolation`: `pass_env=None` → refused naming
    `[runners.cli] pass_env` and `product_sandbox = "off"`; `pass_env=()` passes.
  - `test_off_checks_no_environment`.
  - `test_the_version_probe_runs_in_the_temporary_home`: fake `--version` mode records
    `HOME` (extend the fake) → under `/home` of a `skill-lens-home-` root.
  - `test_a_product_that_fails_inside_the_sandbox_names_off`: `FAKE_PRODUCT_VERSION_FAILS`
    under a sandboxed decision → message contains `product_sandbox = "off"` and the backend.
  - `test_the_status_carries_the_sandbox`: `status.sandbox == sandbox.status()`,
    `status.trust == sandbox.trust()`.
  - `test_the_bridge_probe_runs_under_the_isolation` (monkeypatched `probe_bridge` receives an
    `Isolation`).

- [ ] **Step 2: Run** `uv run pytest tests/test_product_preflight.py -q` — fail.

- [ ] **Step 3: Implement.** Order in `preflight`: environment refusal, `find_executable`,
  then one `isolate(...)` block for the version probe and (when bridged) the bridge probe.
  The version-probe failure message under a backend appends `; under <backend> -- set
  product_sandbox = "off" to run it without one`.

- [ ] **Step 4: Run** the file and `uv run pytest -q`; pass.

- [ ] **Step 5: Commit** `feat: prove a product starts under its sandbox in preflight`.

---

### Task 5: The product judge, and the report

**Files:**
- Modify: `src/skill_lens/judges/product.py`, `src/skill_lens/models.py` (`ProductStatus.
  sandbox: ProductSandboxStatus | None = None`), `docs/gating.md` (`products[].sandbox`)
- Test: `tests/test_product_judge.py`, `tests/test_reporters.py`,
  `tests/test_markdown_reporter.py`, `tests/test_junit_reporter.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `ProductJudge(product, *, sandbox: ProductSandbox = PRODUCT_SANDBOX_OFF)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_judge_runs_in_a_temporary_home` and `..._is_removed_afterwards`.
  - `test_the_judge_preflight_applies_the_environment_check`.
  - `test_runner_and_judge_of_one_product_list_once` (`run_evals` with the same sandbox →
    one `products` entry with `sandbox` set).
  - Reporters: each of the three notes appears in console, Markdown and the JUnit
    `skill-lens.products` property; the JSON `products[0]["sandbox"]` is
    `{"mode", "backend", "home_isolated", "detail"}`.

- [ ] **Step 2: Run** those files — fail.

- [ ] **Step 3: Implement** `judge` inside `isolate(...)`, preflight as Task 4's; document
  the `sandbox` object in `docs/gating.md`'s JSON section.

- [ ] **Step 4: Run** the files, `uv run pytest tests/test_docs.py -q`; pass.

- [ ] **Step 5: Commit** `feat: judge with a product under the same sandbox`.

---

### Task 6: Config, flag and action input

**Files:**
- Modify: `src/skill_lens/config.py`, `src/skill_lens/cli.py`, `action.yml`,
  `docs/configuration.md`, `docs/cli.md`, `docs/ci.md`
- Test: `tests/test_config.py`, `tests/test_cli.py`, `tests/test_action.py`

**Interfaces:**
- Consumes: `decide_product_sandbox`, `PRODUCT_SANDBOX_OFF`, `CONTROLLED_ENV`.
- Produces: `Config.product_sandbox: SandboxMode = "auto"`; `ProductSettings.pass_env:
  list[str] | None = None`; `Config.product(name)` sets `Product.pass_env` (tuple or None)
  and `Product.wrapper`; `skill-lens run --product-sandbox auto|required|off`; action input
  `product-sandbox`.

- [ ] **Step 1: Write the failing tests:**
  - Config: `product_sandbox` defaults to `"auto"` and rejects `"on"`; `pass_env` rejects
    `"1BAD"` and each controlled name (message names the field and `product_sandbox =
    "off"`); `Config.product("claude-code")` with a wrapper `command` → `wrapper is True`;
    `pass_env` reaches `Product.pass_env`.
  - CLI: `test_product_sandbox_flag_beats_the_file` (monkeypatched
    `decide_product_sandbox` records the mode); `test_a_run_with_no_product_decides_nothing`
    (fake runner, `--product-sandbox required`, exit 0, decide not called);
    `test_required_with_no_backend_exits_2`; `test_an_unknown_product_sandbox_is_exit_2`;
    every product runner and the product judge receive the same `ProductSandbox` object.
  - Action: the existing `test_every_cli_flag_is_exposed_as_an_action_input` and
    `test_every_cli_backed_input_is_actually_forwarded_to_the_command` cover the input once
    it exists; add the input with `add --product-sandbox "$SE_PRODUCT_SANDBOX"`.

- [ ] **Step 2: Run** `uv run pytest tests/test_config.py tests/test_cli.py tests/test_action.py
  -q` — fail.

- [ ] **Step 3: Implement.** In `cli.run`, resolve the mode (flag > file), validate it like
  `--baseline`, and call `decide_product_sandbox` once when `uses_product` or `judge_name in
  PRODUCT_NAMES`, before `_build_runner`; pass `sandbox=` through `_build_runner` /
  `_build_judge`. Document `product_sandbox` and `pass_env` in `docs/configuration.md`, the
  flag in `docs/cli.md`, the input in `docs/ci.md` (pass `required` for untrusted pull
  requests).

- [ ] **Step 4: Run** the three files and `uv run pytest tests/test_docs.py -q`; pass.

- [ ] **Step 5: Commit** `feat: add product_sandbox, --product-sandbox and pass_env`.

---

### Task 7: The live sandbox tests

**Files:**
- Create: `tests/test_product_sandbox_live.py`

**Interfaces:**
- Consumes: `decide_product_sandbox("auto")`, `ProductRunner`, a `cli` `Product` whose argv is
  `(sys.executable, "-c", SOURCE, "{prompt}")`, `pass_env=()`.

- [ ] **Step 1: Write the tests** (module skips when the decision's backend is `"none"`;
  raises at import when `SKILL_LENS_REQUIRE_SANDBOX` is set and none works, as
  `tests/test_sandbox_live.py` does):
  - `test_the_product_writes_its_working_directory_and_home`.
  - `test_a_write_outside_never_reaches_the_host` (a `tmp_path` sibling of the workspace does
    not exist after the run).
  - `test_a_sibling_temporary_directory_cannot_be_read`.
  - `test_the_network_is_reachable` (the test listens on `127.0.0.1:<port>`, passed in the
    prompt; output says `connected`).

- [ ] **Step 2: Run** `uv run pytest tests/test_product_sandbox_live.py -v` on this Mac; all
  pass under `sandbox-exec`. If a product-side write needs another path, follow the spec:
  widen to that named path, reword the note and docs in the same commit.

- [ ] **Step 3: Commit** `test: prove the product sandbox on a real backend`.

---

### Task 8: Security docs and invariants

**Files:**
- Modify: `docs/security.md`, `docs/runners.md`, `docs/troubleshooting.md`,
  `tests/test_troubleshooting.py` (`QUOTED`), `ARCHITECTURE.md`, `docs/invariants.md`,
  `CLAUDE.md`

- [ ] **Step 1:** `docs/security.md` "Product runners": the three modes, what each guarantees
  and what none does, the macOS same-user environment read, `product-sandbox: required` for
  untrusted pull requests. `docs/runners.md`: the Trust paragraph, "What the product sees",
  the Copilot hermetic-run note (true under `off` only now). `docs/troubleshooting.md`: the
  auth-variable refusal, quoted, and added to `QUOTED`. `ARCHITECTURE.md`: the new module.
- [ ] **Step 2:** Invariants, in both files: update the bodies of "Naming a product runner
  is the trust decision, and the report says so" and "Preflight spends nothing"; add "Home
  isolation is not a sandbox, and the report never calls it one".
- [ ] **Step 3: Run** `uv run pytest tests/test_invariants_sync.py tests/test_docs.py
  tests/test_troubleshooting.py -q` and `uv sync --group docs && uv run mkdocs build
  --strict`; pass.
- [ ] **Step 4: Commit** `docs: describe the product sandbox and its guarantees`.

---

### Task 9: Real-product check and full verification

- [ ] **Step 1:** Free: `claude --version` and `copilot --version` through
  `decide_product_sandbox("required")` + `isolate` (a short script in the scratchpad).
- [ ] **Step 2 (approved by the user, spends a little quota):** one `skill-lens run` of
  `examples/greeting` (or a one-case suite) under `--runner claude-code --product-sandbox
  required`, and one under `--runner copilot`, with the tokens exported. Record versions,
  results and any profile change in the PR body.
- [ ] **Step 3:** `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv sync --group docs && uv run mkdocs build --strict` — all clean.
- [ ] **Step 4:** Leave a hand-over note for the sibling sessions in the shared notes folder
  (one temporary home per work item for #95; `product.py` edits for #96).
