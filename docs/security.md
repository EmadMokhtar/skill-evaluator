# Security

What is checked, where it runs, and what happens when a check finds something. This page
is written for the person who has to decide whether their organisation can depend on
skill-lens; everything below is enforced by a test or a CI job, not by intent.

The checks cover skill-lens's own code and its dependencies. They say nothing about the
skills you evaluate *with* it — a skill under test is an input, and it gets no more
trust than any other input. The one place skill-lens runs a skill's own code, its bundled
scripts, is off by default; [Running bundled scripts](#running-bundled-scripts) below says
what turning it on means.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting form —
<https://github.com/EmadMokhtar/skill-evaluator/security/advisories/new> — and see
[`SECURITY.md`](https://github.com/EmadMokhtar/skill-evaluator/blob/main/SECURITY.md) for
what to include and what to expect.

## What is checked

### Known vulnerabilities in dependencies

`uv audit` reads `uv.lock` — the exact set of packages anyone installing from this
repository gets — and asks [OSV](https://osv.dev), the cross-ecosystem vulnerability
database, about every one of them. Nothing is installed to do this; it takes about a
second. It covers every extra and every dependency group, so a vulnerable test or docs
dependency is reported too: those run on maintainers' machines and in CI, which is where
a supply-chain attack would want to be.

The build backend is covered too. `uv.lock` records what the project *installs*, not what
*builds* it — `[build-system] requires` is resolved fresh at build time — so a `build`
dependency group mirrors those requirements (a test keeps the two equal). That puts
`hatchling` and its own dependencies in the lockfile, where the audit sees them, and lets
the release pin the build to exactly those versions (see [Releasing](releasing.md)).

Besides vulnerabilities it reports **adverse project statuses** — packages PyPI has
marked deprecated, archived or quarantined — so a dependency being abandoned upstream is
visible before it becomes a vulnerability.

Any finding fails the check. There is no severity threshold to argue about.

`uv audit` is still a preview feature of uv, which is why the command carries
`--preview-features audit`: it acknowledges the opt-in and keeps an "experimental"
warning out of every log. If the command's interface changes, the tests that pin its
wiring (`tests/test_security_checks.py`) fail on the pull request that bumps uv, not in a
release.

### Risky patterns in our own code

Ruff's `S` rule family (`flake8-bandit`) runs as part of the ordinary `ruff check .`:
subprocesses started through a shell, YAML loaders that can construct arbitrary objects,
hard-coded credentials, weak hashes, `assert` used as a runtime check, and so on. Because
it rides on the existing lint step it runs everywhere lint does — every pull request,
the release gate, and your terminal — with nothing extra to remember.

Five sites in `src/` are suppressed, each with the reason on the line itself:

- `skills/baseline.py` starts `git` by name rather than by absolute path. That is
  deliberate: a machine without git must produce `BaselineUnavailable`, not a crash, and
  an absolute path would be wrong on most machines.
- `yaml_loading.py` uses `yaml.load` with `StrictBoolLoader`, which ruff cannot see is a
  `SafeLoader` subclass. Every YAML file skill-lens reads goes through that loader; it is
  what stops YAML 1.1 from turning a bare `yes` or `no` into a boolean.
- `scripts.py` starts subprocesses — the sandbox probe and the bundled script under its
  interpreter — from an argv list with no shell, which is the whole point of that module;
  ruff flags every subprocess call regardless.
- `process.py` runs `taskkill` on Windows to end a process tree, from an argv list with
  no shell; `taskkill` is found on `PATH` by name for the same reason `git` is.
- `runners/product.py` starts the agent product and its `--version` probe from an argv
  list with no shell; the prompt travels as one argv element, never joined into a string.

`tests/` may use `assert`, run `git`, parse the JUnit XML it just wrote, and use literal
`/tmp/...` strings as fake path values in fixtures; `scripts/`
(the CI helpers) may run `git`. Those are per-directory ignores in `pyproject.toml`.
Anything else is suppressed inline, at the site, with a reason — a rule is never switched
off for the whole codebase because one site trips it.

## Where it runs

| When | Where | A failure means |
| --- | --- | --- |
| Every pull request and every push to `main` | The **Security** workflow (`.github/workflows/security.yml`) | A red check on the pull request. |
| Weekly (Monday 06:00 UTC), and on demand | The same workflow, on `schedule` and `workflow_dispatch` | An advisory was published against a lockfile nobody has touched. GitHub notifies the maintainers; the fix is a pull request that upgrades the package. |
| Before a release | The `verify` job in `.github/workflows/release.yml` | Nothing is tagged and nothing reaches PyPI. A merge to `main` never publishes a lockfile with a known vulnerability. |
| Before a push, on your machine | The `pre-push` hook in `.pre-commit-config.yaml` | The push is refused. It runs at push time rather than commit time because it needs the network — a commit hook would fail offline and teach people to skip it. |
| Wherever `ruff check .` runs | The CI `test` job, the release gate, your terminal | The static-analysis rules above found something. |

The scheduled run is what makes this a monitor rather than a one-off check: the
pull-request and push triggers only look when someone changes the repository.

## Running it locally

```bash
uv audit --preview-features audit --locked   # known vulnerabilities in uv.lock
uv run ruff check .                           # includes the S rules
```

`--locked` makes uv fail if `pyproject.toml` and `uv.lock` disagree, instead of quietly
auditing a fresh resolution that nobody installs.

Install the hooks once per clone and the audit runs before every push:

```bash
uv run pre-commit install --hook-type commit-msg --hook-type pre-push
```

## When the audit finds something

**A fixed version exists.** Upgrade the package in the lockfile, re-run the suite, and
commit `uv.lock`:

```bash
uv lock --upgrade-package <name>
uv sync --all-extras --dev
uv run pytest
```

This works for transitive dependencies (packages you did not ask for, pulled in by one
you did) as long as the package that requires them allows the fixed version. If it does
not, upgrade that package instead.

**No fixed version exists yet.** Record an exception in `pyproject.toml`, with a comment
saying why the finding does not affect skill-lens:

```toml
[tool.uv.audit]
ignore-until-fixed = [
    "GHSA-xxxx-xxxx-xxxx",  # <package>: <why this does not affect skill-lens>
]
```

`ignore-until-fixed` stops hiding the advisory the day a fixed version is published, so
the check turns red again and forces the upgrade. Plain `ignore`, which hides an advisory
forever, is rejected by `tests/test_security_checks.py` — as is any other key in that
table, because uv does not validate it and a misspelled key would be silently dropped.

Exceptions live in that table and nowhere else. Every copy of the audit command — the
Security workflow, the release gate, the pre-push hook — reads it, and a test requires the
three commands to be spelled identically, so an exception can never apply to CI and not to
the release, or to your machine and not to CI.

## What a release carries

Each version published to PyPI is accompanied by:

- **A Software Bill of Materials (SBOM)** in CycloneDX 1.5, attached to the GitHub Release
  for the tag at a stable URL:
  `https://github.com/EmadMokhtar/skill-evaluator/releases/download/vX.Y.Z/skill-lens-X.Y.Z.cdx.json`.
  It is exported from the lockfile the release gate just audited, with `--frozen` so nothing
  is re-resolved, and it describes what an installer gets — the runtime dependencies plus the
  optional extras (`pydantic-ai`, `langchain`) — not the dev or docs tooling, which ships to nobody. The wheel
  and sdist PyPI received are attached beside it.
- **Signed attestations on PyPI.** The upload uses Trusted Publishing (PyPI trusts a specific
  GitHub workflow through short-lived tokens; no stored password or API token exists), and
  the publishing action generates a [PEP 740](https://peps.python.org/pep-0740/) attestation
  for every file by default — a signed record of which repository, workflow and commit built
  it. PyPI shows it on each file's page.

The GitHub Release is created **after** PyPI accepted the upload, never before, so it cannot
advertise a version that `pip install` cannot find. See [Releasing](releasing.md) for the
order of jobs and how a failed step is recovered.

## The repository's own automation

- **Every GitHub Action is pinned to a commit SHA**, with the version it corresponds to in a
  trailing comment (`actions/checkout@11d5960…  # v4.4.0`). A tag can be moved, so
  `@v4` runs whatever `v4` points at on the day; a commit cannot. `tests/test_supply_chain.py`
  fails on any `uses:` that is not a 40-character SHA with a version comment.
- **Dependabot** (`.github/dependabot.yml`) proposes weekly updates for both `uv.lock` and the
  pinned actions, since a pinned hash never moves on its own. Minor and patch bumps arrive as
  one grouped pull request; a major bump gets its own. Its pull-request titles are
  Conventional Commits, because the title becomes the commit on `main` that versioning reads.
- **Least-privilege tokens.** No workflow grants write access at the top level; each job asks
  for exactly what it uses, so the docs `build` job, which runs third-party tooling on the
  checkout, holds only the read access it needs, and `publish` holds `id-token` and nothing
  that can write to the repository. The one job that can write releases, `github-release`,
  installs nothing and checks nothing out: it downloads artifacts that earlier jobs built and
  verified, and runs `gh`.

## Running bundled scripts

A skill may ship code under `scripts/`. skill-lens can execute it, and the trust model is:

- **Off by default, on only by the operator's decision.** `allow_scripts = true` in
  `skill-lens.toml` or `--allow-scripts`. Nothing in an eval file or a `SKILL.md` can turn
  it on. A `SKILL.md` under evaluation is unvetted code, and skill-lens runs in CI.
- **The config file is inside the trust boundary.** `skill-lens.toml` is read from the
  checkout, and in a `pull_request` workflow the checkout *is* the pull request: a PR can
  set `allow_scripts = true` and `script_sandbox = "off"` itself. The action's
  `allow-scripts` input is unset by default so the file decides, exactly like
  `keep-workspace`. Under plain `pull_request` a fork gets no secrets, so a real runner
  stops at the missing API key before any script runs; the exposure is
  `pull_request_target`, pull requests from collaborators, and self-hosted runners. A
  workflow in any of those positions should pass `allow-scripts: false` explicitly, which
  overrides the file whatever it says.
- **Portable guards always apply:** an environment rebuilt from an allowlist (no provider
  key, no inherited secret — absent by construction, not by deletion), a scratch directory
  for temporary files, no shell, a process group that is killed after every exit (a
  timeout as much as a normal one), output read from files and capped with a visible cut.
- **The allowlist stops inheritance, not a same-user read of the harness itself.** A
  script runs as the user skill-lens runs as, and the OS will show a same-user process
  another process's exec-time environment — where the provider key lives for the whole
  run. On Linux that is `/proc/<pid>/environ`: when scripts are enabled, skill-lens marks
  itself non-dumpable (`prctl(PR_SET_DUMPABLE, 0)`) so that read is refused where the
  kernel honours the flag, which root does not; the report prints `harness environment
  hidden from same-user processes (PR_SET_DUMPABLE)` when it applied, and under `bwrap`
  the harness is in a separate PID namespace regardless. On macOS the read is the
  `kern.procargs2` sysctl behind `ps -E`, and `sandbox-exec` does not gate it: `/bin/ps`
  itself cannot run in the sandbox (setuid), but the sysctl is one `ctypes` call away and a
  blanket `(deny sysctl-read)` that refuses every other sysctl still lets it through, so no
  profile rule closes it. The consequence: a macOS run with scripts on, or a Linux run
  without a working `bwrap`, exposes the harness's provider key to a script that looks for
  it. Set `script_sandbox = "required"` on any runner that holds a provider key, and in CI
  also stop `actions/checkout` persisting the job token (`persist-credentials: false`, see
  [CI integration](ci.md#the-composite-action)).
- **What a script leaves behind is read on the harness's terms.** Every reader — the
  agent's `read_file` and `read_skill_file`, a `file:` assertion, a judge artifact —
  refuses a path that is not a regular file or a directory (a FIFO, a device, a symbolic-link
  loop) from a `stat` rather than an `open`, so a planted FIFO cannot block the run, and
  refuses a file over `max_file_bytes` before reading a byte of it, so a sparse file of any
  apparent size cannot exhaust memory. A refusal an assertion meets is a **failed** check,
  never an aborted run.
- **An OS sandbox applies where one exists** — `sandbox-exec` on macOS, `bwrap` on Linux.
  Under it a script cannot open a network connection, cannot write to the host filesystem
  outside the workspace and its scratch directory (under `bwrap`, writes under the
  temporary directory and `/dev/shm` land in an in-memory mount discarded when the script
  exits), and cannot read anything under the system temporary directory except the
  workspace, the scratch directory and the skill's own bundle — so other arms' and other
  cases' workspaces are hidden from it. One gap to know about: `bwrap`'s `--unshare-net`
  isolates the network stack only and does not block a Unix-domain socket reachable
  through the filesystem, so on a runner whose user can reach `/var/run/docker.sock` a
  script can talk to the Docker daemon — a full escape on that host; macOS's `(deny
  network*)` covers Unix sockets too. The backend is executed once per
  run before any case, not merely found on `PATH`; `script_sandbox = "required"` makes its
  absence exit 2, and the report always says which backend applied. `sandbox-exec` is
  marked deprecated in Apple's documentation and remains present and working on current
  macOS; Bazel, Chromium and Claude Code rely on it, and the probe is what turns "present"
  into "works".
- **What no layer prevents:** a script can read every other file the CI user can read, and
  print it, and that output reaches the model and the run report. Treat enabling scripts
  as running the skill's code yourself, because it is. Do not enable scripts for a skill
  you would not run by hand.

Details and the per-platform table are in
[Running bundled scripts](runners.md#running-bundled-scripts); enabling it in the GitHub
Action is covered in [CI integration](ci.md#the-composite-action).

## Product runners

A product runner (`--runner copilot`, `claude-code`, `cli`) hands the skill to an agent
product with its **permission prompts disabled** (`--allow-all-tools`,
`--dangerously-skip-permissions`) — the product cannot run non-interactively otherwise —
and with the **whole environment inherited**, because the product needs its own auth.
Everything the product can do, the skill under evaluation can make it do: run shell
commands as you, read what you can read, and run the skill's own bundled scripts through
the product's shell, whatever `allow_scripts` says. `allow_scripts` governs only
skill-lens's `run_script` tool; no skill-lens sandbox applies to a product. This is the
opposite of the script allowlist above, on purpose: under a product runner the product is
the harness, not the subject.

**Naming a product runner is the trust decision.** The report states it on every run
(`product copilot 1.0.37 (/usr/local/bin/copilot): runs with permission prompts disabled
and the full environment; no skill-lens sandbox applies; bundled scripts are reachable
through the product's own tools`), and the JSON report's `products` entries and the JUnit
`skill-lens.products` property carry the same sentence.

**A case's mock tools reach the product through skill-lens's own MCP bridge.** A `tools:`
block under `copilot` or `claude-code` is served by `python -m skill_lens.mcp_bridge`, a
stdio server the product starts as a child process, with the product's environment, from a
config the runner writes into a temporary directory of its own. The bridge is skill-lens's
code, not the skill's: it lists the tools the case declared and answers every call from
the case's `returns:` — one value, a sequence, or a `when:` lookup, by the rules every
runner applies — whatever the arguments; nothing from the eval file
or the skill executes, and the server exits when the product closes its pipe. It adds no
capability the product did not already have; what it adds is a fixed, known answer to a
tool the skill under test may call. The trust sentence on the report is unchanged, because
the decision is unchanged. See [Mock tools under a
product](runners.md#mock-tools-under-a-product).

**A product judge is the same product under the same trust.** `judge = "copilot"`,
`"claude-code"` or `"cli"` starts the product from the same `[runners.<name>]` table, with
permission prompts disabled and the full environment, to grade a rubric — see
[Judging with a product](runners.md#judging-with-a-product). Its prompt is the graded
response and any judge artifacts: text the skill under test produced, and so untrusted. The
prompt says so, but that is a request to the model, not a guarantee. Claude Code grades
with `--tools ""` and Copilot with `--available-tools=skill-lens-none`, each verified
against the product's own CLI to leave the model no tool at all, built-in or MCP, so a
response that reads like an instruction has nothing to act with (`--allow-all-tools` stays
on the Copilot argv because `-p` requires it; it governs approval prompts, and there is
nothing left to approve). The restriction stops the instruction from being *acted on*, not
from swaying the verdict: a judge is a model reading untrusted text, as every judge is.
`cli` is whatever `command` names, with whatever tools that command gives its model, and a
`command` under a preset that names another executable drops the preset's restriction
along with its version probe, because a wrapper is not known to accept the flag;
`[runners.<name>] args` serves both seats, so it cannot restrict the judge alone — and it
cannot widen it either: an `args` or `command` entry carrying the product's tool-selection
flag (`--tools`, `--available-tools`) is refused in the judge's preflight, because both
products accumulate a repeated flag rather than taking the last one, and the entry would
otherwise hand the judge tools back behind the restriction. The
judge's working directory is empty and holds no skill; that limits what such an
instruction can find, not what the product can do. The report lists the product once
whether it ran, judged, or both.

`skill-lens.toml` is inside the trust boundary: in a `pull_request` workflow the checkout
is the pull request, so the file can name a product runner for itself in `default_runner`
and set its `[runners.<name>]` table — including a `command` that replaces the product's
argv with any executable on the runner. A workflow that runs untrusted pull requests should
pin `runner:` explicitly in the action (the flag replaces the file's `default_runner`, so
the file cannot pick the product). The `judge` key has no flag and no action input, so the
checkout's file still picks the judge: `judge = "cli"` with a `command` of its choosing
starts that executable at least once for every case that carries a `judge:` block (once
per arm and repetition), which the same checkout can add. The workflow should also pass
the product's token only to jobs it trusts.
The pin decides *which* runner, but the checkout's file still decides the judge and *how*
each product starts, so the token is what keeps an untrusted checkout from spending your
quota or acting as you. Preflight checks that the executable starts, not that it is signed
in: if the product's own auth fails at run time, that surfaces as an **errored** case, and
what the product does with whatever token the job gives it is the product's behaviour, not
skill-lens's. The exposure is `pull_request_target`, pull requests from collaborators, and
self-hosted runners, the same three as for scripts. For a hermetic Copilot run (one that
loads nothing from your personal setup), point `COPILOT_HOME` at an empty directory and
provide `COPILOT_GITHUB_TOKEN`, so nothing from your personal `~/.copilot` loads. See
[Product runners](runners.md#product-runners) for what each product runner measures and
[CI integration](ci.md#running-under-a-product) for the workflow.

## Why these rules

- **Any finding fails.** A severity threshold is a decision someone has to defend for
  every advisory; "fix it or record why not" is a decision made once.
- **The release re-runs the audit itself.** A green pull request is not proof at release
  time — an advisory can land between the merge and the tag. `verify` runs it again on the
  commit being released, and nothing publishes that `verify` did not pass.
- **Exceptions expire.** `ignore-until-fixed` cannot outlive the fix it is waiting for,
  so the list can only shrink on its own; it never accumulates.
- **One spelling, one source of exceptions.** Three copies of a command drift; a test that
  they are identical, plus a single configuration table, is what makes "the audit passed"
  mean the same thing in every place it runs.
