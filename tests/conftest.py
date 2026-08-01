"""Shared pytest fixtures for the skill-eval test suite."""

from __future__ import annotations

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


@pytest.fixture(scope="module")
def vcr_config():
    """Replay-only by default, with every credential scrubbed on record.

    Matching on the body as well as the URL matters here: every request goes to
    the same chat-completions path, so the body is the only thing that tells one
    turn of a conversation from the next.
    """
    return {
        "filter_headers": [
            "authorization",
            "api-key",
            "x-api-key",
            "openai-organization",
            "openai-project",
            "cookie",
            "set-cookie",
        ],
        "match_on": ["method", "scheme", "host", "port", "path", "body"],
        "decode_compressed_response": True,
    }


@pytest.fixture
def replay(request, monkeypatch):
    """Set up a cassette-backed test: dummy key, and skip if never recorded.

    Provider clients refuse to construct without a key even when every response
    is replayed, so a placeholder is required. A fresh clone with no cassettes
    must not look like a broken build, hence the skip -- but a *mismatched*
    request still fails loudly rather than reaching the network, which is the
    behaviour the tier exists for.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-replay")
    cassette = CASSETTE_DIR / request.node.module.__name__ / f"{request.node.name}.yaml"
    if not cassette.is_file():
        pytest.skip(f"cassette {cassette.name} not recorded; see the recording command in the plan")
