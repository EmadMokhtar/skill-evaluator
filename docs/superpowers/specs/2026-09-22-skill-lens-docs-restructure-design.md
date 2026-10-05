# skill-lens documentation restructure — design

Date: 2026-09-22
Status: approved, not yet implemented

## Problem

The documentation has grown by accretion, one milestone at a time. Six symptoms, each
measured against the tree at commit `970b693`:

1. **`ARCHITECTURE.md` is 1,442 lines, and 84% of it is one flat list.** Lines 180–1395
   hold 140 invariant paragraphs. The part that explains the architecture — scope, the
   three protocols, the module map, the data flow, the core models, extension points,
   testing tiers — is about 250 lines, split across either end of that list. Nobody reads
   the file; it is too long to open on GitHub.

2. **Milestone labels have already gone stale.** `README.md:281` says "Status: Milestone
   7" while `docs/index.md:14` and `CLAUDE.md` both say M9. Milestone references number 26
   in `ARCHITECTURE.md`, 35 in `docs/roadmap.md`, 24 in `CLAUDE.md`, and 9 across
   `gating.md`, `comparative-evals.md`, `security.md`, `index.md` and `contributing.md`. A
   heading such as `### Comparative evals (M4)` tells a reader *when* a thing was built,
   which a reader of an architecture document never needs to know.

3. **There is no concepts page and no glossary anywhere.** Every page assumes: skill,
   case, runner, evaluator, judge, assertion, trajectory, budget, check, outcome, arm,
   candidate, baseline, delta, gate, workspace, bundle, `loaded` versus `offered` mode,
   product runner, tool library. `docs/getting-started.md` defines "skill" in passing at
   step 2 and defines none of the others.

4. **One diagram exists in the whole project** — the ASCII data flow at
   `ARCHITECTURE.md:129`. There is no diagram on any user-facing page.

5. **`README.md` is a tutorial, not a landing page.** 292 lines, with "Try it", "A case is
   a few lines of YAML", "A run reads like a test suite" and "Gate your pull requests" —
   all of which `docs/getting-started.md` already covers. `.github/instructions/
   docs.instructions.md` says the README must be a landing page only, so the README has
   drifted from a rule this repository already enforces on pull requests.

6. **`docs/roadmap.md` is a changelog in disguise.** Nine "What M5 part 2 shipped"
   sections across 199 lines, and nothing at all about what is next. `CHANGELOG.md`
   already records history, generated from commit messages by `cz bump`.

## Goals

- A reader who has never used the tool can learn its vocabulary from one page.
- `ARCHITECTURE.md` becomes readable end to end.
- The invariants stay exactly as enforceable as they are today, and stay in my context at
  the start of every session.
- No documented behaviour changes. Nothing under `src/` is touched.

## Non-goals

- Rewriting what the invariants say. This is a move plus a consistent shape, not a
  re-derivation. A sentence that is wrong today stays wrong today and is fixed separately.
- Touching `docs/superpowers/` (a historical archive) or `CHANGELOG.md` (generated).
- Adding redirects for moved anchors on the published site. See *Accepted risks*.

## Decisions taken

| Question | Decision | Why |
| --- | --- | --- |
| Diagram format | Mermaid | GitHub renders it natively, so `ARCHITECTURE.md` and `README.md` get diagrams too; Material for MkDocs renders it on the site; it is diffable text |
| Where the 140 invariants live | One new page, `docs/invariants.md` | Invariants are looked up, not read end to end, so one page keeps browser search and a single link working. One nav entry, not nine. Cross-references between invariants stay same-page |
| `CLAUDE.md`'s invariant list | Kept in full, plus a drift test | `CLAUDE.md` is loaded into the agent's context at session start. A link is not a substitute: without the list inline, an invariant is only known after the file is read. The duplication is deliberate; a test stops it drifting |
| Delivery | Four sequenced pull requests | Each is reviewable and revertible on its own, and each gets its own Conventional Commit title |
| "Choosing a runner" / "choosing a check" | Sections in existing pages, not new pages | Two more nav entries for two short pages is clutter, and each belongs beside its reference material |

## PR 1 — `docs: drop milestone labels from the documentation`

**Headings renamed in `ARCHITECTURE.md`** (8):

