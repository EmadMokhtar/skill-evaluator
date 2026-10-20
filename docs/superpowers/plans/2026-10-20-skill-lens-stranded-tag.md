# Stranded release tags — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fail the release workflow's no-release path and a weekly job whenever a release tag on `origin` has no version on PyPI, naming the tag and the run that should have published it.

**Architecture:** One stdlib-only script, `scripts/check_tags_published.py`, holds the rule and is run by the same command from two workflows. `release.yml` runs it as the step after `bump` when `cz bump` found nothing; `security.yml` runs it in a new job on `schedule` and `workflow_dispatch`. A text file beside the script, `scripts/spent-tags.txt`, is the only exception list.

**Tech Stack:** Python 3.11+ stdlib (`tomllib`, `urllib.request`, `subprocess`, `json`, `datetime`), GitHub Actions, pytest.

**Spec:** `docs/superpowers/specs/2026-10-20-skill-lens-stranded-tag-design.md`

## Global Constraints

- The script imports nothing outside the standard library; it is run as `python3 scripts/check_tags_published.py`, spelled identically in both workflows.
- `scripts/**` may run `git` with a fixed argv (per-directory ruff ignores `S603`, `S607`); every other `S` finding is suppressed inline with a reason.
- All file reads pin `encoding="utf-8"`.
- Exit codes: `0` nothing missing, `1` a finding, `2` the check could not run.
- No `${{ }}` inside a `run:` block; values arrive through `env:`.
- Every action `uses:` is pinned to a 40-hex SHA with a `# vX.Y.Z` comment; no workflow-level write permission.
- A CLAUDE.md invariant bullet and its `docs/invariants.md` heading are spelled byte for byte alike.
- Conventional Commits on every commit.

## Review Focus

1. A PyPI outage must print "could not check" and exit `2`, never report a tag missing — Task 2 tests the retry and the exit code.
2. `git ls-remote` failing (no network, no remote) prints nothing, like a repository with no tags; it must be `2`, not a green run — Task 2.
3. A tag such as `v0` or `v1.0.0-docs` under the prefix must be listed as ignored, not flagged and not silently dropped — Task 1.
4. A run older than 30 days must say the re-run window closed, with the date, instead of sending a maintainer to a button that is gone — Task 2.
5. The lookup failing (rate limit, no token) must change the message, never the verdict — Task 2.

---

### Task 1: The pure rules

**Files:**
- Create: `scripts/check_tags_published.py`
- Create: `scripts/spent-tags.txt` (comments only; no entry)
- Test: `tests/test_check_tags_published.py`

**Interfaces:**
- Produces:
  - `class CheckUnavailable(Exception)`
  - `@dataclass(frozen=True) class TagFormat(prefix: str, suffix: str)` with `version(tag: str) -> str | None` (None unless the tag carries both and the middle matches `VERSION_RE = r"^\d+\.\d+\.\d+"`).
  - `read_tag_format(pyproject_text: str) -> TagFormat`; `read_project_name(pyproject_text: str) -> str`. Missing keys or a `tag_format` without `$version` raise `CheckUnavailable`.
  - `@dataclass(frozen=True) class ReleaseTag(name: str, version: str, commit: str)`.
  - `parse_ls_remote(output: str, fmt: TagFormat) -> tuple[list[ReleaseTag], list[str]]` — release tags sorted by numeric version parts, and the names under the prefix that are not versions. The `^{}` line's sha wins over the plain line's.
  - `pypi_versions(payload: dict) -> tuple[set[str], set[str]]` — `(with files, without files)`.
  - `parse_spent(text: str) -> dict[str, str]` — `tag -> reason`; `#` starts a comment; blank lines ignored.
  - `@dataclass(frozen=True) class Verdict(missing: list[ReleaseTag], empty: list[ReleaseTag], stale: list[ReleaseTag], accepted: list[ReleaseTag], published: int)`.
  - `assess(tags: list[ReleaseTag], with_files: set[str], without_files: set[str], spent: dict[str, str]) -> Verdict`.

- [ ] **Step 1: Write the failing tests** (load the script by path as `tests/test_check_docs_updated.py` does)

