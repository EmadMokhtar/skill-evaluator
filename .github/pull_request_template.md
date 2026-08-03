## What and why

<!-- What changes, and what problem it solves. The diff shows what; explain why. -->

## Checklist

- [ ] The PR title follows [Conventional Commits](https://www.conventionalcommits.org/) —
      it becomes the commit on `main` when this is squash-merged.
- [ ] Documentation is updated in the same PR: `docs/` for user-facing behavior,
      `ARCHITECTURE.md` for design or invariants, `mkdocs.yml` for a new page.
      (If genuinely not needed, add the `no-docs-needed` label.)
- [ ] `uv run pytest` passes.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `uv run mkdocs build --strict` passes if any documentation changed.
