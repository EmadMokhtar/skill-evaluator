"""Load skill-eval.toml. Secrets never live here — only env vars."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONFIG_FILENAME = "skill-eval.toml"
DEFAULT_MODEL = "openai:gpt-4o-mini"


class ConfigError(Exception):
    """Raised when a config file is missing or invalid."""


class Config(BaseModel):
    """Run defaults for `skill-eval run`.

    `default_runner` (`--runner`), `model` (`--model`), `judge_model`
    (`--judge-model`) and `min_pass_rate` (`--min-pass-rate`) can be overridden
    by a CLI flag; the rest can only be set here. Secrets are never read from
    this file -- API keys come from the environment only.

    `judge` defaults to "fake" for the same reason `default_runner` does:
    upgrading must never start spending money on its own. An unscripted
    FakeJudge errors rather than passing, so that default cannot turn an
    unchecked rubric into a green case. An empty `judge_model` falls back to
    `model`.

    `judge_temperature` is deliberately separate from `temperature` and
    defaults to `0.0` for determinism: the judge grades a fixed rubric and
    must not become a source of flaky CI runs, even when `temperature` is
    raised to exercise the runner under sampling. It does not fall back to
    `temperature` -- a silent fallback is exactly what let the judge inherit
    the runner's temperature before this field existed. A reasoning judge
    model that rejects any explicit temperature needs
    `judge_temperature = "unset"`, same as `temperature` does for a reasoning
    runner model.
    """

    model_config = ConfigDict(extra="forbid")

    default_runner: str = "fake"
    model: str = DEFAULT_MODEL
    temperature: float | Literal["unset"] = 0.0
    retries: int = 2
    retry_backoff_seconds: float = 1.0
    judge: str = "fake"
    judge_model: str = ""
    judge_temperature: float | Literal["unset"] = 0.0
    min_pass_rate: float = 1.0
    fail_on_error: bool = True
    per_skill_min: dict[str, float] = Field(default_factory=dict)


def find_config_file(start: Path) -> Path | None:
    """Search `start` and its parents for skill-eval.toml."""
    start = Path(start).resolve()
    for directory in [start, *start.parents]:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None, start: Path | None = None) -> Config:
    """Load config from an explicit path, else by upward discovery, else defaults."""
    if path is not None:
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"config file does not exist: {path}")
        if not path.is_file():
            raise ConfigError(f"config file is not a file: {path}")
    else:
        path = find_config_file(start or Path.cwd())
        if path is None:
            return Config()

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {path}: {exc}") from exc
