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

## Contributing

### Conventional Commits are required

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
