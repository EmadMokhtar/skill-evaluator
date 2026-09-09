# Releasing

Releases are automatic. Merging to `main` runs `.github/workflows/release.yml`, which
verifies the commit, works out the next version from the commit history, tags it, and
publishes to PyPI. Nobody types a release command.

## What happens on a merge to main

| Job | Does | Runs when |
| --- | --- | --- |
| `verify` | ruff, format check, the full offline suite | every push to `main` |
| `release` | `cz bump`, push the commit and its tag, verify the tag reached `origin`, `uv build`, upload the artifact | `verify` passed |
| `publish` | download the artifact, upload to PyPI | a version was actually cut |

A merge whose commits do not warrant a release is a no-op: `cz bump` exits `21` or `3`, the
job records "nothing to release" in its summary, and `publish` is skipped.

The version comes from the commit messages, so a Conventional Commit title is not a style
rule here — it is the input to versioning. `fix:` gives a patch, `feat:` a minor, and a `!`
or a `BREAKING CHANGE:` footer a breaking change. Because `major_version_zero = true`, a
breaking change stays inside `0.x` rather than promoting the project to `1.0.0`.

### The tag is annotated, and the job proves it landed

`git push --follow-tags` — what `release` uses to push the bump commit and its tag together —
pushes only **annotated** tags. Commitizen creates a lightweight tag unless told otherwise, so
`[tool.commitizen] annotated_tag = true` in `pyproject.toml` exists specifically to make
`--follow-tags` see it; without that setting, every release would push the bump commit to
`main` while its tag stayed on the ephemeral runner and vanished when the job ended.

The push is `--atomic`, so the commit and the tag are one update: if either ref is rejected —
`main` moved under the job, the tag already exists — neither lands, and there is no half-pushed
state to reason about afterwards.

Because a silently dropped tag would otherwise be invisible — the `publish` job is reached
through `needs:`, not through the tag itself, so nothing downstream would notice — the
`release` job runs a `git ls-remote --tags origin` check right after the push and fails loudly
if the tag is not there. That check tests the command's exit status separately from its output:
a failed lookup prints nothing, exactly like a missing tag, so treating "no output" as the only
signal would report a missing tag for one that is very likely present. If you ever see the
"is not on origin" message, it means the bump commit reached `main` but its tag did not: the
release is not reproducible from a tag, and the fix is to re-tag and re-push by hand rather
than re-run the job (which would try to bump again).

### A pin that stops matching aborts the bump

`cz bump` rewrites `version_files` line by line, gated on each entry's regex, and a file where
nothing matched is written back unchanged with no error. Reformat `action.yml`'s pinned
`install-spec` and the next release would tag a tree still pinning the previous version — the
exact drift the pin exists to prevent — with nothing to report it, because the bump commit
carries `[skip ci]` and the bumped tree is never tested.

The workflow therefore runs `cz bump --yes --check-consistency`. That flag turns "matched
nothing" into a hard failure (exit `17`), raised before the new version is written, before
`git add`, and before the tag — so an inconsistent tree stops the release with nothing pushed.
Commitizen reads it from the command line only, never from `[tool.commitizen]`, which is why it
lives in the workflow rather than in `pyproject.toml`.

## One-time setup

None of this can live in a workflow file. A maintainer or a fork needs all four; the current
state of this repository, verified directly rather than assumed, is:

1. **Settings → Actions → General → Workflow permissions → Read and write.** ✅ Already set.
   This setting is a ceiling: with it at read-only, a job requesting `contents: write` silently
   gets read-only, and the failure only shows up much later, at the final `git push`.
2. **A `pypi` GitHub Environment.** ✅ Already created. The `publish` job runs inside it, and
   the PyPI publisher is bound to its name. Add required reviewers to make every publish wait
   for approval.
3. **A pending publisher on PyPI** (Publishing → Add a new pending publisher → GitHub). ✅
   Already registered: project `skill-lens`, owner `EmadMokhtar`, repository
   `skill-evaluator`, workflow `release.yml`, environment `pypi`. This is Trusted Publishing:
   PyPI accepts the upload because the job proves its identity with a short-lived signed
   token, so no API token exists to store, leak or rotate.
4. **An `OPENAI_API_KEY` secret**, used only by the manual cassette refresh below. ❌ **Not
   set.** Nothing else needs it — the release pipeline above does not touch it — so this is
   outstanding without blocking a release.

A pending publisher does **not** reserve the name: PyPI states this explicitly. Until the
first release actually creates the project, anyone else may still register `skill-lens`.

## Pinning the action

`cz bump` rewrites the version wherever it is spelled — `action.yml`'s `install-spec` default
and the documented `uses:` tag — inside the bump commit itself. The tree at tag `vX.Y.Z`
therefore installs exactly `X.Y.Z`, and the action reference cannot drift from the tool.