```python
def test_tag_format_derives_the_version_from_the_prefix_and_suffix():
    fmt = check.read_tag_format('[tool.commitizen]\ntag_format = "v$version"\n[project]\nname = "x"')
    assert fmt == check.TagFormat("v", "")
    assert fmt.version("v0.21.0") == "0.21.0"
    assert fmt.version("v0.22.0rc1") == "0.22.0rc1"
    assert fmt.version("0.21.0") is None          # no prefix
    assert fmt.version("v0") is None              # not a version
    assert check.TagFormat("rel-", "-final").version("rel-1.2.3-final") == "1.2.3"

def test_a_tag_format_without_the_version_placeholder_is_unavailable():
    with pytest.raises(check.CheckUnavailable): check.read_tag_format('[tool.commitizen]\ntag_format = "release"')

def test_the_real_pyproject_parses():
    text = check.PYPROJECT.read_text(encoding="utf-8")
    assert check.read_project_name(text) == "skill-lens"
    assert check.read_tag_format(text).version("v0.21.0") == "0.21.0"

def test_parse_ls_remote_prefers_the_peeled_commit_and_sorts_by_version():
    output = "aaa\trefs/tags/v0.10.0\nbbb\trefs/tags/v0.10.0^{}\nccc\trefs/tags/v0.9.1\nddd\trefs/tags/v0\neee\trefs/tags/other\n"
    tags, ignored = check.parse_ls_remote(output, check.TagFormat("v", ""))
    assert tags == [check.ReleaseTag("v0.9.1", "0.9.1", "ccc"), check.ReleaseTag("v0.10.0", "0.10.0", "bbb")]
    assert ignored == ["v0"]       # `other` is outside the prefix and not reported

def test_pypi_versions_separates_releases_with_files_from_empty_ones():
    payload = {"releases": {"0.20.0": [{"filename": "a.whl"}], "0.21.0": [], "0.22.0": [{}]}}
    assert check.pypi_versions(payload) == ({"0.20.0", "0.22.0"}, {"0.21.0"})

def test_parse_spent_reads_tags_with_reasons_and_skips_comments():
    text = "# header\n\nv0.3.0  # 2026-01-01: build failed after the tag\nv0.4.0\n"
    assert check.parse_spent(text) == {"v0.3.0": "2026-01-01: build failed after the tag", "v0.4.0": ""}

def test_assess_sorts_every_tag_into_one_bucket():
    tags = [T("v0.1.0", "0.1.0"), T("v0.2.0", "0.2.0"), T("v0.3.0", "0.3.0"), T("v0.4.0", "0.4.0"), T("v0.5.0", "0.5.0")]
    verdict = check.assess(tags, {"0.1.0", "0.3.0"}, {"0.5.0"}, {"v0.2.0": "spent", "v0.3.0": "stale"})
    assert verdict.published == 1
    assert [t.name for t in verdict.accepted] == ["v0.2.0"]
    assert [t.name for t in verdict.stale] == ["v0.3.0"]
    assert [t.name for t in verdict.missing] == ["v0.4.0"]
    assert [t.name for t in verdict.empty] == ["v0.5.0"]

def test_the_spent_file_exists_and_parses():
    assert check.parse_spent(check.SPENT_FILE.read_text(encoding="utf-8")) == {}
```

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/test_check_tags_published.py -v` → the module load fails (file missing).
- [ ] **Step 3: Implement the module header, constants, `CheckUnavailable`, `TagFormat`, `read_tag_format`, `read_project_name`, `ReleaseTag`, `parse_ls_remote`, `pypi_versions`, `parse_spent`, `Verdict`, `assess`, and write `scripts/spent-tags.txt`** with a comment header: what the file is (tags on origin that will never be on PyPI), one tag per line with `# <date>: <reason>`, it only grows by a reviewed commit, an entry for a published tag fails the check, and a pointer to `docs/releasing.md`.
- [ ] **Step 4: Run the tests** → PASS. `uv run ruff check scripts tests && uv run ruff format --check scripts tests` → clean.
- [ ] **Step 5: Commit** — `fix: add the rules that compare release tags with PyPI`.

