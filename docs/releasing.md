# Releasing

Releases are automatic. Merging to `main` runs `.github/workflows/release.yml`, which
verifies the commit, works out the next version from the commit history, tags it, and
publishes to PyPI. Nobody types a release command.

## What happens on a merge to main

| Job | Does | Runs when |
| --- | --- | --- |
| `verify` | ruff (including the `S` security rules), format check, the full offline suite, `uv audit` on the lockfile | every push to `main` |
| `release` | `cz bump`, push the commit and its tag, verify the tag reached `origin`, write the release notes, `uv build`, export the SBOM, upload the artifacts — or, when nothing is released, check every release tag against PyPI and rebuild one that was stranded past its re-run window | `verify` passed |
| `publish` | download the artifact, upload to PyPI with attestations | a version was cut *and* its commit and tag reached `origin`, or a stranded tag was verified and rebuilt |
| `github-release` | download the three artifacts and hand them to `gh`: create the GitHub Release for the tag with the notes; attach the wheel, the sdist and the SBOM. No checkout, no install | `publish` succeeded |

A merge whose commits do not warrant a release is a no-op: `cz bump` exits `21` or `3`, the
job records "nothing to release" in its summary, and `publish` is skipped. So is a merge whose
run `main` outran — see [Two merges close together](#two-merges-close-together). Before a
no-op run ends it checks every release tag on `origin` against PyPI, and fails for one PyPI
does not have — or, when that tag's run can no longer be re-run, verifies and rebuilds it and
sends the result through `publish` — see
[A tag that never reached PyPI](#a-tag-that-never-reached-pypi).

`verify` re-runs the dependency audit on the commit being released rather than trusting the
pull request's green check: an advisory can be published between the merge and the tag, and
nothing publishes that `verify` did not pass. See [Security](security.md).

`release` also exports a CycloneDX SBOM from the lockfile `verify` audited —
`skill-lens-X.Y.Z.cdx.json`, for the runtime dependencies and every optional extra (`pydantic-ai`, `langchain`), not
the dev or docs groups — and uploads it as its own `sbom` artifact, apart from `dist`:
`publish` sends every file in `dist` to PyPI, which would reject an SBOM, and a rejected
file fails the upload after the tag is already pushed. `github-release` then attaches it to
the GitHub Release at
`https://github.com/EmadMokhtar/skill-evaluator/releases/download/vX.Y.Z/skill-lens-X.Y.Z.cdx.json`.
The release is created only after PyPI accepted the upload, so it can never advertise a
version `pip install` cannot find.

`release` builds with the audited backend, not a fresh one. `uv build` resolves
`[build-system] requires` at build time, outside the lockfile, so the artifact could otherwise
be produced by a `hatchling` nobody checked. The `build` dependency group mirrors those
requirements, which puts the backend in `uv.lock`; the job exports that group
(`uv export --frozen --only-group build`) as a constraint file and passes it to
`uv build --build-constraint`, so the backend and its own dependencies are exactly the versions
`verify` audited.

The version comes from the commit messages, so a Conventional Commit title is not a style
rule here — it is the input to versioning. `fix:` gives a patch, `feat:` a minor, and a `!`
or a `BREAKING CHANGE:` footer a breaking change. Because `major_version_zero = true`, a
breaking change stays inside `0.x` rather than promoting the project to `1.0.0`.

### The tag is annotated, and the job proves it is the one that landed

`git push --follow-tags` — what `release` uses to push the bump commit and its tag together —
pushes only **annotated** tags. Commitizen creates a lightweight tag unless told otherwise, so
`[tool.commitizen] annotated_tag = true` in `pyproject.toml` exists specifically to make
`--follow-tags` see it; without that setting, every release would push the bump commit to
`main` while its tag stayed on the ephemeral runner and vanished when the job ended.

The push is `--atomic`, so the commit and the tag are one update: if either ref is rejected —
`main` moved under the job, a branch rule refused it — neither lands, and there is no half-pushed
state to reason about afterwards. Whether that rejection is a failure depends on *why* `main`
moved, which is the next section.

### Two merges close together

Two pull requests merged a few minutes apart each start a release run, and the second can
finish before the first. The workflow's `concurrency` group only promises that a run in
progress is never *cancelled* (a cancelled release could leave a tag pushed and nothing built);
it does not keep one run alone from start to finish. GitHub checks the group when a *job*
starts, so a run that is between its `verify` and `release` jobs can be overtaken by the next
run, which then goes all the way to PyPI first. And even with the runs perfectly serialised,
a merge that lands while `verify` is running has already moved `main`.

Whichever way it happens, the earlier run's `release` job then finds itself on a commit that is
no longer the tip of `main`. Its `cz bump` rests on that commit, and the `--atomic` push of the
bump commit and tag is rejected as a non-fast-forward (the remote branch holds commits the job's
branch does not). Nothing landed: no tag, no bump commit.

The run is not a failure, and the job says so. The push step fetches `main`, and if this run's
commit is a strict ancestor of the new tip — `main` moved *past* it, rather than away from it —
it records `pushed=false`, writes "Nothing released: main moved on to `<sha>` while this run was
in progress" to the summary, and exits `0`; `publish` is skipped just as after a `cz bump` exit
`21` or `3`. That is safe because the later push has a run of its own, and its `cz bump` reads
**every commit since the last tag**, the earlier run's commit included: a `fix:` merged first
and a `feat:` merged three minutes later come out as one minor version that contains both. The
earlier run's commit is released; only the version it would have cut on its own never exists,
which is the same cosmetic gap as any other skipped number.

Any other rejection still fails the job with the push's own exit code: `main` unmoved but the
push refused (a branch rule), `main` rewritten to a history that does not contain this commit
(a force push), or a fetch that fails. In each of those no later run is known to carry the
commit, so silence would be the vacuous pass this project refuses everywhere else.

If the run for the later push should itself fail, the earlier commit is still not lost: the next
merge that warrants a release computes its increment from every commit since the last tag.

Because a silently dropped tag would otherwise be invisible — the `publish` job is reached
through `needs:`, not through the tag itself, so nothing downstream would notice — the
`release` job runs a `git ls-remote --tags origin` check right after a push that landed
(every step after the push, and `publish`, are gated on the push step's `pushed` output rather
than on the bump having been computed) and fails loudly if the tag is not there. That check tests the command's exit status separately from its output:
a failed lookup prints nothing, exactly like a missing tag, so treating "no output" as the only
signal would report a missing tag for one that is very likely present. If you ever see the
"is not on origin" message, it means the bump commit reached `main` but its tag did not: the
release is not reproducible from a tag, and the fix is to re-tag and re-push by hand rather
than re-run the job (which would try to bump again).

The check asks what the tag **points at**, not only whether it exists. `--follow-tags` sends
only the annotated tags origin does not already have, so a tag of the release's name that is
already on origin — pushed by hand, pointing at some other commit — is not a rejection: the
push skips it, the bump commit lands on `main`, and the tag now names code that was never
released. Existence alone would pass that. The step therefore lists both `refs/tags/vX.Y.Z`
and `refs/tags/vX.Y.Z^{}` (the commit an annotated tag points at; a lightweight tag has no
`^{}` line and its ref *is* the commit) and requires the target to equal the commit it just
pushed. If you see the "exists on origin but points at" message, the bump commit is on `main`
under a tag that means something else, and nothing in this workflow created that tag. Treat the
version as spent, exactly as in
[Recovering a failure after the tag but before the artifact](#recovering-a-failure-after-the-tag-but-before-the-artifact):
the next merge that warrants a release computes its increment from the commits after the tag,
`pip install` never sees `X.Y.Z`, and the tag is left where its author put it rather than moved.
Find out who pushed it; a hand-pushed tag in the release's namespace is the thing to stop.
Record the tag in `scripts/spent-tags.txt` with the reason, or the
[tag check](#a-tag-that-never-reached-pypi) reports it on every no-op run and every week.

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

You should not normally see it fire. The same property is asserted on every pull request by
`tests/test_release_config.py`, which replays the bump in memory the way Commitizen runs it —
every `(file, pattern)` pair in sorted order, the file rewritten in place after each — and
requires each pair to change at least one line. So a reformatted pin fails review rather than
costing a release. `--check-consistency` is the backstop for whatever reaches `main` anyway.

Replaying the rewrite, rather than checking each pattern against the original file, is what
catches a line that spells two versions. Commitizen writes the file back after every pattern,
so a line matched by two patterns has one version to give: the first pattern replaces it, and
the second finds the line already bumped. If that second pattern has no other line of its own,
the release aborts with exit `17` even though every pattern matched something. **Every version
spelling gets a line of its own.**

A third test requires every spelled version to *be* the current version. A branch cut before a
release and merged after it still spells the version it was cut from on the lines it added —
the bump on `main` rewrote only the lines that existed then — and `cz bump` would leave those
lines stale forever, because the pattern still matches the current lines beside them. That
test cannot run on the merged tree until the merge exists, so it fails in `verify` on `main`
rather than on the pull request; rebase a long-lived branch after a release to catch it earlier.

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

## The plugin has no version

`plugins/skill-lens/` is the `skill-lens` plugin (see
[Writing evals](writing-evals.md#installing-it)), and the release pipeline does nothing for it:
the agents install it from the tip of `main`, not from a release. None of its three manifests
spells a version, and none should. An agent treats a version as a cache key, so one tied to
the package version would hold an existing install on its old files whenever the skill changed
between releases. Without one, Claude Code keys each install on the commit, and an update
fetches whatever `main` holds.

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

### Refreshing locally

The same three steps run on a maintainer's machine through the `justfile`:

```bash
just refresh-cassettes                                        # all six recordings
just refresh-cassettes test_a_real_judge_grades_a_rubric_with_evidence   # one of them
```

Naming one test refreshes one cassette, which matters because every re-recording is a paid
provider call and `rewrite` re-records everything it touches.

The provider key never touches the shell. The recipe runs `pytest` through `op run`, the
1Password command-line tool, which resolves the reference in `.env.tpl` and hands the value to
the child process only — it is not in the environment before the command starts and it is
gone when the command exits. Typing `export OPENAI_API_KEY=...` instead puts the key into the
shell's history file in plain text, where it stays until someone deletes it.

`.env.tpl` holds a 1Password *address*, not a secret, so it is committed. The vault and item
names in it belong to one maintainer; another creates an "API Credential" item in their own
1Password and points the reference at it. `just replay-cassettes` and `just scan-cassettes`
run the two verification steps on their own, and `just` alone lists every recipe.

## A tag that never reached PyPI

`publish` runs inside the `pypi` environment, which can require a reviewer's approval. If
that approval never comes — the request is cancelled, or simply never answered — the run ends
with the bump commit and its tag on `main` and nothing on PyPI. Nothing retries it: every
later run's `cz bump` reads only the commits since that tag, so a merge with nothing
releasable publishes nothing, and the next release publishes the *next* version. That is how
`v0.21.0` was tagged on 2026-10-05 and PyPI went from `0.20.0` straight to `0.22.0`: for two
weeks installing `skill-lens` from PyPI gave `0.20.0` while `main`, the changelog and
`action.yml` said `0.21.0`, so the composite action pinned at that tag installed a version
PyPI did not have. CI did not notice, because `action-smoke` installs the checkout.

`scripts/check_tags_published.py` is what notices now. It lists every release tag on
`origin` with `git ls-remote` (the exit status checked apart from the output, since a failed
lookup prints nothing, exactly like no tags), derives each version from
`[tool.commitizen] tag_format`, fetches PyPI's release list for the project without any
credential, and fails for a tag whose version PyPI does not list with at least one file. A
release whose files were all deleted is listed but cannot be installed, so it counts as
missing. It runs in two places, spelled identically (`tests/test_security_checks.py` requires
it):

- in `release`, right after `cz bump`, **only when the run released nothing**. A release in
  flight is never failed over an older tag — the `publish` job that follows a bump is exactly
  what the check waits for.
- in the **Security** workflow, on its weekly schedule and on `workflow_dispatch`, so a tag
  stranded on a quiet week is found without a merge. Not on pull requests or pushes: a
  stranded tag says nothing about the change under review.

The failure names the tag and the run that should have published it. The tag's commit is the
bump commit, pushed on top of the commit the run was for, so the run is the `release.yml` run
whose head is the tag commit's parent, found through the GitHub API with the job's own
token (which is why both jobs hold `actions: read`). The message says when the run started
and whether it can still be re-run:

```
Tag v0.21.0 is on origin, but 0.21.0 is not on PyPI (skill-lens).
  The run that should have published it: https://github.com/EmadMokhtar/skill-evaluator/actions/runs/37301857121 (started 2026-10-05, failure).
  Re-run its failed jobs; GitHub allows that until 2026-11-04 (30 days after the run started).
  Otherwise record the tag in scripts/spent-tags.txt with the reason. See docs/releasing.md, "A tag that never reached PyPI".
```

**While the window is open, re-run the failed jobs of that run** from the Actions tab. That
re-runs `publish` — which downloads the `dist` artifact its own `release` job built and
`verify` tested, so nothing unverified ships, and whose attestation names the run that pushed
the tag — and then `github-release`. The approval is requested again; this time, answer it. Do
not start a fresh run or run `cz bump` by hand: a fresh run finds the tag already on `HEAD`
and reports nothing to release, exactly as
[There is no manual path to PyPI](#there-is-no-manual-path-to-pypi) describes. Afterwards,
run the Security workflow by hand (**Actions → Security → Run workflow**) to see the check
go green.

**Once the window has closed, or when no release run pushed the tag, the release workflow
recovers it on its own.** The message changes accordingly:

```
  Re-running it is no longer possible: GitHub's 30-day window closed on 2026-11-04. The next merge to main that releases nothing rebuilds it from its commit and publishes it through the release workflow.
```

Any merge to `main` that releases nothing — a `docs:` or `chore:` commit will do — starts a
run whose `release` job, finding nothing to bump, runs the check with `RECOVERY_PLAN` set.
The check then hands that one tag to the next step instead of failing (its line reads "This
run rebuilds it from its commit and publishes it"), and the **Verify and build a stranded
tag** step does the rest:

1. It fetches the tag from `origin` and requires it to point at the commit the check saw. A
   tag that moved since is refused. The tag is only ever read: nothing in the step creates,
   moves or deletes one.
2. It requires that commit to be on `main` as this run sees it (an ancestor of the run's
   commit). A tag at a branch, or at a commit `main` was rewritten away from, is refused.
3. It adds the commit as a worktree and runs **the same commands `verify` runs** there —
   `uv sync --all-extras --dev`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run pytest`, `uv audit --preview-features audit --locked` — spelled identically and in
   the same order, which `tests/test_release_workflow.py` requires. The tag's own run passed
   them once; this run passes them again, on this tree, before a byte of it is built. That is
   how the rule that nothing publishes unverified in the same run is kept for a commit this
   run's `verify` job did not see. (The design, and why the suite runs inside `release` rather
   than in a job ahead of `verify`, is in
   `docs/superpowers/specs/2026-10-20-skill-lens-stranded-tag-recovery-design.md`.)
4. It requires `cz version --project` in that tree to print the version being recovered, so a
   tag at the wrong commit cannot build — and upload — some other version.
5. It writes the release notes (`cz changelog <version> --dry-run`, from the same commits
   that chose the version), builds with the audited backend from the tag's own lockfile
   (`uv export --frozen --only-group build` → `uv build --build-constraint`), and exports the
   SBOM, exactly as the bump path does, into the places the three artifact uploads read.
6. It sets `recovered=true` and the version, so `release` reports `bumped=true` and `publish`
   runs unchanged — inside the `pypi` environment, so the approval is requested. **Answer it.**
   `github-release` then creates the release for the tag, which `--verify-tag` finds on
   `origin` where it has been all along.

One tag per run, the oldest first; a second stranded tag is reported as waiting and does not
fail the run. Nothing before the upload is irreversible — no tag pushed, no commit — so a
recovery that fails spends nothing: the run goes red with the reason, and the next
no-release merge tries again. If `publish` is again left unapproved, the same.

Some tags are still **spent**, and the check says so rather than recovering them:

- the run on the tag's parent commit *succeeded* — the tag was pushed by hand on top of a
  released tip, or PyPI no longer has a version that run published. A hand-pushed tag in the
  release's namespace is the thing to stop, not to publish;
- PyPI lists the version with no files. PyPI refuses a filename it has seen before, deleted
  or not, so the version can never be rebuilt;
- the recovery fails for a reason that will not change: the tag's lockfile now carries a
  known vulnerability (the audit fails, as it would for a fresh release), or its tree no longer
  passes its own suite under today's tooling.

A finding the run cannot resolve — including a tag still within its window, or a lookup of
the run that failed — fails the check as before, and then **no recovery runs**: a red check
is never followed by a publish. So an older stranded tag can wait behind a newer one that is
still re-runnable; the newer one's window closes within 30 days, after which both are
recovered in turn. The weekly Security run only ever reports; it has no `publish` to hand a
tag to.

For a spent tag, record it in `scripts/spent-tags.txt`, one per line with the date and the
reason after a `#`:

```
v0.21.0  # 2026-11-05: pushed by hand on top of a released tip; the version is spent
```

The check accepts a listed tag and says so in its output. The file only grows, by a reviewed
commit; an entry with no reason is refused (exit `2`), and an entry for a tag PyPI *does* have
fails the check with "Remove the entry": the list exists to acknowledge a gap, never to hide a
release — and never to skip a recovery that would have worked.

Two things the check never does. It never re-uploads a version PyPI lists — PyPI refuses that
anyway, and the check only reads. And it never turns an outage into a finding: a PyPI or
`origin` it cannot read (three attempts for PyPI) is reported as "Could not check … Nothing
was verified", exit `2`, so a flaky network is a red run that says so rather than either a
false "missing" or a silent pass. So is a PyPI response without its release list, and an
`origin` that lists no release tag at all while PyPI has releases — a wrong or empty remote,
not a clean bill. Exit `1` is a finding; exit `0` is every tag accounted for.

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
depending on which push you ask about. The [tag check](#a-tag-that-never-reached-pypi) reports
it, and since the run on its parent commit — `main`'s previous tip — succeeded, it is spent
rather than recovered: record it in `scripts/spent-tags.txt`.

### Recovering a failed GitHub Release

`github-release` runs after `publish`, so a failure here means PyPI already has the version
and only the GitHub Release or its assets are missing. Re-run the job: it skips creation
when the release already exists and uploads the assets with `--clobber`, so a partial run
converges rather than failing on "already exists". Nothing in it can affect PyPI.

The job holds `contents: write`, so it is kept to downloading artifacts and running `gh`:
no checkout, no `uv sync`, no build. The notes, the SBOM and the distributions are all
produced in `release` and handed on as artifacts, so no third-party code runs under the
token that can write releases.

### Recovering a failed publish

The one recoverable failure is a run whose `release` job fully succeeded — tests passed, the tag
reached origin, the `dist` artifact was built and uploaded — where only the final "Publish to
PyPI" step failed (a PyPI outage, an expired trusted-publisher binding, a transient network
error). Open that run in the Actions tab and re-run the `publish` job: it re-downloads the
artifact its own `release` job already built and retries the upload; nothing upstream of it runs
again. The same re-run recovers a `publish` that was cancelled while waiting for the
environment's approval, within GitHub's 30-day window — the case
[A tag that never reached PyPI](#a-tag-that-never-reached-pypi) exists to report. The publish step sets `skip-existing: true`, so a retry after a *partial* upload — the
wheel landed, the sdist did not — uploads what is missing instead of dying on "File already
exists". Starting a fresh run instead, or re-running `release`, is not a substitute: a fresh
run's `release` job faces the same already-tagged `HEAD` as the local `cz bump` case above and
reports nothing to release.

### Recovering a failure *after* the tag but *before* the artifact

There is one state with no re-run at all. If anything between the tag and the artifacts
fails once the tag has already reached origin — writing the release notes, `uv build`, the
SBOM export, or any of the three artifact uploads — `main` carries a permanent tag `vX.Y.Z`, `publish` was never
eligible (`needs.release.outputs.bumped` never reached it), so there is no `publish` job to
re-run, and a fresh run's `cz bump` exits `3` — nothing to release — for the same
already-tagged-`HEAD` reason as above. Version `X.Y.Z` is spent: it exists as a tag and will
never exist on PyPI.

This is self-healing, and the right response is to fix the cause and move on. The next merge
that warrants a release computes its increment from the commits after `vX.Y.Z` and publishes
`X.Y.Z+1` (or whatever the history warrants) normally. The
[tag check](#a-tag-that-never-reached-pypi) reports `vX.Y.Z` meanwhile. Its "re-run its
failed jobs" advice does not help here — re-running `release` runs `cz bump` again on an
already-tagged tree, and `publish` never existed to re-run — but the tag's tree is complete,
so once the 30-day window closes the next no-release merge rebuilds it from the tag and
publishes `X.Y.Z` after all. Until then the version sequence has a gap, which is a cosmetic
cost, not a broken repository. Do not try to
reclaim `vX.Y.Z` by deleting and re-pushing the tag: a tag that once existed publicly and later
points at different code is the failure mode this whole section exists to avoid.