Pin an exact tag. There is no floating `v0` tag: under SemVer a `0.x` minor release may
change behaviour, so a moving tag would carry you across a breaking change without warning.
A moving major tag starts at 1.0, when the promise behind it becomes true.

## Refreshing the cassettes

The replay test tier runs against recorded provider traffic. When the provider's responses
change, run **Actions → Refresh cassettes → Run workflow**. The job:

1. Re-records with `pytest tests/test_cassettes.py --record-mode=rewrite`. This must be
   `rewrite`, not `once`: `once` only fills in a cassette that does not exist yet, and
   write-protects one the moment it is loaded — a matching request replays instead of hitting
   the provider, and a request that no longer matches raises
   `CannotOverwriteExistingCassetteException` instead of being refreshed. A workflow whose
   whole purpose is to refresh *existing* recordings would be a no-op under `once`.
2. Proves the new recordings replay, with `--record-mode=none` — a recording that cannot be
   replayed is worthless, and a reviewer reading the YAML diff would not notice.
3. **Stages the recordings** (`git add -A -- tests/cassettes`) before either check below runs.
   `git diff` cannot see an untracked file, and a brand-new cassette — exactly what a
   re-record can produce — is untracked until something stages it. Both checks that follow
   compare against the index (`git diff --cached`), not the working tree, for that reason.
4. Refuses to push if a credential appears in the staged diff.
5. Pushes a branch for review.

Open the pull request yourself from the link in the job summary. A pull request opened by the
workflow's own token would carry no CI checks, and on a cassette refresh those checks are the
review.

## There is no manual path to PyPI

Do not run `cz bump` locally to release. It will tag a version without publishing it, and the
tag it leaves behind cannot be reused: it looks like a real release happened, but nothing
reached PyPI.

Here is why. `publish` only runs as part of the same workflow run whose `release` job just
built and uploaded the `dist` artifact it downloads — it is reached through `needs: release`,
gated on `needs.release.outputs.bumped == 'true'`, not through a tag push. That is deliberate:
a tag pushed with `GITHUB_TOKEN` starts no new workflow run at all, so a tag-triggered `publish`
job could never fire on an automated release either, and chaining the jobs with `needs:` is
what makes it fire at all.

Running `cz bump` locally and pushing the commit and its tag starts **no run at all**:
`bump_message` ends in `[skip ci]`, and GitHub creates no workflow run for a push whose head
commit carries that marker. Nothing downstream ever hears about the version you just cut.

And a run started some other way would not save you either. Its `release` job would find the
tag it wants to create already sitting on `HEAD`, since you just pushed it; `cz bump` sees no
commit since that tag warranting a release, exits `3` or `21` (both a no-op, not an error), and
the run records "nothing to release": no build, no artifact, no `publish`. Either way you are
left with a real, permanent tag and no published package behind it, which is worse than doing
nothing — the tag cannot simply be re-cut, since `vX.Y.Z` would then mean two different things
depending on which push you ask about.

### Recovering a failed publish

The one recoverable failure is a run whose `release` job fully succeeded — tests passed, the tag
reached origin, the `dist` artifact was built and uploaded — where only the final "Publish to
PyPI" step failed (a PyPI outage, an expired trusted-publisher binding, a transient network
error). Open that run in the Actions tab and re-run the `publish` job: it re-downloads the
artifact its own `release` job already built and retries the upload; nothing upstream of it runs
again. The publish step sets `skip-existing: true`, so a retry after a *partial* upload — the
wheel landed, the sdist did not — uploads what is missing instead of dying on "File already
exists". Starting a fresh run instead, or re-running `release`, is not a substitute: a fresh
run's `release` job faces the same already-tagged `HEAD` as the local `cz bump` case above and
reports nothing to release.

### Recovering a failure *after* the tag but *before* the artifact

There is one state with no re-run at all. If `uv build` or the artifact upload fails once the
tag has already reached origin, `main` carries a permanent tag `vX.Y.Z`, `publish` was never
eligible (`needs.release.outputs.bumped` never reached it), so there is no `publish` job to
re-run, and a fresh run's `cz bump` exits `3` — nothing to release — for the same
already-tagged-`HEAD` reason as above. Version `X.Y.Z` is spent: it exists as a tag and will
never exist on PyPI.

This is self-healing, and the right response is to fix the cause and move on. The next merge
that warrants a release computes its increment from the commits after `vX.Y.Z` and publishes
`X.Y.Z+1` (or whatever the history warrants) normally. The only lasting trace is a gap in the
published version sequence, which is a cosmetic cost, not a broken repository. Do not try to
reclaim `vX.Y.Z` by deleting and re-pushing the tag: a tag that once existed publicly and later
points at different code is the failure mode this whole section exists to avoid.
