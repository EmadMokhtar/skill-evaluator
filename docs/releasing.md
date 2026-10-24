# Releasing

Releases are automatic. Merging to `main` runs `.github/workflows/release.yml`, which
verifies the commit, works out the next version from the commit history, tags it, and
publishes to PyPI. Nobody types a release command.

## What happens on a merge to main

The same pipeline also runs when you start it by hand — see
[Starting a release by hand](#starting-a-release-by-hand).

| Job | Does | Runs when |
| --- | --- | --- |
| `verify` | ruff (including the `S` security rules), format check, the full offline suite, `uv audit` on the lockfile | every push to `main` |
| `release` | check every release tag against PyPI, then `cz bump`, push the commit and its tag, verify the tag reached `origin`, write the release notes, `uv build`, export the SBOM, upload the artifacts — or, instead of the bump, rebuild a tag that was stranded past its re-run window | `verify` passed |
| `publish` | download the artifact, upload to PyPI with attestations | a version was cut *and* its commit and tag reached `origin`, or a stranded tag was verified and rebuilt |
| `github-release` | download the three artifacts and hand them to `gh`: create the GitHub Release for the tag with the notes; attach the wheel, the sdist and the SBOM. No checkout, no install | `publish` succeeded |

A merge whose commits do not warrant a release is a no-op: `cz bump` exits `21` or `3`, the
job records "nothing to release" in its summary, and `publish` is skipped. So is a merge whose
run `main` outran — see [Two merges close together](#two-merges-close-together). Before every
bump the job checks every release tag on `origin` against PyPI, and fails for one PyPI does not
have, so a release never goes out past a missing one and leaves a gap in the versions users
see. When that tag's run can no longer be re-run, the job instead verifies and rebuilds it,
sends the result through `publish`, and cuts no version of its own — see
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

Two pull requests merged a few minutes apart each start a release run. The workflow's
`concurrency` group holds a whole run, `publish`'s wait for approval included, so two runs never
overlap, and a run in progress is never *cancelled* (a cancelled release could leave a tag
pushed and nothing built).

The group keeps only **one** run waiting, though. When a newer merge queues, GitHub cancels the
run that was waiting, whatever `cancel-in-progress` says. So a release whose `publish` waits
hours for its approval leaves a row of cancelled runs behind it, one for every merge in that
time but the last. That loses nothing. The last run's `cz bump` reads **every commit since the
last tag**, the cancelled runs' commits included, and releases them all as one version. A
cancelled release run needs no action. **Do not re-run it.** It releases nothing, and while a
newer run is waiting it does harm: the re-run joins the group as the waiting run, so GitHub
cancels the newer one, whose commits then wait for the next merge or a run
[started by hand](#starting-a-release-by-hand) — see
[A run that starts behind `main`](#a-run-that-starts-behind-main).

A run that has started can still find `main` moved: a merge that lands while it runs moves
`main` all the same. The run's `release` job then finds itself on a commit that is no longer the
tip of `main`. When the merge landed before the job started, the tip step finds it (see the
same section). When it landed after, the job's `cz bump` rests on the old commit, and the
`--atomic` push of the bump commit and tag is rejected as a non-fast-forward (the remote branch
holds commits the job's branch does not). Nothing landed: no tag, no bump commit.

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
than re-run the job: a re-run finds `main` past its commit and releases nothing, and its green
conclusion would make the tag check read the re-tagged version as published, and so as
spent.

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
`pip install` never sees `X.Y.Z`, and the tag is left where its author put it rather than moved.
Find out who pushed it; a hand-pushed tag in the release's namespace is the thing to stop.
Record the tag in `scripts/spent-tags.txt` with the reason. Until you do, the
[tag check](#a-tag-that-never-reached-pypi) fails every release run on it, and every week;
after that, the next merge that warrants a release computes its increment from the commits
after the tag and goes out normally.

### A run that starts behind `main`

A re-run checks out the run's own commit again, not `main`. Re-running a run that a newer run
has already released — one GitHub cancelled while it waited, or one that finished — would
otherwise cut that version a second time. Its `cz bump` reads the old commit's `pyproject.toml`
and computes the version the newer run already tagged. The tag is already in the job's clone
(it fetches every tag), so `cz bump` fails on `fatal: tag 'v0.25.0' already exists` with exit
`7`, before the push step can call it a no-op.

So the `release` job checks before the bump. Its tip step fetches `main`. When this run's commit
is a strict ancestor of the tip, it writes "Nothing released: main is at `<sha>`, past this run's
commit `<sha>`" to the summary and skips the bump. If no run on `main`'s tip has run or is
waiting — the case where a re-run cancelled it — start one by hand. With no `bumped` written, the tag check, the
push, the recover step and `publish` are skipped too, and the run is green. This is the push
step's test, made earlier, and it refuses the same things: `main` at this commit goes on to the
bump; `main` rewritten to a history without it also goes on, and the push step fails it; a
fetch that fails fails the job. The same step also catches a merge that landed after this run's
`verify` started and before its `release` job did, which would otherwise cut a version only to
have its push rejected.

`verify` still runs first, on the old commit, and can still fail there. Those are that commit's
own tests, and a bug a later commit fixed is still in it. A red `verify` on a re-run of an older
run says that commit was broken, not that a release is.

One run behind `main` is not a no-op: **the run that cut a release**, re-run. Its own bump
commit is the first commit after its commit on `main` and carries a release tag, and the tip
step fails it instead: "This run already cut `vX.Y.Z` at `<sha>`, right after its commit `<sha>`, so
its release job cannot release anything again." A green re-run would change that run's
conclusion to `success`, and the [tag check](#a-tag-that-never-reached-pypi) reads a run that
succeeded as one that published its version. A tag PyPI lacks would then be called spent —
never re-run, never recovered — and, since the check runs before every bump, every later
release would stop on it until someone recorded it in `scripts/spent-tags.txt`. Failing keeps
the run's conclusion `failure`, so the tag stays something to re-run or to recover.

A release tag here is a tag spelled with the `[tool.commitizen] tag_format` prefix and then a
digit (`v[0-9]*`), on whatever commit it sits. Another tag on that commit, such as a
`docs-snapshot` marker, is not one, and the re-run is the ordinary no-op. A tag that only looks
like a release, pushed by hand, still fails it: the tag check finds a release tag's run through
the tag commit's parent, whatever the commit is.

To retry a `publish` that failed or never got its approval, re-run that run's *failed jobs*, as
[Recovering a failed publish](#recovering-a-failed-publish) says, not all of them: when its
`release` job succeeded, that re-runs `publish` alone. Re-running all jobs re-runs `release`
too, which the tip step fails as above.

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

- in `release`, right **before** `cz bump`, on every run that is on `main`'s tip. A tag PyPI
  does not have fails the job before a version is cut: with `0.21.0` tagged and never
  published, `0.22.0` does not go out, so PyPI never jumps from `0.20.0` to `0.22.0`. It
  cannot fail over a release still on its way to PyPI: this run's own tag does not exist yet,
  and the concurrency group keeps every other run, its `publish` included, from running at the
  same time. A PyPI that cannot be read also stops the release (exit `2`, "nothing verified"),
  since a missing tag cannot be ruled out; re-run the job once PyPI answers.
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
  If its `release` job succeeded and only `publish` failed or was never approved, re-run its failed jobs: that retries `publish`. GitHub allows it until 2026-11-04 (30 days after the run started).
  If its `release` job failed after pushing the tag, re-running it cannot publish anything. The release workflow rebuilds the tag from its commit once that window closes.
  Otherwise record the tag in scripts/spent-tags.txt with the reason. See docs/releasing.md, "A tag that never reached PyPI".
```

The check does not look up which job failed; the run's page shows it, and it decides what a
re-run can do. If `release` failed after pushing the tag, no re-run can publish: see
[Recovering a failure after the tag but before the artifact](#recovering-a-failure-after-the-tag-but-before-the-artifact).

**While the window is open, and `release` succeeded, re-run the failed jobs of that run** from
the Actions tab. That re-runs `publish` — which downloads the `dist` artifact its own `release` job built and
`verify` tested, so nothing unverified ships, and whose attestation names the run that pushed
the tag — and then `github-release`. The approval is requested again; this time, answer it. Do
not start a fresh run or run `cz bump` by hand: a fresh run's tag check fails on this same tag,
and a local bump makes a second stranded tag, as
[There is no manual path to PyPI](#there-is-no-manual-path-to-pypi) describes. Afterwards,
run the Security workflow by hand (**Actions → Security → Run workflow**) to see the check
go green. Until then every release run fails at the check, so nothing newer goes out past the
missing version. The commits merged meanwhile are released by the next run once the tag is on
PyPI: merge again, or [start a release by hand](#starting-a-release-by-hand).

**Once the window has closed, or when no release run pushed the tag, the release workflow
recovers it on its own.** The message changes accordingly:

```
  Re-running it is no longer possible: GitHub's 30-day window closed on 2026-11-04. The next run of the release workflow on main rebuilds it from its commit and publishes it, before it releases anything else.
```

The next run of the release workflow on `main` — any merge, or a run
[started by hand](#starting-a-release-by-hand) — runs the check with `RECOVERY_PLAN` set. The
check then hands that one tag on instead of failing (its line reads "This run rebuilds it from
its commit and publishes it"). The bump sees the plan and cuts no version: one run publishes
one version, so the stranded one goes first and this run's own commits wait for the next run,
which finds every tag on PyPI and releases them. The **Verify and build a stranded tag** step
does the rest:

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
recovery that fails spends nothing: the run goes red with the reason, and the next run
tries again. If `publish` is again left unapproved, the same.

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

## Starting a release by hand

**Actions → Release → Run workflow**, with `main` selected, runs the same pipeline a merge
does: `verify`, the tag check, the bump, and `publish` behind the same approval. It is a
`workflow_dispatch` trigger (a manual start of a workflow from the Actions tab or the API).
Its one input, `increment`, becomes a `cz bump` flag:

| Value | Becomes |
| --- | --- |
| `auto` (default) | no flag: the commits choose the increment, as on a merge |
| `PATCH`, `MINOR` | `--increment PATCH` or `--increment MINOR`, whatever the commit types say |

Use it to release the commits a [stranded-tag recovery](#a-tag-that-never-reached-pypi) held
back, without waiting for another merge, or to force a patch or a minor where the commit types
would choose otherwise — a release of `docs:`-only commits, say. With `auto` it behaves exactly
as a merge does, so it is also a way to run the tag check and a recovery on demand.

The rules:

- **`main` only.** The workflow can be started on any branch, but the push step pushes
  `HEAD:main`, so a branch that contains `main` would move `main` onto commits no pull request
  reviewed. The tip step refuses any other ref before anything is fetched: "A release is cut
  from main only, and this run is on `refs/heads/<branch>`."
- **No `MAJOR`.** `[tool.commitizen] major_version_zero = true` keeps the project in `0.x` on
  purpose. Leaving `0.x` is a change to that setting, in a pull request of its own, not a
  button.
- **The value is checked.** GitHub checks a `choice` input against its options, and the bump
  step checks again: any other value exits `2` with nothing bumped, and never reaches the
  command line. The input reaches the script as an environment variable, never as a `${{ }}`
  expression inside it.
- **A forced increment needs at least one commit.** It overrides what the commit types say,
  but the changelog the bump writes still needs a commit since the last tag. With none,
  Commitizen exits `3` and the run is the ordinary "nothing to release" no-op.
- **An increment that cannot be applied is announced.** A `PATCH` or `MINOR` run that turns
  out to be behind `main`, or that a [recovery](#a-tag-that-never-reached-pypi) goes ahead of,
  bumps nothing and stays green. It shows an "Increment not applied" warning on the run, and the
  summary says "The requested increment `MINOR` was not applied". Start the workflow again on
  `main`; otherwise the next run bumps as its commits say.
- **No pre-release.** Commitizen finishes a pre-release on the next bump of any kind, so
  after a `0.26.0rc0` the next merge — a Dependabot update would do — publishes `0.26.0`
  before anyone has tried the rc. Meanwhile `version_files` would point the docs site and
  `action.yml` at the rc. A pre-release input would need a rule that stops ordinary merges
  from bumping past it first.

A manual run takes its turn in the same concurrency group as merges, so it never overlaps a
release, and a merge that lands while it waits cancels it like any other waiting run.

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

And a run started afterwards would not save you either. Its [tag
check](#a-tag-that-never-reached-pypi) finds your tag on `origin` and not on PyPI, and since
the run on its parent commit — `main`'s previous tip — succeeded, the tag is spent rather than
recovered: the check fails, and every release after it fails the same way, until you record
the tag in `scripts/spent-tags.txt`. Either way you are left with a real, permanent tag and no
published package behind it, which is worse than doing nothing — the tag cannot simply be
re-cut, since `vX.Y.Z` would then mean two different things depending on which push you ask
about. To release without waiting for a merge, use
[Starting a release by hand](#starting-a-release-by-hand) instead: it is this same pipeline.

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
run's tag check fails on this same tag, and a re-run of `release` fails at its tip step, because
the commit right after its own carries the tag it cut (see
[A run that starts behind `main`](#a-run-that-starts-behind-main)). Use "Re-run failed jobs",
never "Re-run all jobs".

### Recovering a failure *after* the tag but *before* the artifact

There is one state with no re-run at all. If anything between the tag and the artifacts
fails once the tag has already reached origin — writing the release notes, `uv build`, the
SBOM export, or any of the three artifact uploads — `main` carries a permanent tag `vX.Y.Z`, `publish` was never
eligible (`needs.release.outputs.bumped` never reached it), so there is no `publish` job to
re-run, and re-running `release` fails at its tip step, because the commit right after its own
carries the tag it cut. Nothing can publish `X.Y.Z` from this run.

The tag's tree is complete, so the release workflow rebuilds it, but only once the 30-day
re-run window has closed. Until then the [tag check](#a-tag-that-never-reached-pypi) fails
every release run on `vX.Y.Z`, so nothing newer goes out and PyPI shows no gap. The check
names the run and gives both cases: re-running its failed jobs helps only when `release`
succeeded, and this is the other case, where `release` itself failed after pushing the tag.
Fix the cause, then choose:

- **Wait.** Once the window closes, the next run rebuilds `vX.Y.Z` from the tag and publishes
  it; the run after that releases what was merged meanwhile. No gap, at the cost of up to 30
  days without a release.
- **Accept the gap.** Record the tag in `scripts/spent-tags.txt` with the reason. The check
  then passes, and the next release goes out as `X.Y.Z+1` (or whatever the history warrants);
  `X.Y.Z` never exists on PyPI. A spent entry is permanent: the list only grows.

Do not try to
reclaim `vX.Y.Z` by deleting and re-pushing the tag: a tag that once existed publicly and later
points at different code is the failure mode this whole section exists to avoid.
