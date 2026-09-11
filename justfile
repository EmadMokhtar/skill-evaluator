# Task runner for maintainers. `just` with no arguments lists the recipes.
#
# Anything that needs a provider key runs through `op run` (the 1Password
# CLI), which resolves the reference in .env.tpl and hands the value to the
# child process only. The key never touches the shell, its history, or a
# file on disk.

# List the recipes
default:
    @just --list --unsorted

# Then proves the recordings replay offline and refuses to leave a credential
# in them -- the same three steps as .github/workflows/refresh-cassettes.yml.
# Pass a test name to refresh ONE cassette instead of paying for all six:
#   just refresh-cassettes test_a_real_judge_grades_a_rubric_with_evidence
# Re-record provider traffic through 1Password (costs real money)
refresh-cassettes target="":
    op run --env-file=.env.tpl -- uv run pytest tests/test_cassettes.py{{ if target != "" { "::" + target } else { "" } }} --record-mode=rewrite
    just replay-cassettes
    just scan-cassettes

# A recording that cannot be replayed is worthless, and nobody reading the
# YAML would notice.
# Prove every cassette replays with the network forbidden and no key present
replay-cassettes:
    env -u OPENAI_API_KEY uv run pytest tests/test_cassettes.py --record-mode=none -q

# Scrubbing already runs on both sides of the exchange in tests/conftest.py;
# this is a second lock on a locked door, using the exact pattern the CI
# workflow uses so the two cannot drift apart.
# Refuse to commit a credential found in the cassette diff
scan-cassettes:
    #!/usr/bin/env bash
    set -euo pipefail
    # Staged and unstaged changes to tracked cassettes, plus any brand-new
    # cassette git does not know about yet -- `git diff` cannot see those.
    if ! changed="$(git diff HEAD -- tests/cassettes)"; then
        echo "git diff failed; cannot verify the recordings are secret-free." >&2
        exit 1
    fi
    untracked="$(git ls-files --others --exclude-standard -- tests/cassettes)"
    if [ -n "$untracked" ]; then
        changed="$changed"$'\n'"$(cat $untracked)"
    fi
    pattern='(^|[^A-Za-z0-9])(sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{20,})'
    if printf '%s' "$changed" | grep -nE "$pattern"; then
        echo "A credential appears in the cassettes. Refusing." >&2
        exit 1
    fi
    echo "cassettes: no credential in the diff"
