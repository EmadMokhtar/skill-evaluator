# skill-eval M5 Part 2 — Design

**Date:** 2026-09-09
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§9 M5, §12), `2026-08-05-skill-eval-m5-design.md` (§10)

## 1. Scope

Part 1 made a run legible to CI. Part 2 makes the tool itself shippable: a merge to `main`
bumps the version, tags it, and publishes to PyPI without anyone typing a command, and a
maintainer can refresh the recorded provider traffic that the replay test tier depends on.

- **`release.yml`** — one workflow, three jobs: verify, release, publish.
- **`refresh-cassettes.yml`** — manual re-recording, delivered as a branch to review.
- **Version pinning made self-maintaining** — Commitizen rewrites every file that spells a
  version, so the action reference and the CLI it installs cannot drift apart.
- **`docs/releasing.md`** — the release process and the one-time setup this repository
  cannot perform for itself.

### This design supersedes §10 of the M5 design

The M5 design stated that the release job "needs a credential that can push to protected
`main` — `GITHUB_TOKEN` cannot bypass branch protection." That is false for this repository,
and the real constraint is a different one (§2.1). §10 also assumed three workflow files and
a tag-triggered publish, which §2.1 replaces. The M5 document gets a pointer to this one
rather than a silent correction, so the archive stays honest about what was believed when.

### Explicitly deferred

| Deferred | Milestone | Why |
| --- | --- | --- |
| TestPyPI rehearsal | never | Listed as optional in the parent design. It doubles the publish path to rehearse a step Trusted Publishing makes hard to get wrong, and nothing consumes the result. Same reasoning that dropped the HTML reporter from part 1. |
| A floating `v1` (or `v0`) tag | at 1.0 | Under SemVer a `0.x` minor release may break compatibility, so a moving `v0` would carry users across breaking changes without them noticing. Exact tags until 1.0; a moving major tag starts when the promise behind it becomes true. |
| Publishing from a hand-pushed tag | later | A consequence of §2.1, not a goal given up. A manual release is `cz bump` locally plus a re-run of the publish job. |
| Signing artifacts beyond PEP 740 attestations | later | Trusted Publishing already binds the artifact to the workflow that built it. Anything further needs a key to hold, which is the thing this design is avoiding. |
| Auto-merging the cassette-refresh branch | never | §2.4. The review is the point. |

## 2. Decisions

1. **One workflow, publish as a dependent job.** GitHub does not start new workflow runs from
   pushes made with `GITHUB_TOKEN` — a deliberate loop guard in the platform. A `publish.yml`
   listening on `push: tags` would therefore never fire for a tag pushed by the release job:
   the release would tag, and then silently publish nothing. Putting `publish` in the same run
   behind `needs:` removes the trigger entirely, and with it the need for a personal access
   token or a GitHub App. The cost is that a hand-pushed tag does not publish. That is
   acceptable: the automated path is the path.

2. **The release verifies before it bumps.** `ci.yml` runs on pushes to `main`, so a release
   triggered by the same push would race it — `cz bump` could tag and publish a commit whose
   tests are still running, or already red. A `verify` job runs ruff and the full offline
   suite, and `release` and `publish` sit behind `needs:`. The offline tier is 562 tests in
   about 13 seconds, so the duplicated work is cheap, and publishing is irreversible: PyPI
   does not allow re-uploading a version that already exists. `workflow_run` would avoid the
   duplication but always runs the default branch's copy of the workflow and does not check
   out the triggering commit, trading a clear failure mode for a subtle one.

3. **Commitizen owns version pinning, not a workflow step.** `version_files` rewrites the
   version inside the bump commit itself, so the tree at tag `vX.Y.Z` already installs
   `skill-eval[pydantic-ai]==X.Y.Z` and already documents `@vX.Y.Z`. A workflow step doing
   the same rewrite would have to run between `cz bump`'s commit and its tag, which means
   amending a commit that a tag already points at. Configuration beats sequencing.

