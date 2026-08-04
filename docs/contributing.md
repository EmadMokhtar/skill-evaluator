# Contributing

## Development

```bash
uv sync
uv run pytest                  # test suite
uv run ruff check .            # lint
uv run ruff format --check .   # formatting (as CI runs it)
uv run skill-eval list ./examples
```

`.python-version` pins the interpreter to 3.13, so `uv sync` uses the same one CI does — no
per-job setup step, and no drift between your machine and the pipeline. The package itself
still supports `>=3.11` (`requires-python`); CI does not currently exercise that floor.

Tests marked `integration` hit real provider APIs and are deselected by default; run them with
`uv run pytest -m integration`. Tests marked `cassette` replay recorded provider traffic —
zero cost, no key needed, and selected by default. Everything else passes offline with no API
spend. `uv run skill-eval run ./examples` needs the `pydantic-ai` runner (see
[Running against a real agent](runners.md)); the shipped examples now
assert real model behavior, so `list` is what dogfoods discovery for free.

`test_a_baseline_run_reaches_the_provider_without_the_skill_name` in `tests/test_cassettes.py`
proves the M4 no-name-leak rule survives the wire, but its cassette has not been recorded yet
— no API key was available when it was written. A missing cassette **skips rather than
fails**, by design, so a fresh clone is never red for this; whoever has a key next should
record it with:

```bash
uv run pytest tests/test_cassettes.py --record-mode=once
```

Development is test-driven: write the failing test first, then the implementation.

## Documentation

```bash
uv sync --group docs
uv run mkdocs serve            # live preview at http://127.0.0.1:8000
uv run mkdocs build --strict   # as CI runs it
uv run pytest tests/test_docs.py
```

Documentation ships with the change, not as a follow-up. Two CI jobs enforce it: `docs`
builds the site with `--strict`, and `docs-freshness` fails a PR that changes
`src/skill_eval/**` without touching `docs/`, `README.md`, `ARCHITECTURE.md` or
`mkdocs.yml`. When a change genuinely needs no documentation — a pure refactor, a
dependency bump — add the `no-docs-needed` label to the PR.

`tests/test_docs.py` is the precise half of the same idea: it asserts that every command,
flag, config key, `EvalCase` field and assertion kind appears in the docs, that every page
is in the nav, and that no relative link is dead.

## Conventional Commits are required

Every commit message **and** every pull request title must follow
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope][!]: <description>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`, `chore`,
`style`, `revert`. Use the imperative mood, lowercase, no trailing period.

This is not stylistic. `cz bump` derives the next version and the changelog from
commit history, so a non-conforming message silently breaks the release. Because
PRs are **squash-merged**, the PR title becomes the commit on `main` — so the
title is what release automation actually reads.

Install the hook once per clone so bad messages are rejected before they land:

```bash
uv run pre-commit install --hook-type commit-msg
```

CI enforces the same rules on every PR: the title is checked with `cz check`, and
every commit on the branch with `scripts/check_commits.py`. Two docs-only commits
predating the convention are exempted in `scripts/legacy-commits.txt`; that list
should only ever shrink.

## One-time repository settings

Two settings live in the GitHub UI, not in this repo, so they are easy to miss when
standing up a fork or a new instance of this project:

- **Settings -> Pages -> Source = "GitHub Actions".** Without it, `docs.yml`'s `build`
  job succeeds but `deploy` fails with an opaque error, and every published docs link
  (including the ones in `README.md`) stays dead.
- **Copilot code review enabled** on the repository. Without it, `.github/copilot-instructions.md`
  and `.github/instructions/*` are never read, so Copilot's PR reviews ignore this
  project's conventions.
