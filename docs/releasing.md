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

Because a silently dropped tag would otherwise be invisible — the `publish` job is reached
through `needs:`, not through the tag itself, so nothing downstream would notice — the
`release` job runs a `git ls-remote --tags origin` check right after the push and fails loudly
if the tag is not there. If you ever see this step fail, it means the bump commit reached
`main` but its tag did not: the release is not reproducible from a tag, and the fix is to
re-tag and re-push by hand rather than re-run the job (which would try to bump again).

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

## Releasing by hand

Rarely needed. `uv run cz bump` locally, push the commit and tag, then re-run the `publish`
job. Note that pushing a tag does not by itself publish: the `publish` job is reached through
`needs:`, not through a tag trigger — GitHub does not start new workflow runs from a
`GITHUB_TOKEN` push in the first place, so a tag-triggered publish job would never fire on an
automated release either.