| Line | From | To |
| --- | --- | --- |
| 357 | `### Comparative evals (M4)` | `### Comparative evals` |
| 415 | `### CI surfaces (M5)` | `### Reporting and concurrency` |
| 572 | `### Real-execution tools (M6 part 1)` | `### Workspaces and files` |
| 685 | `### Bundled scripts (M6 part 2)` | `### Bundled scripts and the sandbox` |
| 870 | `### Developer experience (M7)` | `### Failure context and the run matrix` |
| 1027 | `### Per-call mock returns (issue #41)` | `### Per-call mock returns` |
| 1166 | `### Product runners (M9 part 1)` | `### Product runners` |
| 1285 | `### The product judge (M9 part 2)` | `### The product judge` |

**In-body references rewritten in `ARCHITECTURE.md`** — lines 67, 97, 148, 160, 178, 359,
363, 453, 455, 659, 671, 672, 863, 895, 896, 1194, 1256, 1367. The rewrites follow three
patterns:

- "the pre-M4 shape", "the layout it had before M4" → "the single-arm shape".
- "since M4", "an M4 addition", "an M5 invariant" → drop the clause, or say what the
  behaviour is without dating it.
- "a runner written against an earlier milestone", "a third-party runner written against
  M6 part 1" → "a runner written against an earlier version".