4. **The cassette refresh delivers a branch, not a pull request.** A pull request opened with
   `GITHUB_TOKEN` gets no CI checks — the same loop guard as §2.1. For re-recorded cassettes
   the checks are the entire value of the review, so an unchecked pull request is worse than
   no pull request. The workflow pushes a branch and prints the compare URL; a human opening
   the pull request is what wakes CI up.

5. **A re-recording proves it can replay before anyone sees it.** The refresh job re-records,
   then runs the replay tier again with `--record-mode=none` against the fresh files. A
   recording that cannot replay is caught by the machine, not by a reviewer reading YAML.

6. **Permissions are granted per job, never at the top.** `permissions: {}` at workflow level
   and the narrowest grant on each job: `contents: read` to verify, `contents: write` to bump
   and push, `id-token: write` to publish. The publish job cannot write to the repository and
   the verify job cannot mint an identity token.

7. **No bump is a no-op, not a failure.** Commitizen has two exit codes meaning "nothing to
   release": `21` (`NoneIncrementExit` — commits exist, none of them warrant a version
   change) and `3` (`NoCommitsFoundError` — no commits since the last tag at all). Both set
   `bumped=false`; any other non-zero code fails the job. Treating only 21 as a no-op would
   turn a quiet merge into a red build. This is the same "capture the exit code before
   `set -e` discards it" pattern the action already uses.

8. **The artifact published is the artifact built.** `release` builds the distributions and
   uploads them; `publish` downloads and uploads them to PyPI. Rebuilding from the tag in the
   publish job would be a second build of the same source, and only one of the two would have
   been verified.

## 3. `release.yml`

```
on: push: branches: [main]
concurrency: {group: release, cancel-in-progress: false}
permissions: {}

verify    (contents: read)
  ruff check . / ruff format --check . / pytest

release   (contents: write) needs: verify
  checkout with fetch-depth: 0 and tags
  git identity = github-actions[bot]
  cz bump --yes            → capture exit code; 21 or 3 means no release
  outputs: bumped, version
  if bumped: push the commit and the tag; uv build; upload dist/

publish   (id-token: write) needs: release, if: bumped == 'true'
  environment: {name: pypi, url: https://pypi.org/p/skill-eval}
  download dist/ → pypa/gh-action-pypi-publish
```

`cancel-in-progress: false` because cancelling a release halfway is worse than queueing it:
the tag may already be pushed. `fetch-depth: 0` because `cz bump` reads the whole history and
the previous tag to compute the increment.

The `pypi` environment is what the PyPI pending publisher is bound to, and where a required
reviewer would go if a human gate before publishing is wanted later.

## 4. Version pinning

```toml
[tool.commitizen]
version_files = [
    "action.yml:skill-eval\\[pydantic-ai\\]==",
    "docs/ci.md:skill-evaluator@v",
    "README.md:skill-evaluator@v",
]
bump_message = "bump: version $current_version → $new_version [skip ci]"
```

Each entry is `path:pattern`; Commitizen replaces the current version string on lines the
pattern matches. Three files change as a result:

- **`action.yml`** — `install-spec`'s default becomes `skill-eval[pydantic-ai]==0.1.0`.
  Previously unpinned, so a user pinning the action to an exact ref still received whatever
  release was newest on PyPI. The action reference and the tool it installs now move together.
- **`README.md:148` and `docs/ci.md:35`** — `@v1` becomes an exact tag that exists. `v1` was
  never true: the project is at `0.1.0` and no `v1` tag has ever been cut.

`[skip ci]` is defence in depth. A `GITHUB_TOKEN` push starts no workflow already; the marker
means the loop guard is written down rather than depending on a platform behaviour that a
future credential change would silently remove.

**Until the first publish, the pinned version does not exist on PyPI.** That is not a
regression — there is no PyPI project at all today — and internal CI is unaffected because
`action-smoke` passes `install-spec: .`. `docs/releasing.md` says so plainly.

## 5. `refresh-cassettes.yml`

`workflow_dispatch` only, `contents: write`.

