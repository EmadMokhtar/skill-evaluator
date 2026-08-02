---
applyTo: "docs/**,*.md,mkdocs.yml"
---

# Reviewing documentation

- **`README.md` is a landing page.** Reference prose lives in `docs/`. Flag any PR that
  reintroduces command, config or eval-file reference material into the README — it will
  drift.
- **`ARCHITECTURE.md` has exactly one copy**, at the repository root.
  `docs/architecture.md` includes it with a `pymdownx.snippets` directive. Flag any
  duplication of its content.
- **A new page must be added to `nav:` in `mkdocs.yml`.** `tests/test_docs.py` fails on an
  orphan page, and `mkdocs build --strict` fails on a nav entry with no file.
- **`docs/superpowers/` is a historical archive** of specs and plans. It is excluded from
  the built site and does not count as documenting a change. Its contents were superseded by
  what shipped — read `src/` as the source of truth.
- Documentation ships **with** the change: a new flag updates `docs/cli.md`, a new config
  key updates `docs/configuration.md`, a new assertion kind updates `docs/eval-files.md`, a
  new invariant updates `ARCHITECTURE.md`.
- Do not add `pymdownx.emoji` or mermaid `custom_fences` to `mkdocs.yml`: both need
  `!!python/name:` YAML tags, which break the plain-YAML nav parsing in `tests/test_docs.py`.
- Relative links must resolve — there is a test for it.