### Task 2: IO, the run lookup, the messages and `main`

**Files:**
- Modify: `scripts/check_tags_published.py`
- Test: `tests/test_check_tags_published.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `_get(url: str, headers: dict[str, str]) -> bytes` (urllib, timeout 30).
  - `ls_remote() -> str` — `git ls-remote --tags origin`; non-zero exit → `CheckUnavailable`.
  - `fetch_pypi(name: str, *, attempts: int = PYPI_ATTEMPTS, sleep=time.sleep) -> dict` — 404 → `{"releases": {}}`; other errors retried, then `CheckUnavailable`.
  - `fetch_github(path: str) -> Any` — `GITHUB_API_URL` or `https://api.github.com`, `Authorization: Bearer <GITHUB_TOKEN>` when set.
  - `repository() -> str | None` — `GITHUB_REPOSITORY`, else parsed from `git remote get-url origin`.
  - `describe_run(repo: str | None, tag: ReleaseTag, now: datetime) -> list[str]` — the indented lines after a finding's first line, exactly the spellings below.
  - `main(argv: list[str] | None = None, *, now: datetime | None = None) -> int`.
- Message spellings (tests assert substrings):
  - `Tag {name} is on origin, but {version} is not on PyPI ({project}).`
  - `Tag {name} is on origin, but PyPI lists {version} with no files ({project}).`
  - `  The run that should have published it: {html_url} (started {YYYY-MM-DD}, {conclusion}).`
  - `  Re-run its failed jobs; GitHub allows that until {YYYY-MM-DD} (30 days after the run started).`
  - `  Re-running it is no longer possible: GitHub's 30-day window closed on {YYYY-MM-DD}.`
  - `  No release run exists for its commit's parent {sha7}: the release workflow did not push this tag.`
  - `  Looking up the run that should have published it failed: {error}.`
  - `  Looking up the run that should have published it was skipped: the repository is unknown (set GITHUB_REPOSITORY).`
  - `  Otherwise record the tag in scripts/spent-tags.txt with the reason. See docs/releasing.md, "A tag that never reached PyPI".`
  - `Tag {name} is listed in scripts/spent-tags.txt, but {version} is on PyPI. Remove the entry.`
  - `Could not check: {reason}. Nothing was verified.`
  - `Every release tag on origin is on PyPI ({n} tags).`

- [ ] **Step 1: Write the failing tests** — monkeypatch `check.ls_remote`, `check.fetch_pypi`, `check.fetch_github`, `check.repository`, `check.PYPROJECT`, `check.SPENT_FILE`; `now = datetime(2026, 10, 20, tzinfo=UTC)`.

```python
def test_every_tag_published_exits_zero_and_writes_the_summary(monkeypatch, tmp_path, capsys)
    # two tags, both with files; GITHUB_STEP_SUMMARY set → file contains "Every release tag on origin is on PyPI (2 tags)."
def test_a_missing_tag_is_a_finding_that_names_the_run_and_the_window(monkeypatch, capsys)
    # fetch_github: commits/<sha> → parents [{"sha": "1d7f2b2..."}]; actions/runs → one run with path release.yml, html_url, created_at "2026-10-05T11:16:14Z", conclusion "failure"
    # exit 1; stderr has the first line, the run line with "(started 2026-10-05, failure)", "until 2026-11-04", the spent-tags footer
def test_a_run_older_than_thirty_days_says_the_window_closed(monkeypatch, capsys)   # now = 2026-12-01 → "closed on 2026-11-04"
def test_a_tag_with_no_release_run_says_the_workflow_did_not_push_it(monkeypatch, capsys)   # runs list has only ci.yml
def test_a_failed_lookup_changes_the_message_not_the_verdict(monkeypatch, capsys)   # fetch_github raises → exit 1, "failed:" in stderr
def test_an_unknown_repository_skips_the_lookup(monkeypatch, capsys)   # repository() → None
def test_a_release_listed_with_no_files_is_a_finding(monkeypatch, capsys)   # "with no files"
def test_a_spent_tag_is_accepted_and_a_published_spent_tag_is_stale(monkeypatch, tmp_path, capsys)
    # spent file lists v0.1.0 (missing → accepted, exit 0, stdout names it) and in a second run v0.2.0 (published → exit 1, "Remove the entry")
def test_an_unreadable_pypi_is_unknown_not_missing(monkeypatch, capsys)   # fetch_pypi raises CheckUnavailable → exit 2, "Could not check", "not on PyPI" absent
def test_a_failed_ls_remote_is_unknown_not_a_clean_bill(monkeypatch, capsys)   # ls_remote raises → exit 2
def test_fetch_pypi_retries_then_gives_up()   # get raises URLError three times → CheckUnavailable, sleep called twice
def test_fetch_pypi_treats_a_404_as_no_releases()   # get raises HTTPError(404) → {"releases": {}}
def test_fetch_pypi_returns_the_payload_on_a_later_attempt()   # fails once, then returns bytes
def test_repository_parses_both_remote_spellings(monkeypatch)   # https://github.com/o/r.git and git@github.com:o/r.git → "o/r"; GITHUB_REPOSITORY wins
```