Where a sentence exists *only* to date a change ("Before M9, `--runner fake --model x` was
silently ignored"), keep the behavioural half and drop the date. Lines 671–672 describe the
`run_evals` parameter order using milestones as ordinals; rewrite them naming the
parameters instead.

**Doc pages** — `docs/gating.md:144,146`; `docs/comparative-evals.md:16,26,28`;
`docs/security.md:183`; `docs/contributing.md:28`. `gating.md`'s two lines describe JSON
report compatibility as "every M3 field … M4 only adds fields alongside them"; rewrite as
"every field a reader depended on before baselines existed still means what it meant".

**`docs/index.md:14`** — the `!!! info "Status: M9"` admonition is a 200-word feature dump
that restates the pages below it and goes stale every release. Replace with a short
stability note: the project is `0.x`, a minor release may change behaviour, pin what you
depend on, and a link to the roadmap.

**`README.md:281`** — "Status. Milestone 7." The fix is to drop the milestone, not to
update it to 9. State the stability instead, matching `docs/index.md`.

**Untouched here:** `docs/roadmap.md` (PR 4) and `CLAUDE.md` (PR 2).

**New test**, in `tests/test_docs.py`:

```
test_no_milestone_labels_outside_the_roadmap
```

Greps every published page plus `README.md` and `ARCHITECTURE.md` for `\bM[0-9]\b` and the
word "milestone", allowing `docs/roadmap.md`, `CHANGELOG.md` and `docs/superpowers/`.
`CLAUDE.md` is **not** in scope: it is an agent instruction file, not documentation, and it
still carries 24 milestone references until PR 2 rewrites it. The
test must not match legitimate text such as an `M1 Mac` reference; the allowlist is by
file, and any future exception is added with a comment saying why.

## PR 2 — `docs: split the invariants out of ARCHITECTURE.md`

**New page `docs/invariants.md`.** All 140 invariants, in nine `##` groups:

1. The core contract — `errored` is not `failed`, zero cases fail the gate, authoring
   errors abort, exit codes, `extra="forbid"`, UTF-8, the YAML loader, secrets from the
   environment, base URL versus key, the four framework-importing modules, cost lookup
   degrades, nothing scores a vacuous pass, hidden-data rubric entries, judge spend,
   mock tools accept anything, cassettes, the `skill_lens` spelling, `FakeRunner` copies.
2. Comparative evals.
3. Reporting and concurrency.
4. Release and supply chain — the current *CI surfaces* release invariants merged with the
   *Security checks* section, which is the same subject.
5. Workspaces and files.
6. Bundled scripts and the sandbox.
7. Failure context and the run matrix.
8. Mock tools — the current *Importing MCP tools*, *Tool libraries*, *Call arguments* and
   *Per-call mock returns* sections, which are one subject split four ways by when they
   shipped.
9. Product runners — product runners, the product judge, and mock tools under a product.

**Each invariant gets a `###` heading carrying its claim**, then a body in one shape: why
it exists, then what enforces it. Today each is a free-form bold-led paragraph with no
consistent order, so scanning them is slow. The heading also gives every invariant a stable
anchor to link to, which `CLAUDE.md`, the pull-request templates and other pages can use.

`mkdocs.yml`'s `toc` extension gains `toc_depth: 2` so the sidebar shows the nine groups
rather than 140 entries. Python-Markdown still assigns an `id` to every heading outside that
range, so `#the-delta-is-paired` resolves; what a `###` heading loses is the clickable
permalink icon that `permalink: true` adds. Confirm this by eye on the built site before
merging, because no test distinguishes a missing anchor from a missing icon.

**`ARCHITECTURE.md` drops to roughly 250 lines**: scope and non-goals, the three protocols,
the module map, the data flow, the core data models, extension points, testing tiers, and a
new short `## Invariants` section that says what an invariant is here, why the project keeps
them, and links to the page. That link must be an absolute URL — the file is inlined into
`docs/architecture.md` by a `pymdownx.snippets` directive, so a relative link passes
`tests/test_docs.py` and still breaks `mkdocs build --strict`.

**`mkdocs.yml`** gains `invariants.md` in the nav (its final position is set in PR 4).

**`CLAUDE.md`:**

- The `## What this is` section's ~70-line milestone paragraph shrinks to about six lines:
  what the tool is, that skills and cases are inputs, and links to `ARCHITECTURE.md`,
  `docs/invariants.md` and the roadmap.
- The invariant list stays, and its pointer changes from `ARCHITECTURE.md` to
  `docs/invariants.md`.
- Every bullet's bold lead is rewritten to match its `###` heading in `docs/invariants.md`
  byte for byte, and the bullets that currently merge two invariants are split. The count
  therefore goes from 119 to 140. This is what makes the drift test exact rather than
  fuzzy.

**New test**, `tests/test_invariants_sync.py`: every `###` heading under a `##` group in
`docs/invariants.md` is the bold lead of exactly one `CLAUDE.md` bullet, and every such
bullet matches exactly one heading. Failure messages name the specific headings or bullets
with no partner, in both directions, so the fix is obvious from the output alone.

## PR 3 — `docs: add a concepts page, a glossary and diagrams`

**Enable Mermaid.** `mkdocs.yml` gains:

```yaml
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
          format: !!python/name:pymdownx.superfences.fence_code_format
```

That `!!python/name:` tag is why Mermaid is banned today: `tests/test_docs.py` parses
`mkdocs.yml` with `skill_lens.yaml_loading.safe_load`, a `SafeLoader` subclass, which raises
`ConstructorError` on an unknown tag. The fix is local to the test — a loader subclass with
a multi-constructor that returns `None` for any tag it does not know, used at the three
`safe_load(MKDOCS_YML)` call sites (`_nav_pages`, `test_releasing_is_in_the_nav`, and the
nav check in `test_the_nav_has_no_missing_pages`). The project's own `safe_load` is not
changed: it guards user YAML, where an unknown tag should still be refused.

`.github/instructions/docs.instructions.md`'s ban is replaced by the rule behind it: a
`!!python/name:` tag in `mkdocs.yml` is allowed as long as the test's loader tolerates it.

**New page `docs/concepts.md`.** One section per concept. Each gives a plain-English
definition, one concrete example, and a link to the reference page. Order follows the
pipeline, so the page reads as a story rather than an alphabetical list:

skill and its bundle · eval case, suite and tags · task and mode (`loaded` versus
`offered`) · runner, and its three kinds (fake, framework, product) · mock tools and tool
libraries · workspace · evaluator, and its four kinds (assertion, trajectory, budget,
judge) · check, evidence and score · outcome: passed, failed, errored · arm: candidate and
baseline, and the delta · gate and exit codes.

It closes with a glossary table, one line per term, for readers who want a lookup rather
than a narrative.

**Five Mermaid diagrams:**

| Diagram | Page | Shows |
| --- | --- | --- |
| The pipeline | `concepts.md` | discovery → the matrix → run → evaluate → aggregate → report → gate |
| A case's anatomy | `concepts.md` | task and tools → runner → result → four evaluators → checks → outcome |
| The exit-code decision | `gating.md` | how a run becomes 0, 1 or 2 |
| The two arms | `comparative-evals.md` | candidate and baseline, pairing, and the delta |
| The three protocols as seams | `ARCHITECTURE.md` | where a framework, a scoring rule and a judge plug in |

Each must be legible in both the light and dark site themes, so no hard-coded colours —
Mermaid's default theme follows the page.

**`docs/index.md`** and **`README.md`** gain a Concepts row in their documentation tables.

## PR 4 — `docs: restructure the nav, README and roadmap`

**New nav:**

```
Home
Guides:    Getting started · Concepts · Writing evals · CI integration · Troubleshooting
Reference: Eval files · CLI · Configuration · Runners · Gating and exit codes · Comparative evals
Internals: Architecture · Invariants · Security · Releasing · Contributing
Roadmap
```

`writing-evals.md` moves out of Reference, where it does not belong — it is a 37-line guide
to installing an Agent Skill, not reference material.

**`docs/runners.md`** gains a `## Choosing a runner` section at the top: a table of runner ×
what it costs × whether it needs an API key × what it can measure (mock tools, trajectory,
tokens, `offered` mode). This information exists today, spread across `index.md`,
`getting-started.md`, `runners.md` and `configuration.md`, and a reader has to assemble it.

**`docs/eval-files.md`** gains a `## Choosing a check` section at the top: assertion versus
trajectory versus budget versus judge, in "use this when…" form. This is the most common
authoring question, and the judgment currently lives only inside the `writing-skill-evals`
Agent Skill.

**`README.md` shrinks from 292 lines to roughly 90**: what the tool is in three sentences,
one short eval-file example, install, the documentation table, four lines on contributing,
status, licence. Method: for each block removed, confirm the same content exists in
`docs/getting-started.md` before deleting it; if it does not, move it there first in the
same pull request.

**`docs/roadmap.md`** loses the nine "What MX shipped" sections. It gains:

- *What's shipped* — a table organised by capability, not by milestone.
- *What's next* and *Not planned* — built from the deferred items currently buried inside
  those sections: a per-skill `min_delta`, efficiency regression gates, an explicit
  `--baseline-ref <rev>`, flagging checks that fail in both arms, an HTML reporter
  (dropped as unnecessary), process and subinterpreter pools (deferred: the work is
  network-bound).
- A pointer to `CHANGELOG.md` for history.

A milestone table may stay here as a historical record; this is the one page where the
labels earn their place.

**New page `docs/troubleshooting.md`**, keyed by the message the user actually sees rather
than by the feature that emits it: the three exit codes; "no skills found"; "no cases
found"; no case name matched `--case`; a baseline that could not be resolved; `usage_note`
and a token limit that cannot be measured; `cost_note` and an unpriced model;
`NO_RESPONSE_SCRIPTED`; `UndeclaredTool`; the product preflight refusals; and
`script_sandbox = "required"` with no backend available.

**Sentence-length pass** on `docs/runners.md` and `docs/security.md` — split paragraphs
over roughly 120 words. No content removed.

**`.github/instructions/docs.instructions.md`** gains the nav-section rule and an updated
README rule.

## Verification

Every pull request runs:

```bash
uv run pytest tests/test_docs.py
uv sync --group docs && uv run mkdocs build --strict
uv run pytest
```

`tests/test_docs.py::test_relative_links_resolve` covers `docs/`, `README.md` and
`ARCHITECTURE.md`, so every link broken by a move is caught. `test_every_page_is_reachable_from_the_nav` and `test_the_nav_has_no_missing_pages` catch an orphan page or a nav entry
with no file. PR 3 additionally needs one rendered page checked by eye, because no test can
tell a Mermaid diagram that renders from one that does not.

`scripts/check_docs_updated.py` only fires when `src/skill_lens/` changes, so these
documentation-only pull requests pass the `docs-freshness` gate without the
`no-docs-needed` label.

## Accepted risks

**Anchors move on the published site.** A bookmark to
`…/architecture/#some-invariant` breaks in PR 2, because those anchors move to
`…/invariants/`. Every in-repository link is covered by a test; external bookmarks are not.
For a `0.x` project this is acceptable, and it is noted in PR 2's body rather than
mitigated with redirects.

**`CLAUDE.md` and `docs/invariants.md` hold the same rules twice.** Deliberate: the file is
an agent instruction file loaded at session start, not documentation, so "no duplication" is
the wrong rule for it. `tests/test_invariants_sync.py` is what makes the duplication safe.

**PR 1's grep test could match innocent text.** `\bM[0-9]\b` would match a hardware
reference such as "M1 Mac". The allowlist is by file, and any future exception is added
with a comment saying why.

## Out of scope, deliberately

- Any change under `src/skill_lens/`.
- Rewriting what an invariant says, as opposed to where it lives and what shape it takes.
- `docs/superpowers/` and `CHANGELOG.md`.
- Redirects for moved site anchors.
