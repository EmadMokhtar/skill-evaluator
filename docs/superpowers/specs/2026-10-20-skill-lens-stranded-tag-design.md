# skill-lens stranded release tags — Design

**Date:** 2026-10-20
**Status:** Approved (design); the implementation plan follows in a separate document
**Issue:** [#103](https://github.com/EmadMokhtar/skill-evaluator/issues/103)

## 1. Scope

A version can be tagged and never published, and nothing notices. `release` bumps the
version and pushes the bump commit and its annotated tag; only then does `publish` run,
inside the `pypi` environment, which waits for a reviewer. If the approval never comes, the
run ends with the tag on `main` and nothing on PyPI. Every later run reads only the commits
since that tag, so a merge with nothing releasable (`cz bump` exit `21` or `3`) publishes
nothing, and nothing ever retries the stranded version.

It happened to `v0.21.0`: run 37301857121 pushed the tag on 2026-10-05, `publish` waited a
day for review and was cancelled with no step run, and PyPI went from `0.20.0` straight to
`0.22.0`. For two weeks `pip install skill-lens` installed `0.20.0` while `main`,
`action.yml` and the changelog said `0.21.0`, and the composite action pinned a version
PyPI did not have. CI did not notice because `action-smoke` installs the checkout.

This change makes a stranded tag **loud**. It ships as one pull request
(`fix: fail loudly when a tagged release is not on PyPI`). Recovery — rebuilding from the
tag and sending the result through the approved `publish` — is **out of scope** here and
proposed as a follow-up; see §7.

## 2. Decisions

| Question | Decision | Why |
| --- | --- | --- |
| Which tags are checked | **Every** release tag on `origin`, not only the latest | All 23 other tags are on PyPI, so the check has exactly one finding today. A latest-only check goes quiet the moment a newer version publishes, while `uses: …@v0.21.0` stays broken forever. The cost of checking everything is one HTTP request. |
| What "published" means | The version is a key of PyPI's `releases` **and has at least one file** | A release whose files were all deleted is listed but cannot be installed. |
| Where the check runs | In `release`, as the step after `bump`, only when `cz bump` found nothing to release; and as a job in `security.yml` on `schedule` and `workflow_dispatch` only | The release path must never fail a release that is happening in the same run. The weekly job finds a stranded tag when nothing merges. Not on `pull_request` or `push`: a stranded tag says nothing about the change under review. |
| Where the rule lives | `scripts/check_tags_published.py`, stdlib only, run as `python3 scripts/check_tags_published.py` | Testable with injected inputs, like the other two CI helpers. No `uv sync`, so the security job keeps installing nothing. Both workflows spell the command byte-for-byte alike, and a test requires it, as for the audit. |
| Where the tags come from | `git ls-remote --tags origin`, exit status checked apart from output | What is on `origin` is the truth; it works on a shallow checkout; a failed lookup prints nothing, exactly like no tags, so the two must not be confused. |
| How the version is spelled | Derived from `[tool.commitizen] tag_format` in `pyproject.toml` | The tag prefix is derived, not duplicated (existing invariant). |
| An unreachable PyPI | Exit `2`, "could not check", after three attempts | Never reported as "missing", and never a silent pass. No release is in flight on either path, so this can never fail a correctly published release. |
| Naming the run | Best effort through the GitHub API: the tag's commit → its first parent → the `release.yml` run on that `head_sha` | The issue asks for it. A failed lookup is a note in the message, never a different verdict. |
| Credentials | None for PyPI; `GITHUB_TOKEN` for the run lookup, so both jobs add `actions: read` | An unauthenticated GitHub call from a shared runner address is rate-limited; a token with only `contents: read` is refused on the runs endpoint. |
| A tag that will never be published | `scripts/spent-tags.txt`, one tag per line with a reason; only ever grows by a reviewed commit; an entry for a tag PyPI **does** have fails the check | `docs/releasing.md` already names two states where a version is spent for good. Without an acknowledged list the check would stay red forever; a stale entry would be a lie, so it fails, the way `ignore-until-fixed` turns red the day a fix ships. |
| Recovery in this change | Documented, not automated: re-run the failed jobs of the named run within GitHub's 30-day window | Re-running `publish` uploads the artifact `release` built and `verify` tested in that run, so the "verified in the same run" invariant holds. Rebuilding from the tag does not, and needs its own design. |

## 3. The script

`scripts/check_tags_published.py`:

1. Reads `pyproject.toml` beside it: `[project] name` and `[tool.commitizen] tag_format`.
   `tag_format` is split at `$version` into a prefix and a suffix.
2. Runs `git ls-remote --tags origin`. A non-zero exit is `CheckUnavailable`. From the
   output it keeps `refs/tags/<name>` where `<name>` carries the prefix and suffix and the
   middle starts like a version (`\d+\.\d+\.\d+`); the `^{}` line, when present, gives the
   commit an annotated tag points at. Anything else under the prefix is listed as ignored,
   never silently dropped.
3. Fetches `https://pypi.org/pypi/<name>/json`, three attempts. A `404` is "no releases";
   any other failure after the retries is `CheckUnavailable`. Published versions are the
   `releases` keys with at least one file.
4. Reads `scripts/spent-tags.txt` (`#` comments, blank lines ignored; `tag  # reason`).
5. Verdict per tag: published; spent and not published (accepted); spent **and** published
   (stale entry, a finding); neither (missing, a finding).
6. For each missing tag, best effort: `GET /repos/{repo}/commits/{sha}` →
   `parents[0].sha`; `GET /repos/{repo}/actions/runs?head_sha=…&event=push` → the run whose
   `path` is `.github/workflows/release.yml`. The repository is `GITHUB_REPOSITORY` or parsed
   from the origin URL. The message names the run's URL, start date and conclusion, and says
   whether the 30-day re-run window is still open and until when; when no release run exists
   it says the workflow did not push this tag; when the lookup fails it says so.
7. Exit `0` when there are no findings, with "Every release tag on origin is on PyPI (N
   tags)"; `1` with the findings on stderr; `2` when the check could not run. When
   `GITHUB_STEP_SUMMARY` is set the one-line verdict is appended to it.