1. Re-record: `pytest tests/test_cassettes.py --record-mode=once` with `OPENAI_API_KEY`.
2. Prove replay: run the same tier again with `--record-mode=none`.
3. Refuse to leak: fail the job if the diff contains `sk-` or `Bearer `. Scrubbing already
   exists on both sides of the exchange in `tests/conftest.py`, so this is a second lock on a
   locked door — three lines against an invariant the whole replay tier rests on.
4. No changes recorded → no branch, and a summary that says so.
5. Otherwise commit to `chore/refresh-cassettes-<run_id>`, push, and write the compare URL to
   `$GITHUB_STEP_SUMMARY`.

## 6. One-time setup outside this repository

None of this can be done from a workflow file. `docs/releasing.md` carries the list.

| # | Setting | Current state |
| --- | --- | --- |
| 1 | Settings → Actions → General → Workflow permissions → **Read and write** | `read` — this caps every job, so `permissions: contents: write` would still resolve to read-only and the bump could not push |
| 2 | PyPI → Publishing → **pending publisher**: owner `EmadMokhtar`, repo `skill-evaluator`, workflow `release.yml`, environment `pypi` | absent; the name `skill-eval` is unregistered and free |
| 3 | The **`pypi`** GitHub Environment | absent (`copilot` and `github-pages` exist) |
| 4 | An **`OPENAI_API_KEY`** secret for the refresh workflow | the repository has no secrets |

Item 1 is the one that fails least obviously: the workflow is syntactically valid, the job
runs, and only the `git push` fails.

## 7. Testing

Every test is offline and free. The workflows themselves cannot be executed by the test
suite, so the tests assert the structure that carries the decisions above.

| Test | Asserts |
| --- | --- |
| `test_action.py` (extended) | the version pinned in `action.yml`'s `install-spec` equals `skill_eval.__version__` |
| `test_release_workflow.py` | every file spelling a version appears in `version_files`, so a new one cannot go stale unnoticed |
| `test_release_workflow.py` | `publish` has `needs: release`, is guarded on `bumped == 'true'`, requests `id-token: write`, and names the `pypi` environment |
| `test_release_workflow.py` | `release` and `publish` both sit behind `verify`; no job grants itself more than it needs |
| `test_docs.py` (extended) | `docs/releasing.md` lists all four setup items |

The third row is the load-bearing one: it is what stops a later edit from quietly removing
the gate that keeps an unverified commit off PyPI.

## 8. Documentation

| Change | Update |
| --- | --- |
| The release process, the four setup items, how to cut a release by hand | `docs/releasing.md` (new page + `nav:`) |
| Pinning guidance, the now-pinned `install-spec`, `@v1` → an exact tag | `docs/ci.md`, `README.md` |
| M5 complete | `docs/roadmap.md` |
| Milestone paragraph, the new invariants | `CLAUDE.md` |
| A pointer to this document | `2026-08-05-skill-eval-m5-design.md` §10 |

`ARCHITECTURE.md` is unchanged: workflows are not modules, and no protocol, invariant of the
core, or module boundary moves.

## 9. Invariants this milestone must not break

New:

1. **Nothing publishes that has not been verified in the same run.** `publish` reaches PyPI
   only through `needs:` on a green `verify`.
2. **A run with no releasable commit publishes nothing and fails nothing.** Exit codes 21 and
   3 from `cz bump` are no-ops, not errors.
3. **The version in `action.yml` always equals the package version.** Enforced by a test, not
   by remembering.
4. **Every file that spells a version is in `version_files`.** Enforced by a test, so adding a
   fourth file cannot leave it stale.
5. **No job holds a permission it does not use**, and `publish` can never write to the
   repository.
6. **No long-lived publishing credential exists.** Trusted Publishing only; a stored PyPI
   token would move the trust boundary from the workflow to a secret someone can copy.
7. **A cassette refresh proves the new recordings replay before a human sees them**, and never
   opens a pull request that CI has not run on.
8. **Re-recorded cassettes are checked for secrets before being pushed**, independently of the
   scrubbing that produced them.

Preserved: Conventional Commits are what `cz bump` reads, so the existing PR-title and
branch-commit checks in `ci.yml` become load-bearing for versioning rather than stylistic.