- [ ] **Step 2: Run them to verify they fail** → `AttributeError` on the missing names.
- [ ] **Step 3: Implement the IO functions, `describe_run` and `main`.** `main` reads `PYPROJECT`, calls `ls_remote`, `fetch_pypi`, reads `SPENT_FILE`, calls `assess`, prints accepted and ignored tags on stdout, findings on stderr, the one-line verdict on stdout and appends it to `$GITHUB_STEP_SUMMARY` when set; `CheckUnavailable` anywhere → `Could not check: …` on stderr, return 2. Guard `if __name__ == "__main__": raise SystemExit(main())`.
- [ ] **Step 4: Run the tests, ruff and format** → PASS, clean. Then run the script for real from the worktree: `python3 scripts/check_tags_published.py` → exit 1 naming `v0.21.0` and run 37301857121.
- [ ] **Step 5: Commit** — `fix: report a release tag that is not on PyPI, naming its run`.

### Task 3: The release workflow

**Files:**
- Modify: `.github/workflows/release.yml` (job `release`: `permissions`, new step between `bump` and `push`)
- Test: `tests/test_release_workflow.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_merge_with_nothing_to_release_checks_every_tag_against_pypi(workflow):
    steps = workflow["jobs"]["release"]["steps"]
    check = next(s for s in steps if "check_tags_published" in str(s.get("run", "")))
    assert check["if"] == "steps.bump.outputs.bumped == 'false'"
    assert steps.index(check) > next(i for i, s in enumerate(steps) if s.get("id") == "bump")
    assert steps.index(check) < next(i for i, s in enumerate(steps) if s.get("id") == "push")
    assert check["run"].strip() == "python3 scripts/check_tags_published.py"
    assert "${{" not in check["run"]
    assert check["env"]["GITHUB_TOKEN"] == "${{ github.token }}"

def test_the_release_job_may_read_workflow_runs(workflow):
    assert workflow["jobs"]["release"]["permissions"] == {"contents": "write", "actions": "read"}
```

Also extend `test_only_the_release_job_may_write_to_the_repository` if it compares the whole mapping (it reads `["contents"]` only; leave it).

- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Edit `release.yml`**: add `actions: read` with a comment (the check names the run that should have published a stranded tag; listing runs with the job's token needs it); add the step `Check every release tag reached PyPI` with a comment explaining why it runs only when nothing was released (a release in flight must never fail over an older tag; the weekly job covers that), and that the command is spelled identically in `security.yml`.
- [ ] **Step 4: Run `uv run pytest tests/test_release_workflow.py tests/test_supply_chain.py -v`** → PASS (the existing `test_everything_after_the_push_waits_for_it_to_land` still passes because the step is before `push`).
- [ ] **Step 5: Commit** — `ci: check every release tag against PyPI when a merge releases nothing`.

### Task 4: The weekly job

**Files:**
- Modify: `.github/workflows/security.yml` (new job `published`)
- Test: `tests/test_security_checks.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_stranded_tag_is_found_on_the_schedule_even_when_nothing_merges(workflow):
    job = workflow["jobs"]["published"]
    assert job["if"] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert any("checkout" in str(s.get("uses", "")) for s in job["steps"])

def test_every_copy_of_the_tag_check_is_the_same_command(workflow):
    release = safe_load(RELEASE.read_text(encoding="utf-8"))
    commands = {
        *_commands_in(workflow["jobs"]["published"]["steps"], "check_tags_published"),
        *_commands_in(release["jobs"]["release"]["steps"], "check_tags_published"),
    }
    assert len(commands) == 1, commands
    assert (REPO_ROOT / "scripts" / "check_tags_published.py").is_file()
```

Generalise `_audit_commands_in(steps)` into `_commands_in(steps, needle)` and keep the audit helper calling it.

- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Edit `security.yml`**: the job, with a comment: why it exists (approval never given → tag with no package; v0.21.0), why only on `schedule`/`workflow_dispatch` (a stranded tag says nothing about a pull request; `release.yml` covers a merge), why `actions: read`. Same checkout pin as `audit`, `env: GITHUB_TOKEN: ${{ github.token }}`, `run: python3 scripts/check_tags_published.py`.
- [ ] **Step 4: Run `uv run pytest tests/test_security_checks.py tests/test_supply_chain.py -v`** → PASS.
- [ ] **Step 5: Commit** — `ci: check every release tag against PyPI on the weekly schedule`.

### Task 5: Docs and invariants

**Files:**
- Modify: `docs/releasing.md`, `docs/security.md`, `docs/invariants.md`, `CLAUDE.md`
- Test: `tests/test_invariants_sync.py`, `tests/test_docs.py`, `uv run mkdocs build --strict`

- [ ] **Step 1: Edit `docs/invariants.md`** — rename the heading `### A merge with no releasable commit publishes nothing and fails nothing` to `### A merge with no releasable commit publishes nothing, and fails only for a tag PyPI does not have`; body per spec §5.
- [ ] **Step 2: Edit `CLAUDE.md`** — the matching bullet, bold lead byte-identical to the heading, condensed body.
- [ ] **Step 3: Run `uv run pytest tests/test_invariants_sync.py -v`** → PASS.
- [ ] **Step 4: Edit `docs/releasing.md`** — in "What happens on a merge to main", the no-op paragraph gains: the job then checks every release tag on `origin` against PyPI (link). New `## A tag that never reached PyPI` section before "There is no manual path to PyPI": how it happens (approval never given), what the check reads, where it runs (no-release path; weekly; on demand), what the failure says, recovery (re-run the failed jobs of the named run within 30 days: it uploads the artifact that run built and verified), `scripts/spent-tags.txt` (what goes in it, only grows, stale entry fails), that a PyPI it cannot read is exit `2`. Point "Recovering a failure after the tag but before the artifact", the "exists on origin but points at" paragraph and "There is no manual path to PyPI" at it (the spent list replaces "fix the cause and move on").
- [ ] **Step 5: Edit `docs/security.md`** — a sentence after the "Where it runs" table: the scheduled run also checks every release tag against PyPI, with a link to the new section.
- [ ] **Step 6: Run `uv sync --group docs && uv run mkdocs build --strict && uv run pytest tests/test_docs.py tests/test_naming.py tests/test_troubleshooting.py -v`** → PASS.
- [ ] **Step 7: Commit** — `docs: describe the stranded-tag check and its recovery`.

### Task 6: Whole-branch verification and the pull request

- [ ] **Step 1:** `uv run pytest` (full offline suite), `uv run ruff check .`, `uv run ruff format --check .`, `uv run skill-lens list ./examples` → all green.
- [ ] **Step 2:** `python3 scripts/check_commits.py origin/main HEAD` → every commit conventional.
- [ ] **Step 3:** Fresh reviewer pass over the branch diff; fix what it finds.
- [ ] **Step 4:** Push and open the PR: title `fix: fail loudly when a tagged release is not on PyPI`, body with `Closes #103`, the decisions that differ from the issue text (every tag, not only the latest; detect only; the spent list), the live finding (`v0.21.0`, recoverable by re-running run 37301857121 until 2026-11-04), assignee `EmadMokhtar`.