## 4. The workflows

`release.yml`, job `release`: `permissions` gains `actions: read`; a new step between
`bump` and `push`, `if: steps.bump.outputs.bumped == 'false'`, `env: GITHUB_TOKEN:
${{ github.token }}`, `run: python3 scripts/check_tags_published.py`. Every step after
`push` still gates on `steps.push.outputs.pushed == 'true'`.

`security.yml`: a new job `published`, `if: github.event_name == 'schedule' ||
github.event_name == 'workflow_dispatch'`, `permissions: {contents: read, actions: read}`,
a checkout and the same step. The `audit` job is unchanged.

## 5. Invariants

The heading **A merge with no releasable commit publishes nothing and fails nothing**
becomes **A merge with no releasable commit publishes nothing, and fails only for a tag
PyPI does not have**, in `docs/invariants.md` and `CLAUDE.md` alike. Its body states: the
check reads every release tag on `origin` against PyPI's file-bearing releases, with no
credentials; it runs on the no-release path and weekly; a PyPI it cannot read is "unknown"
(exit `2`), never "missing"; a tag PyPI has is never re-uploaded; `scripts/spent-tags.txt`
is the only exception and a stale entry fails; both workflows spell the command alike.

## 6. Tests and docs

- `tests/test_check_tags_published.py`: the pure functions and `main()` with injected
  `ls_remote`, `fetch_pypi`, `fetch_github`, clock and file paths — every verdict, every
  message branch, every exit code, the retry, the ignored non-version tag.
- `tests/test_release_workflow.py`: the step exists, its `if`, its position before `push`,
  the token through `env`, no `${{` in `run`, `actions: read` on the job.
- `tests/test_security_checks.py`: the `published` job, its gate, its permissions, and the
  command byte-identical to `release.yml`'s.
- `docs/releasing.md`: a new section "A tag that never reached PyPI"; the no-op paragraph,
  the "spent" paragraphs and "There is no manual path to PyPI" point at it.
  `docs/security.md`: the scheduled run also runs this check. `docs/invariants.md` and
  `CLAUDE.md` as in §5.

## 7. Out of scope, proposed as a follow-up

Recovery after the 30-day window: in `release`, when the check finds a stranded tag, check
the tag's commit out, run the test suite on it, build, export the SBOM and the notes, and set
`bumped=true` / `version` so `publish` and `github-release` run unchanged. The open question
is the "verified in the same run" invariant: the tag's own run verified that commit, but this
run's `verify` job ran on `main`'s tip. That needs a decision — a `verify` that checks out
the tag, or the suite re-run inside `release` — and is not made here.
