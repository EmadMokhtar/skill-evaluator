# skill-lens stranded release tags, part two: recovery — Design

**Date:** 2026-10-20
**Status:** Approved (design); implemented in the same pull request
**Issue:** [#103](https://github.com/EmadMokhtar/skill-evaluator/issues/103)
**Builds on:** [the detection design](2026-10-20-skill-lens-stranded-tag-design.md), whose §7
proposed this change and left one question open

## 1. Scope

The detection half makes a stranded tag loud: `scripts/check_tags_published.py` fails the
release workflow's no-release path and the weekly Security run for every release tag on
`origin` whose version PyPI does not have, naming the run that should have published it and
whether GitHub's 30-day re-run window is still open. Recovery is manual: re-run that run's
failed jobs. When the window has closed, or no run exists, the version is spent and the tag
is recorded in `scripts/spent-tags.txt`.

This change adds the automated path for exactly that remaining case. When the check, running
in `release` after a `cz bump` that released nothing, finds a tag whose run can no longer be
re-run — or that no release run pushed — the job checks the tag's commit out, verifies it,
builds the distributions from it, exports the SBOM and the release notes for that version,
and sets `release`'s `bumped` / `version` outputs so the existing, environment-gated `publish`
and `github-release` jobs run unchanged. The tag is never moved or re-created; a version PyPI
lists is never uploaded; a tag listed in `scripts/spent-tags.txt` is never recovered.

A tag whose run is still re-runnable is still reported for a manual re-run, not rebuilt
(§3). The weekly Security run still only reports: it has no `publish` job to hand anything to.

## 2. The decision: where the tag's commit is verified

The invariant **Nothing publishes that has not been verified in the same run** was written
for a run that builds the commit it was started for: `verify` lints, formats, tests and audits
that commit, and `publish` is reachable only through `needs:` on a green `verify`. A recovery
run builds a different commit. Its `verify` job ran on `main`'s tip, and the tag's own run
verified the tag's commit — in another run, weeks ago, against the advisories known then.

Two shapes were considered.

**(a) A `plan` job ahead of `verify`.** A first job decides bump-or-recover (`cz bump
--dry-run`, then the tag check) and outputs the ref `verify` and `release` check out: the
pushed commit, or the stranded tag. `verify` stays the one verification job and the job graph
keeps its meaning literally.

**(b) Verification inside `release`'s recover branch.** `release` stays as it is until the
tag check finds a recoverable tag; it then adds the tag's commit as a worktree and runs
`verify`'s five commands there — `uv sync --all-extras --dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pytest`, `uv audit --preview-features audit
--locked` — before building, in the same step that builds.

**Shape (b) is chosen.** The reasons, in order of weight:

1. **The normal path is untouched.** Shape (a) puts a new job, a `uv sync` and a decision in
   front of every release, and makes `verify`'s checkout a variable: a wrong `ref` output
   would verify the wrong tree on the common path — the invariant's own failure mode,
   introduced to protect against it on the rare path. Under (b) nothing before the tag check
   changes, and the recover branch is reached only after `cz bump` found nothing to release
   and the check found a tag to recover.
2. **The check stays where the detection design put it**, and recovery is literally "instead
   of only failing": the step after `bump`, on the no-release path. Shape (a) moves the check
   into a job of its own and separates the decision from the build by two jobs, which is more
   room for a concurrent run to change the facts between them.
3. **The verification and the build are the same checkout in the same step.** The tree the
   five commands ran on is the tree `uv build` reads, with no handoff between jobs to get
   wrong. The invariant's reason — the bytes that ship are the bytes that were tested — holds
   as directly as it does on the bump path.
4. **The duplication it costs is the kind this project already pins.** The five commands are
   now spelled twice in `release.yml`. `tests/test_release_workflow.py` requires every `run:`
   command of `verify` to appear, byte for byte, as a line of the recover step; the audit
   command joins the set `tests/test_security_checks.py` already requires to be identical
   across its copies, which becomes four. Shape (a) would instead duplicate the *decision*
   — `cz bump --dry-run` in one job, `cz bump` in another — with no test able to pin two
   invocations of a tool to the same answer.

What (b) gives up, stated plainly: the tag's suite runs under `release`'s `contents: write`
token, where `verify` holds `contents: read`. The code it runs is a commit already on `main`
that `verify` passed at the time, the same trust the job already extends to `uv sync` and
`cz bump` under that token; and the recover branch checks that the commit *is* on `main`
before running anything from it (§4). A recovery also makes `release` longer by one test
suite — on the recover path only.

The invariant keeps its heading. Its body, in `docs/invariants.md` and `CLAUDE.md` alike,
names the second verification site and the test that keeps it equal to the first.

## 3. Which tags are recovered

From the check's findings, a tag is **recoverable** when all of these hold:

- it is on `origin` and its version is **absent** from PyPI's release list — not merely listed
  with no files. PyPI refuses to accept a filename it has seen before, even after the file was
  deleted, so a version it lists is never rebuilt; that finding stays a failure and the tag is
  recorded as spent;
- it is not in `scripts/spent-tags.txt` (a spent tag is accepted by the check and never a
  finding, so this follows from the first);
- the run lookup *positively* found one of two states: the release run on the tag commit's
  parent exists, did not succeed, and started more than 30 days ago (GitHub's re-run window
  has closed); or no release run exists for that parent at all. A lookup that failed, a
  repository the check cannot name, a run that **succeeded** (a tag pushed by hand on top of a
  released tip — the detection design's "spent"), and a run whose window is still **open**
  are not recoverable.

A tag within its window is reported for a manual re-run, as before. Re-running `publish`
uploads the artifact that run itself built and verified, and the PEP 740 attestation names
the run that pushed the tag; that is the better artifact, and a human has to answer the
environment approval either way. The automation is for the case where that option is gone.
The detection design's message for an open window already says what to do and until when.
One imprecision carries over: when the original run failed *in* `release` after the tag
(notes, build, SBOM, an upload), there is no `publish` job to re-run and the "re-run its
failed jobs" advice does not help; such a tag waits for its window to close and is then
rebuilt — the tag's tree is complete — rather than staying spent for good as the docs said.
Reading the run's job list to tell the two apart is one more API call and is left out (§8).

**One tag per run, the oldest first.** The check lists tags in version order; the first
recoverable one is planned. A run builds one version and `release` has one `version` output;
a second recoverable tag is reported as waiting for a later run and does **not** fail the
check, or the first would never be recovered. Every other finding — an open window, a failed
lookup, a succeeded parent run, an empty release, a stale spent entry — still fails the step
(exit 1), and then no recovery runs: a red check is never followed by a publish. That can
hold an older recoverable tag behind a newer one still inside its window; the newer one's
window closes within 30 days, after which both are recoverable in turn.

## 4. The pieces

### `scripts/check_tags_published.py`

Gains one switch, the environment variable `RECOVERY_PLAN`: a file path. The command line is
unchanged in both workflows (`python3 scripts/check_tags_published.py`, which
`tests/test_security_checks.py` requires to be spelled identically), so the behaviour differs
by environment alone, exactly as `GITHUB_TOKEN` already does.

- **Unset** (`security.yml`, a laptop): every finding fails as before. The lines for a
  closed window or a missing run now end with what will happen — "The next merge to `main`
  that releases nothing rebuilds it from its commit and publishes it through the release
  workflow" — instead of telling the reader to record the tag as spent.
- **Set** (`release.yml`): the first recoverable finding is written to the file as three
  lines, `tag=`, `version=`, `commit=` (the commit the annotated tag points at, from the same
  `git ls-remote` the check read), its message says "This run rebuilds it from its commit and
  publishes it", and it is not a failure. Further recoverable findings say they wait for a
  later run and are not failures either. Anything else fails, exit 1, and the file is left
  **absent** — never half-written — so the step that reads it cannot act on a plan the check
  did not stand behind. With no finding the file is absent too.

`describe_run` is split: a `lookup_run` that returns a small `RunInfo` (status, URL, start,
deadline, conclusion, detail), and a renderer that turns it into lines given whether the tag
is being recovered. The verdict logic (`assess`) is unchanged.

### `release.yml`, job `release`

The check step gets `id: check` and `RECOVERY_PLAN: recovery-plan.txt` in its `env`. A new
step, **Verify and build a stranded tag** (`id: recover`, `shell: bash`,
`if: steps.bump.outputs.bumped == 'false'` — the same condition as the check, which a test
requires), sits right before the three artifact uploads, so what it writes is where they
read. Its `env` carries `RECOVERY_PLAN`; the script takes nothing through `${{ }}`, so the
tests run it verbatim against a local bare origin with a fake `uv` on `PATH`. In order:

1. No plan file, or an empty one: "No stranded tag to recover.", exit 0, no outputs.
2. Read `tag`, `version`, `commit`; refuse a plan missing any of them.
3. `git fetch origin refs/tags/<tag>:refs/tags/<tag>` — a no-op for a tag the clone already
   has at that commit, a refusal (`would clobber existing tag`) for one that moved — then
   require the local tag's peeled commit to equal the plan's. The tag is only ever *read*.
4. `git merge-base --is-ancestor <commit> $GITHUB_SHA`: the commit must be on `main` as this
   run sees it. A tag pointing at a branch, or at a commit `main` was rewritten away from, is
   refused.
5. `git worktree add --detach recover <commit>`, then in `recover/` the five `verify`
   commands, spelled as `verify` spells them.
6. `uv run cz version --project` in the worktree must print the plan's `version`: the tree
   must spell the version it is about to publish, or a tag pointing at the wrong commit would
   build and upload a different version — one PyPI may already list.
7. `uv run cz changelog <version> --dry-run > ../notes.md`; the build-constraint export and
   `uv build --build-constraint … --out-dir ../dist` with the same flags as the bump path,
   from the tag's own lockfile — the one step 5 audited; the SBOM export to
   `../sbom/skill-lens-<version>.cdx.json` with the same flags as the bump path.
8. `recovered=true` and `version=<version>` to `$GITHUB_OUTPUT`; a summary line.

Any failure before step 8 leaves `recovered` unset and fails the job. Nothing before the
upload is irreversible: no tag is created, moved or deleted, and no commit is pushed, so a
failed recovery spends nothing and the next no-release merge tries again.

The three uploads gate on `steps.push.outputs.pushed == 'true' || steps.recover.outputs.recovered == 'true'`.
The job's outputs become `bumped: ${{ steps.push.outputs.pushed == 'true' || steps.recover.outputs.recovered == 'true' }}`
and `version: ${{ steps.bump.outputs.version || steps.recover.outputs.version }}`. The bump
step's own `bumped` still drives nothing downstream: a version cut and never pushed is still
never published. `publish` and `github-release` are not edited. `github-release`'s
`--verify-tag` finds the tag on `origin`, where it has been all along.

### `security.yml`

Unchanged but for its comments, and a test that its check step sets no `RECOVERY_PLAN`: the
scheduled job reports and never recovers.

## 5. Invariants

In `docs/invariants.md` and `CLAUDE.md`, spelled alike:

- **Nothing publishes that has not been verified in the same run** — heading unchanged; the
  body names the recover branch as the second site and the test that pins it to `verify`.
- **A merge with no releasable commit publishes nothing, and fails only for a tag PyPI does
  not have** becomes **A merge with no releasable commit cuts no version, and fails only for a
  tag PyPI does not have that this run cannot recover**, with the plan-file switch, the
  one-per-run rule and the never-recover list in the body.
- New: **A stranded tag is rebuilt only from its own commit on `main`, spelling its own
  version, and only once its run can no longer be re-run** — the guards of §4, the
  within-the-window rule of §3, and that a failed recovery spends nothing.
- **The dependency audit is one command, spelled identically in three places** becomes
  **… in four places**: the recover step is the fourth copy.

## 6. Tests

- `tests/test_release_workflow.py`: the recover step's `id`, `shell`, `if` equal to the
  check's, position before the uploads, `env`, no `${{ }}`; the check step's `RECOVERY_PLAN`
  equal to the recover step's; every `verify` `run:` command present as a line of the recover
  step; the build flags, SBOM flags and `cz changelog --dry-run` present with their outputs
  outside the worktree; the uploads' and job outputs' gates. Then the script run verbatim with
  a fake `uv` on `PATH` against a local bare origin: no plan is a no-op with no outputs; a
  good plan creates the worktree at the tag's commit, runs the five commands in `verify`'s
  order before any build, writes the three artifacts and both outputs, and leaves the tag
  where it was; a plan whose commit the tag no longer points at, a commit not on `main`, a
  tree spelling another version, and a red suite each fail with no `recovered=` and no
  artifact.
- `tests/test_check_tags_published.py`: with `RECOVERY_PLAN` set — a closed window writes the
  plan and exits 0 with the "this run rebuilds" line; no run does the same; an open window,
  a failed lookup, a succeeded parent run and an empty release exit 1 and leave the file
  absent; two recoverable tags plan the older and say the newer waits; a recoverable tag
  beside a non-recoverable finding exits 1 with no file. Without it, the closed-window and
  no-run lines name the next merge instead of the spent list.
- `tests/test_security_checks.py`: the audit identity set gains the recover step's line; the
  scheduled check sets no `RECOVERY_PLAN`.

## 7. Docs

`docs/releasing.md` — "A tag that never reached PyPI" describes both recoveries and when a
tag is still spent; "There is no manual path to PyPI" and "Recovering a failure after the tag
but before the artifact" say what now happens to such a tag; `docs/security.md` says the
weekly job reports and the release workflow recovers; `docs/invariants.md` and `CLAUDE.md` as
in §5; `scripts/spent-tags.txt`'s header says when a tag is still spent.

## 8. Out of scope

- Telling a run whose `release` job failed after the tag (nothing to re-run) from one whose
  `publish` was cancelled (something to re-run) by reading the run's job list, so the first
  is recovered at once instead of after its window closes.
- A `workflow_dispatch` trigger on `release.yml` to start a recovery without a merge. Any
  merge that releases nothing — a `docs:` or `chore:` commit — starts one.
- Recovering more than one tag per run.
