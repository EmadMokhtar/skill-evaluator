# skill-lens: a sandbox for product runners (issue #99)

**Status:** approved 2026-10-23. **Scope of the first PR:** home isolation, plus the
`sandbox-exec` / `bwrap` backend. The container backend is a later PR.

## Why

A product runner (`claude-code`, `copilot`, `cli`) starts the agent with permission prompts
disabled and the whole environment. Its working directory is a fresh temporary directory,
but that is not a boundary: the agent, the skill's hooks and the skill's scripts run as the
user who started skill-lens, and can read and write anything that user can.

Evaluating planning-with-files under `claude-code` showed the cost. The skill's hooks fall
back to `$HOME/.claude/skills/planning-with-files/…` and keep a cache under
`~/.cache/pwf-pretool`, so a run reads and writes the user's home directory. The skill
author's first question about the results was how the real-agent run is isolated. Today the
honest answer is "a separate working directory and configuration, not a separate process".

skill-lens already has OS sandboxes, but only for its own `run_script` tool
(`src/skill_lens/scripts.py`). This design puts a product inside one, and says on every
report exactly what applied.

## Goals

- A **product sandbox decision**, made once per run before any case and shared by every
  product runner and the product judge, stated on every report — the way `ScriptStatus`
  works for scripts.
- **`product_sandbox = "auto" | "required" | "off"`** in `skill-lens.toml`, a matching
  `--product-sandbox` flag, and a matching action input. `required` with no working backend
  exits 2 before anything is spent.
- **Home isolation** as the floor under `auto` and `required`: a temporary home directory
  per work item, and an environment built from an allowlist.
- **An OS sandbox** around the product where one works: `sandbox-exec` on macOS, `bwrap` on
  Linux, reusing the script backends with a profile written for a product.
- **A trust note that says exactly what applied, never more.**

## Non-goals (this PR)

- **The container backend** (Docker or Podman). It is the strongest backend, and the only
  one that hides the rest of the filesystem. It needs an image that holds the product *and*
  skill-lens itself (the MCP bridge runs as `sys.executable -m skill_lens.mcp_bridge`, a
  host path a Linux container cannot run), a mount of the workspace at the same path, user
  mapping for files the product writes, and a version probe inside the image. That is a PR
  of its own; a follow-up issue tracks it.
- **Hiding reads of the user's home directory.** The products and their runtimes are
  installed there: on the machine this was designed on, `claude` resolves to
  `~/.local/share/claude/versions/2.1.289`; an `npm` install lives under `~/.nvm` or
  `~/.volta`. A read denial on the real home would stop the product starting. Hiding reads
  is the container backend's job.
- **Closing the same-user read of the harness's own environment.** On macOS any same-user
  process can read another's exec-time environment through the `kern.procargs2` sysctl
  behind `ps -E`, and `sandbox-exec` does not gate it (already documented for scripts).
  The allowlist stops *inheritance*; the docs say what it does not stop.

## Anti-goals

- **Calling home isolation a sandbox.** It keeps personal installs, logins and caches out
  of a run. It does not stop a process writing `/Users/you/anything` by its absolute path.
  No report, doc line or status field may say or imply otherwise.
- **A trust note that claims more than applied.** Under `auto` with no backend the note
  says no sandbox applied, and why.
- **A silent change in what a run measures.** A change to the product's environment shows
  in the trust note on every report; a product that cannot authenticate under isolation is
  exit 2 in preflight with the fix in the message, never thirty errored cases.

## The three modes

