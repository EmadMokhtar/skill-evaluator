# Security

What is checked, where it runs, and what happens when a check finds something. This page
is written for the person who has to decide whether their organisation can depend on
skill-lens; everything below is enforced by a test or a CI job, not by intent.

The checks cover skill-lens's own code and its dependencies. They say nothing about the
skills you evaluate *with* it — a skill under test is an input, and it gets no more
trust than any other input.

## What is checked

### Known vulnerabilities in dependencies

`uv audit` reads `uv.lock` — the exact set of packages anyone installing from this
repository gets — and asks [OSV](https://osv.dev), the cross-ecosystem vulnerability
database, about every one of them. Nothing is installed to do this; it takes about a
second. It covers every extra and every dependency group, so a vulnerable test or docs
dependency is reported too: those run on maintainers' machines and in CI, which is where
a supply-chain attack would want to be.

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

Two sites in `src/` are suppressed, each with the reason on the line itself:

- `skills/baseline.py` starts `git` by name rather than by absolute path. That is
  deliberate: a machine without git must produce `BaselineUnavailable`, not a crash, and
  an absolute path would be wrong on most machines.
- `yaml_loading.py` uses `yaml.load` with `StrictBoolLoader`, which ruff cannot see is a
  `SafeLoader` subclass. Every YAML file skill-lens reads goes through that loader; it is
  what stops YAML 1.1 from turning a bare `yes` or `no` into a boolean.

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
