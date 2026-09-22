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
- **`ARCHITECTURE.md` must use absolute links only (full URLs), never relative links.**
  `tests/test_docs.py` resolves its relative links from the repo root, but mkdocs resolves
  them from `docs/` because the file is inlined into `docs/architecture.md` by a snippet —
  a relative link can pass the test and still fail `mkdocs build --strict`.
- **A new page must be added to `nav:` in `mkdocs.yml`.** `tests/test_docs.py` fails on an
  orphan page, and `mkdocs build --strict` fails on a nav entry with no file.
- **The nav has four sections, and a new page belongs to one of them.** *Guides* is read
  start to finish. *Reference* is looked things up in. *Internals* is for people working on
  skill-lens itself. *Roadmap* stands alone. A page that fits none of them probably belongs
  inside an existing page as a section.
- **`docs/superpowers/` is a historical archive** of specs and plans. It is excluded from
  the built site and does not count as documenting a change. Its contents were superseded by
  what shipped — read `src/` as the source of truth.
- Documentation ships **with** the change: a new flag updates `docs/cli.md`, a new config
  key updates `docs/configuration.md`, a new assertion kind updates `docs/eval-files.md`, a
  new invariant updates `ARCHITECTURE.md`.
- **`!!python/name:` tags in `mkdocs.yml` are fine**, as long as `tests/test_docs.py` parses
  the file with `_mkdocs_config()` — its tag-tolerant loader — rather than the project's
  `safe_load`, which refuses unknown tags. Mermaid's `custom_fences` entry needs one.
  `skill_lens.yaml_loading.safe_load` itself must not be loosened: it guards user YAML.
- **Diagrams are Mermaid**, in a ```mermaid fence. GitHub renders them natively, so
  `README.md` and `ARCHITECTURE.md` get diagrams too. No hard-coded colours — the default
  theme follows the page, so a diagram stays legible in both light and dark mode.
- Relative links must resolve — there is a test for it.
