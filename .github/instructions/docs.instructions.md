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
- **A new page must be added to `nav:` in `mkdocs.yml`**, and linked from `README.md`'s
  Documentation table or `docs/index.md`'s "Where to go next" table. `tests/test_docs.py`
  fails on an orphan page or one neither table links, and `mkdocs build --strict` fails on
  a nav entry with no file.
- **The install instructions have one copy in `docs/`**: `docs/snippets/install.md`, which
  pages include with `--8<-- "docs/snippets/install.md"`. `README.md` keeps the only other
  copy of the command, and `tests/test_docs.py` compares the two. Flag a page that writes
  the install command out again.
- **A message quoted in `docs/troubleshooting.md` is pinned to the module that emits it.**
  Adding, rewording or removing one means changing `QUOTED` in
  `tests/test_troubleshooting.py` in the same PR.
- **The nav has four sections, and a new page belongs to one of them.** *Guides* is read
  start to finish. *Reference* is looked things up in. *Internals* is for people working on
  skill-lens itself. *Roadmap* stands alone. A page that fits none of them probably belongs
  inside an existing page as a section.
- **`docs/superpowers/` is a historical archive** of specs and plans. It is excluded from
  the built site and does not count as documenting a change. Its contents were superseded by
  what shipped — read `src/` as the source of truth.
- Documentation ships **with** the change: a new flag updates `docs/cli.md`, a new config
  key updates `docs/configuration.md`, a new assertion kind updates `docs/eval-files.md`, a
  new invariant updates **both** `docs/invariants.md` and `CLAUDE.md`.
- **An invariant lives in two files, spelled identically.** Its `###` heading on
  `docs/invariants.md` and its bold lead in `CLAUDE.md`'s condensed list must match byte for
  byte — `tests/test_invariants_sync.py` fails if one side is missing or reworded.
  `ARCHITECTURE.md` keeps the design and the module map, not the invariant list.
- **`!!python/name:` tags in `mkdocs.yml` are fine**, as long as `tests/test_docs.py` parses
  the file with `_mkdocs_config()` — its tag-tolerant loader — rather than the project's
  `safe_load`, which refuses unknown tags. Mermaid's `custom_fences` entry needs one.
  `skill_lens.yaml_loading.safe_load` itself must not be loosened: it guards user YAML.
- **Diagrams are Mermaid**, in a ```mermaid fence. GitHub renders them natively, so
  `README.md` and `ARCHITECTURE.md` get diagrams too. No hard-coded colours — the default
  theme follows the page, so a diagram stays legible in both light and dark mode.
- Relative links must resolve, and so must the `#anchor` after one — there is a test for
  each. Renaming a heading means updating every link to it.
