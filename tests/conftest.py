"""Shared pytest fixtures for the skill-eval test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_cwd(tmp_path, monkeypatch):
    """Isolate every test from ambient filesystem state.

    `load_config` falls back to searching upward from `Path.cwd()` to `/`
    when no `--config` is given. No CLI test passes `--config`, so without
    this fixture every CLI `run` test silently depends on there being no
    `skill-eval.toml` in any ancestor of the pytest working directory --
    the day this repo (or any checkout of it) grows its own root-level
    `skill-eval.toml`, those tests would start reading it and change
    behavior for reasons invisible to the test itself.

    Chdir into a fresh, empty `tmp_path` before each test so any code path
    that reads `Path.cwd()` (config discovery in particular) starts from an
    isolated directory. Tests that need genuine upward-discovery behavior
    (see test_config.py) create their own file and pass an explicit `start=`
    path, which this fixture does not interfere with.
    """
    monkeypatch.chdir(tmp_path)


CASSETTE_DIR = Path(__file__).parent / "cassettes"


def _scrub_response(response):
    """Strip account-identifying response headers before they hit disk.

    `filter_headers` only ever touches the *request* side in vcrpy -- it is
    wired solely into the request path, so response headers pass through
    untouched regardless of what's listed there. Recorded responses carry
    `openai-organization`, `openai-project`, and a `set-cookie` (`__cf_bm`)
    that are permanently tied to whichever account did the recording, plus
    `x-request-id`/`cf-ray` which are harmless but noisy. This is the other
    half of the scrub: response headers, applied via `before_record_response`.
    Header keys in cassettes recorded through the httpx transport come out
    lower-cased already, but this pops case-insensitively so it stays correct
    if that ever changes. `access-control-expose-headers` is scrubbed too since
    its value is just the *names* of the headers above (`X-Request-ID`,
    `CF-Ray`) -- leaving it would both re-leak those names in its value list
    and advertise exposure of headers that are no longer present.
    """
    scrub = {
        "openai-organization",
        "openai-project",
        "set-cookie",
        "x-request-id",
        "cf-ray",
        "access-control-expose-headers",
    }
    headers = response.get("headers")
    if headers:
        for key in list(headers):
            if key.lower() in scrub:
                del headers[key]
    return response


@pytest.fixture(scope="module")
def vcr_config():
    """Replay-only by default, with every credential scrubbed on record.

    Matching on the body as well as the URL matters here: every request goes to
    the same chat-completions path, so the body is the only thing that tells one
    turn of a conversation from the next.

    Scrubbing is split across two hooks because vcrpy wires them into two
    different sides of the exchange: `filter_headers` only ever touches
    *request* headers (auth tokens, cookies sent by us), while
    `before_record_response` (see `_scrub_response` above) strips
    account-identifying headers the *provider* sends back.
    """
    return {
        "filter_headers": [
            "authorization",
            "api-key",
            "x-api-key",
            "cookie",
        ],
        "before_record_response": _scrub_response,
        "match_on": ["method", "scheme", "host", "port", "path", "body"],
        "decode_compressed_response": True,
    }


def configure_replay_key(monkeypatch, record_mode, environ=None):
    """Set (or deliberately withhold) OPENAI_API_KEY for a cassette-backed test.

    Provider clients refuse to construct without a key even when every response
    is replayed, so a placeholder is required in replay-only mode. However, when
    recording cassettes (--record-mode=once), the real API key must survive so
    the recording request succeeds.

    In replay-only mode (`record_mode == "none"`, the default), a dummy key is
    set via `monkeypatch`, which is scoped to the test and un-sets it afterward.

    When recording (`record_mode != "none"`), a real OPENAI_API_KEY must already
    be present in `environ`. If not, this fails with a clear message. This
    prevents the confusing scenario where the fixture clobbered a real key
    during an attempted recording -- the bug this function exists to make hard
    to silently reintroduce (see tests/test_conftest_replay.py).

    `environ` defaults to `os.environ` and is only overridable so tests can
    exercise both branches without mutating the real process environment.
    """
    if environ is None:
        environ = os.environ
    if record_mode == "none":
        # Replay-only: use a dummy key since clients need *something*
        monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-replay")
    else:
        # Recording mode: require a real key in the environment
        if "OPENAI_API_KEY" not in environ:
            pytest.fail(
                "Recording mode (--record-mode=once) requires a real OPENAI_API_KEY "
                "in the environment. Run: export OPENAI_API_KEY=<your-key>"
            )


@pytest.fixture
def replay(request, monkeypatch, record_mode):
    """Set up a cassette-backed test: dummy key for replay, skip if never recorded.

    Key handling is delegated to `configure_replay_key` (see its docstring for
    the replay-vs-record rationale); this fixture just wires it to the real
    `monkeypatch`/`record_mode`/`os.environ` and adds the "never recorded"
    skip, which only fires in replay-only mode: if a cassette doesn't exist,
    re-recording is impossible anyway.

    `pytest-recording`'s autouse, function-scoped `vcr` fixture always sets up
    before this one regardless of mode, so the skip check respects mode exactly.
    """
    configure_replay_key(monkeypatch, record_mode)

    cassette = CASSETTE_DIR / request.node.module.__name__ / f"{request.node.name}.yaml"
    if record_mode == "none" and not cassette.is_file():
        pytest.skip(
            f"cassette {cassette.name} not recorded; run "
            "`uv run pytest tests/test_cassettes.py --record-mode=once` with a real "
            "OPENAI_API_KEY exported to record it"
        )
