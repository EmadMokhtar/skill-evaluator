"""Shared pytest fixtures for the skill-eval test suite."""

from __future__ import annotations

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
