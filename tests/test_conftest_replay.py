"""Regression tests for the `replay` fixture's API-key handling in conftest.py.

This is test infrastructure testing test infrastructure, which looks unusual,
so here is the justification: `configure_replay_key` (used by the `replay`
fixture in tests/conftest.py) decides whether a dummy `OPENAI_API_KEY` gets
written into the environment. Get that decision wrong in one direction and a
fresh clone with no key can no longer replay cassettes offline; get it wrong
in the other direction and a developer's real exported key gets clobbered
mid-recording (`--record-mode=once`), silently breaking the documented
cassette re-record workflow with no error until the request tries to
authenticate. That exact regression shipped once, was caught in review, and
was fixed with no permanent test -- only a temporary one, run once and
deleted. These tests exist so a future edit that reintroduces the
unconditional `setenv` fails here instead of being caught by whoever next
attempts a paid recording.

`configure_replay_key(monkeypatch, record_mode, environ=None)` is exercised
directly, bypassing `pytest-recording`'s `record_mode` fixture entirely, so
these tests need no `--record-mode` flag, no cassette, and make no network
call. Tests 1 and 2 let it default to the real `os.environ` (via the test's
own `monkeypatch` fixture) so they observe exactly what the `replay` fixture
would do in production -- a test that only inspected a fake `environ` dict
would not actually notice an unconditional `monkeypatch.setenv` regression,
since that call mutates the real environment regardless of what `environ`
was passed for the presence check.
"""

from __future__ import annotations

import os

import pytest

from conftest import configure_replay_key


def test_replay_only_mode_sets_the_dummy_key(monkeypatch):
    """record_mode == 'none' (the default): a fresh clone with no key can replay."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    configure_replay_key(monkeypatch, "none")

    assert os.environ["OPENAI_API_KEY"] == "dummy-key-for-replay"


def test_recording_mode_leaves_a_real_key_untouched(monkeypatch):
    """record_mode == 'once' with a real key exported: the real key must survive.

    This is the exact scenario the fixed bug broke: an unconditional `setenv`
    would clobber the developer's real exported key before the recording
    request goes out, so the request would authenticate as the dummy instead
    and fail. Asserting against the real `os.environ` (not a stand-in dict)
    is what makes this test able to catch that regression.
    """
    real_key = "sk-real-developer-key"
    monkeypatch.setenv("OPENAI_API_KEY", real_key)

    configure_replay_key(monkeypatch, "once")

    assert os.environ["OPENAI_API_KEY"] == real_key


def test_recording_mode_without_a_key_fails_with_guidance():
    """record_mode == 'once' with no key exported: fail loudly, don't record garbage.

    `environ={}` is passed explicitly (rather than relying on the ambient
    `os.environ`) so this assertion holds regardless of what happens to be
    exported in whatever environment runs the suite. `monkeypatch` is not
    needed on this branch -- it fails before any env var would be set.
    """
    with pytest.raises(pytest.fail.Exception, match="export OPENAI_API_KEY"):
        configure_replay_key(monkeypatch=None, record_mode="once", environ={})