| `product_sandbox` | Home isolation | OS backend | No backend works |
| --- | --- | --- | --- |
| `off` | no — the whole environment, the real home (today's behaviour) | no | — |
| `auto` | yes | yes, when the probe succeeds | home isolation only; the report says why |
| `required` | yes | yes | exit 2 before any case |

`required` needs a real backend. Home isolation alone never satisfies it, because home
isolation is not a sandbox.

The default is **`auto`** (the user's decision, below).

The mode resolves flag > file > default, like `--allow-scripts`. The flag is
`--product-sandbox auto|required|off`, and the action input `product-sandbox` (empty by
default, so the file decides) passes it.

## Where the decision is made

`runners/product_sandbox.py` (a new module, so `runners/product.py` changes stay small and
the merges with #96 and PR #115 stay easy) holds:

```python
@dataclass(frozen=True)
class ProductSandbox:
    mode: SandboxMode            # what was asked
    backend: SandboxBackend      # what applies: "sandbox-exec", "bwrap" or "none"
    detail: str                  # the probe's reason, as ScriptStatus.detail

    @property
    def home_isolated(self) -> bool: ...   # mode != "off"
    def trust(self) -> str: ...            # the trust note, below

def decide_product_sandbox(mode, *, system=None, which=shutil.which,
                           run=subprocess.run) -> ProductSandbox: ...
```

`decide_product_sandbox` is the decision. `off` probes nothing. `auto` and `required` run
the backend once, the way `scripts.probe_sandbox` does (executed, not merely found), with a
product probe: no network denial, a write denial, `/usr/bin/true` (`/bin/true` under
`bwrap`). `required` with no backend raises `ProductSetupError`, which `cli.py` already
turns into exit 2. `system`, `which` and `run` are injectable so the decision table is
tested on every platform.

**The CLI calls it once**, when the run names at least one product runner or a product
judge, and passes the one `ProductSandbox` to every `ProductRunner` and `ProductJudge` it
builds. A run with no product probes nothing, so a `fake` run stays probe-free. This is
before `run_evals`, so before any case and before any spend. Each product's own
`preflight` then proves *its* product starts under that decision (below).

Library API: `ProductRunner(product, *, sandbox=PRODUCT_SANDBOX_OFF)` and
`ProductJudge(product, *, sandbox=PRODUCT_SANDBOX_OFF)`. The default is `off`, so a caller
written against an earlier release runs exactly as before.

## Home isolation

Per **work item** — one `ProductRunner.run` call — a private directory
`skill-lens-home-XXXXXXXX` under the system temporary directory, created with `mkdtemp`
(atomic, so two arms or two concurrent cases never share one), holding `home/` and `tmp/`.
The prefix is short on purpose: a Unix socket path is limited to about 104 bytes, the macOS
temporary directory already takes about 50, and a tool that puts a socket under `TMPDIR`
needs the rest. Every product process that work item starts uses the same one, so the sessions
of #95 can share it. It is deleted in a `finally` when `run` returns, however it returns.
`--keep-workspace` does not keep it: it can hold what the product wrote about its own login.
The judge gets one per `judge` call; the version probe one per probe.

The product's environment is then **built, never inherited**:

| Variable | Value |
| --- | --- |
| `HOME` (and `USERPROFILE`, `APPDATA`, `LOCALAPPDATA` on Windows) | `<root>/home` (`APPDATA` and `LOCALAPPDATA` under it) |
| `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` | `<root>/home/.config`, `.cache`, `.local/share`, `.local/state` |
| `TMPDIR`, `TMP`, `TEMP` | `<root>/tmp` |
| Kept from the harness when set | `PATH`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`, `SHELL`, `USER`, `LOGNAME`; the proxy and CA variables (`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `ALL_PROXY` and their lower-case forms, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS`), because the product must reach its provider; `SystemRoot`, `COMSPEC`, `PATHEXT` on Windows |
| The product's own variables, when set | `claude-code`: `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`. `copilot`: the first set of `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` (Copilot's documented auth variables, in its order of precedence) — only that one, changed after review, since the other two are general GitHub tokens. `cli`: none |
| Set for the product | `claude-code`: `DISABLE_AUTOUPDATER=1`, so a fresh home does not trigger an update on every case, and `CLAUDE_CODE_TMPDIR=<root>/tmp` (added after review: Claude Code ignores `TMPDIR` for its own files and defaults to `/tmp`, which no backend lets it write, so every Bash command reported exit 1). `copilot` already passes `--no-auto-update` |
| `[runners.<name>] pass_env` | each named variable, when set |

`CLAUDE_CONFIG_DIR` and `COPILOT_HOME` are not set: they are dropped with the rest of the
inherited environment, so each product falls back to its directory under the temporary
`HOME` (`~/.claude`, `~/.claude.json`, `~/.copilot`). Setting them explicitly would change
nothing, and Claude Code derives its keychain entry's name from `CLAUDE_CONFIG_DIR`.

**`pass_env`** is a list of variable *names* in `[runners.<name>]`, never values, because a
value would be a secret in a committed file. It is how a Bedrock or Vertex setup passes its
`AWS_*` or `GOOGLE_*` variables, and how a `cli` product gets its own auth. A name must
match `^[A-Za-z_][A-Za-z0-9_]*$`. A name the isolation sets or drops on purpose (`HOME`,
`USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TMPDIR`, `TMP`, `TEMP`, the four `XDG_*_HOME`,
`CLAUDE_CONFIG_DIR`, `COPILOT_HOME`, `CLAUDE_CODE_TMPDIR`, `CLAUDE_TMPDIR`) is a config error naming the field: passing it would
point the product back at the real home. The message says to use `product_sandbox = "off"`
for that. Under `off` the whole environment passes anyway, and `pass_env` is not consulted;
setting it then is not an error (a repository may turn isolation off in one job only).

**`cli` must say what it needs.** skill-lens does not know a `cli` product's auth, and a
command that loses its key may print an error and exit 0, which would score as a *failed*
case — an infra fault read as a skill signal. So under home isolation `[runners.cli]` must
set `pass_env` explicitly (`pass_env = []` when the command needs nothing); unset is exit 2
in preflight, naming the key and `product_sandbox = "off"`. `pass_env` is therefore
`list[str] | None`, `None` meaning "not set".

**A preset whose `command` names another executable** (a wrapper, such as `secret run --
claude …`) keeps the preset's variables and `env` in its allowlist, but is not
auth-checked: the wrapper may bring its own auth. This matches how `Config.product` already
drops the version probe, `judge_args` and the MCP flag for a wrapper.

**What home isolation changes for the user.** The product cannot use a login stored in the
real home or the OS keychain, and personal skills, plugins, settings and caches no longer
load (today the docs say personal skills under `~/.copilot` still load for Copilot; that
becomes true under `off` only). Auth comes from the environment: `CLAUDE_CODE_OAUTH_TOKEN`
(`claude setup-token`) or `ANTHROPIC_API_KEY`; `COPILOT_GITHUB_TOKEN`. CI already works
this way.

## The OS backend for a product

The script profile blocks the network; a product must reach its provider, so the product
profile allows it. Otherwise the rules are the script rules, rebuilt from the same parts in
`scripts.py` (`_quoted`, the parent-metadata rule, the probe):

**`sandbox-exec`** (macOS):

```
(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath <cwd>) (subpath <root>) [(subpath <bridge dir>)])
(allow file-write-data (literal "/dev/null") (literal "/dev/stdout")
                       (literal "/dev/stderr") (regex #"^/dev/fd/[0-9]+$"))
(deny file-read* (subpath <tempdir>))
(allow file-read* (subpath <cwd>) (subpath <root>) [(subpath <bridge dir>)])
(allow file-read-metadata (literal <each parent between tempdir and those>))
```

**`bwrap`** (Linux):

```
bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs <tempdir>
      --bind <cwd> <cwd> --bind <root> <root> [--bind <bridge dir> <bridge dir>]
      --remount-ro <tempdir>
      --unshare-pid --die-with-parent --new-session -- <argv>
```

No `--unshare-net`. `--remount-ro <tempdir>` after the binds (added during implementation,
matching PR #120 for scripts): without it a write elsewhere under the temp directory lands
in the tmpfs and vanishes instead of being refused, unlike `sandbox-exec`. It is not
recursive, so the binds keep their writable mode, and it must follow them, because bwrap
creates their mount points inside the tmpfs. `--unshare-pid` with `--die-with-parent` means killing `bwrap` kills
every process the product started, as it does for scripts.

So, under either backend, the product:

- **cannot change files outside** its working directory, its temporary home and, when the
  case declares `tools:`, the MCP bridge's directory (the bridge is the product's child, so
  it runs inside the same sandbox and must write its record file);
- **cannot read** other skill-lens temporary directories — the other arm's workspace, other
  cases' homes;
- **can read** everything else the user can, and **can reach the network**.

**Verified before the design was written**, at no cost: `claude --version` (2.1.289) and
`copilot --version` (1.0.37) start under this profile with a temporary home, writing only
`<root>/home/Library/Caches/copilot` and `<root>/tmp/node-compile-cache`, with `/dev/null` the
only device written. Writes to an already-open inherited file (the capture files `invoke`
opens before the process starts) need no rule.

The standard streams by path were added after review: `echo msg >/dev/stderr`, common in
hooks and scripts, was refused under `/dev/null` alone, and under `set -e` that aborts the
script. They reach only descriptors the process already holds.

**If a real product needs more**, the writable set is widened only to a named path, the
trust note and the docs are reworded to match in the same commit, and the PR says so. It is
never widened silently, and never to a whole directory such as the real home.

`scripts.macos_profile` and `scripts.bwrap_argv` keep their output byte for byte; the
product builders sit beside them. The script backend and the product backend are two
decisions with two keys; a run with both on probes each once.

## Preflight

`ProductRunner.preflight` and `ProductJudge.preflight` keep every check they make today,
and add:

1. **The version probe runs under the run's decision**: in a temporary home, with the built
   environment, wrapped in the backend. A product that starts outside the sandbox but not
   inside it is exit 2 up front, naming the backend and saying `product_sandbox = "off"`
   runs it without one. `cli` has no version command, so its first start is the first case;
   a failure there is an errored case, as today.
2. **The MCP bridge probe runs under the same decision**, when a planned case declares
   `tools:` — the bridge is the product's child, and its environment is the product's.
3. **Under home isolation, a preset needs an auth variable.** At least one of the preset's
   own variables, or one named in `pass_env`, must be set and non-empty in the harness's
   environment. Otherwise `ProductSetupError` (exit 2), for example:

   > runner claude-code: product_sandbox = "auto" gives the product a temporary home
   > directory, so it cannot use a login stored in your home directory or keychain; export
   > CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY, name the
   > variable your setup uses in [runners.claude-code] pass_env, or set
   > product_sandbox = "off"

   This checks that a variable is present, not that it is valid: an expired token is still
   an errored case at run time. `ANTHROPIC_BASE_URL` is passed but does not count: it is an
   endpoint, not a credential. A wrapper `command` is not checked (above); `cli` needs
   `pass_env` set instead (above). A product with no auth fails cleanly at run time anyway
   (verified: `copilot -p` with no token exits 1, "No authentication information found"),
   so the check only moves that failure to before the first case.

All of it spends nothing: no prompt reaches a provider.

## What the report says

`ProductStatus` gains `sandbox: ProductSandboxStatus` (`mode`, `backend`, `home_isolated`,
`detail`), so a JSON consumer reads the decision as data. `trust` stays the sentence a human
reads, composed by `ProductSandbox.trust()` — fixed harness text on the model, so the three
reporters cannot drift. A product serving as runner and judge still lists once: both
statuses are equal.

The three notes. Every one keeps the clause about bundled scripts, because a product can
run them whatever `allow_scripts` says.

- **`off`** — today's sentence, unchanged:

  > runs with permission prompts disabled and the full environment; no skill-lens sandbox
  > applies; bundled scripts are reachable through the product's own tools

- **Home isolation only** (`auto`, no backend works):

  > runs with permission prompts disabled, a temporary home directory and only an
  > allowlisted environment; that is not a sandbox: no skill-lens sandbox applies (bwrap not
  > found on PATH), and the product can read and write anything you can; bundled scripts
  > are reachable through the product's own tools

  The parenthesis is `detail`, so the reason is on the same line.

- **A backend applied**:

  > runs with permission prompts disabled, a temporary home directory and only an
  > allowlisted environment, under sandbox-exec: it cannot change files outside its working
  > directory and its own temporary directories, or read other skill-lens temporary
  > directories; it can read anything else you can, and reach the network; bundled scripts
  > are reachable through the product's own tools

Console, Markdown and JUnit print `trust` where they print it today. The JSON report's
`products` entries carry `sandbox` as an object.

## The product judge

The same `ProductSandbox`, the same home isolation per `judge` call, and the same wrapper
around the judge's process. Its working directory stays empty; the trust note is the same
sentence, so a product serving both seats is still one line.

## Config, flag and action

- `Config.product_sandbox: SandboxMode = "auto"` (top level, beside `script_sandbox`).
- `ProductSettings.pass_env: list[str] | None = None`, validated as above.
- `--product-sandbox [auto|required|off]` on `skill-lens run`, overriding the file. On a
  run with no product runner and no product judge it is accepted and unused, and nothing
  is probed — like `--allow-scripts` on a run with no scripts, not like `--model` with
  nothing to read it. It is a policy, set once in a shared workflow; refusing it would make
  `product-sandbox: required` break every framework-runner job that shares the workflow.
- `action.yml`: input `product-sandbox`, empty by default so the file decides. A workflow
  that runs untrusted pull requests should pass `required`: `skill-lens.toml` is inside the
  trust boundary, so a pull request can set `product_sandbox = "off"` for itself, and the
  flag overrides the file.

## How it fits the work in flight

- **PR #115 (#92)** changes *what* is delivered into the working directory. This design
  changes *how* the process is started. They meet in `ProductRunner.run` only: the delivery
  call and the invocation sit side by side. Delivery writes into `cwd`, which is writable
  under the sandbox, so no rule here depends on which files are delivered.
- **#96** adds `--include-hook-events` to the `claude-code` argv. This design adds no argv
  flag to the preset; the changes to `PRESETS` are new fields (`auth_env`, `env`) only.
- **#95** (several sessions per case) makes one `Runner.run` call per session (corrected
  during implementation, from its notes), so each session gets its own temporary home; what
  the sessions share is the workspace, which is what #95 measures.
- **#94** (spend ceiling) is unaffected: spend is still read from the trace.

## Invariants

Changed, in both `docs/invariants.md` and `CLAUDE.md`, headings unchanged:

- **Naming a product runner is the trust decision, and the report says so.** The body
  describes the three notes, the decision being made once per run, and `trust` being
  composed from it.
- **Preflight spends nothing.** The body adds the version and bridge probes under the
  decision, and the auth-variable check.

New, in both files:

- **Home isolation is not a sandbox, and the report never calls it one.** `required` needs
  a real backend; the home-only note says "that is not a sandbox"; the environment is built
  from an allowlist, so the harness's own provider keys are absent by construction.

## Docs

`docs/security.md` (Product runners: the three modes, what each guarantees and what none
does, the macOS environment read, the action input for untrusted pull requests),
`docs/runners.md` (the Trust paragraph, the Copilot hermetic-run note, what the product
sees), `docs/configuration.md` (`product_sandbox`, `pass_env`), `docs/cli.md`
(`--product-sandbox`), `docs/ci.md` (the input), `docs/gating.md` (`products[].sandbox` in
the JSON report), `docs/troubleshooting.md` (the auth-variable message, with `QUOTED`),
`ARCHITECTURE.md` (the new module).

## Tests

TDD, each test failing first; the pipeline tier stays offline and costs nothing.

- **The decision table**: every mode × platform × probe outcome, with injected `system`,
  `which` and `run`; `required` with no backend raises; `off` never calls `run`.
- **The environment**: built from the allowlist; a harness-only variable
  (`OPENAI_API_KEY`) is absent; the product's variables and `pass_env` pass; `HOME` and the
  `XDG_*` point into the private directory; Windows variables with `windows=True`.
- **`pass_env` validation**: a bad name and each controlled name are config errors.
- **The profile and the argv**: the network is not denied; the writable set is exactly
  `cwd`, the root and the bridge directory; the script builders' output is unchanged.
- **The runner and the judge**, with the existing fake product (it reads its
  `FAKE_PRODUCT_*` variables, which reach it through `pass_env`): `HOME` is temporary, the
  private directory is gone after `run` (also after an error), two concurrent work items get
  two homes, and `off` passes `os.environ` as today.
- **Preflight**: the version probe runs wrapped; no auth variable is exit 2; a `cli` product
  is not checked; one decision serves runner and judge.
- **Reporters**: each trust note in console, Markdown, JUnit and JSON.
- **CLI**: flag beats file; a run with no product probes nothing; the action input maps to
  the flag.
- **Live, skipped where no backend works** (CI's macOS job sets
  `SKILL_LENS_REQUIRE_SANDBOX`, so it cannot skip there): a fake product under the real
  backend cannot write outside, can write its home and working directory, cannot read a
  sibling temporary directory, and **can** connect to a socket the test listens on at
  `127.0.0.1` — still offline.

**`bwrap` is verified by string tests and by the live tests where a Linux machine has a
working `bwrap`**; it cannot run on macOS, and a CI runner whose kernel refuses user
namespaces skips the live file, as it does for scripts. No CI change in this PR.

**Manual check against the real products**, with the user's permission because a prompt
spends quota: `claude --version` and `copilot --version` under each decision cost nothing;
one short `claude -p` and one `copilot -p` under `sandbox-exec` show the product
authenticates from the environment and runs a tool inside the sandbox. It is done before the
PR is opened, and the PR records the result.

## Calls made without asking

- Home isolation is per `Runner.run` call (one work item, or one #95 session), not per
  process.
- The temporary home is always deleted, `--keep-workspace` or not.
- `CLAUDE_CONFIG_DIR` and `COPILOT_HOME` are dropped, not set.
- `DISABLE_AUTOUPDATER=1` for `claude-code` under isolation.
- Proxy and CA variables are kept by default.
- The auth-variable check in preflight.
- The variable list is named `pass_env` (tox's name for the same thing).
- The version and bridge probes run under the decision; under `auto`, a product that will
  not start inside a working sandbox is exit 2, not a quiet fallback to no sandbox.
- The action gets a `product-sandbox` input in this PR.
- One new invariant, for home isolation.
- `cli` must set `pass_env` (empty allowed) under home isolation; a wrapper `command` is
  not auth-checked.
- `--product-sandbox` on a run with no product is accepted and unused.
- The temporary-home prefix is `skill-lens-home-`, short for socket paths.
- A profile gap is fixed by widening to a named path with the note reworded, never
  silently.

## Decisions the user made

1. **The default is `auto`.** Every product run is isolated after upgrade. Local users who
   log in through the keychain or `~/.claude` export a token once; preflight says so, exit 2.
2. **Scope:** home isolation plus `sandbox-exec`/`bwrap` in this PR; the container backend
   in a follow-up issue.
3. **The real-product check runs:** one short `claude -p` and one `copilot -p` under
   `sandbox-exec`, with the user's tokens, before the PR is opened.
