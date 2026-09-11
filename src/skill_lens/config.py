"""Load skill-lens.toml. Secrets never live here — only env vars."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from skill_lens.workspace import DEFAULT_LIMITS

CONFIG_FILENAME = "skill-lens.toml"
DEFAULT_MODEL = "openai:gpt-4o-mini"


class ConfigError(Exception):
    """Raised when a config file is missing or invalid."""


class Config(BaseModel):
    """Run defaults for `skill-lens run`.

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

    `baseline` defaults to `""` (off) so upgrading never doubles anyone's bill:
    `none` and `previous` are the two kinds of baseline, and the *absence* of a
    value is what turns comparison off. `min_delta` has no default because 0.0
    is a real, stricter choice ("must not regress") -- silently assuming it
    would gate runs nobody asked to gate.

    `concurrency` defaults to 1 -- no executor is constructed and behavior is
    identical to a single-threaded run. The work is network-bound (one provider
    round trip per item against sub-millisecond of local CPU), so raising it
    overlaps waits rather than using more cores; the practical ceiling is the
    provider's rate limit. Validation lives in the CLI, not here, so a config
    value and a flag are checked the same way `repeat` already is.

    `keep_workspace` keeps each case's temporary directory instead of deleting
    it, and `--keep-workspace` / `--no-keep-workspace` override it in either
    direction. Two states would not be enough: with `keep_workspace = true`
    committed there would be no way to get a clean run back without editing
    the file. Every kept directory is printed on every run, however keeping
    was turned on -- a persistent setting that produced no visible output
    would fill a disk with nothing on screen explaining why.

    `full_output` lifts the 500-character cap on the agent output printed
    under a non-passing case, in every reporter. `--full-output` /
    `--no-full-output` override it in either direction, like
    `keep_workspace`. The cap itself is never silent: a cut output always
    states exactly how many characters were removed.

    The three caps are runaway guards on what one case may write. They get no
    CLI flag because they are policy set once per repository rather than a
    per-run decision, the same reasoning that leaves `fail_on_error` and
    `retries` config-only. `gt=0` lives on the model rather than in the CLI
    because, with no flag, there is only one entry point to validate --
    unlike `repeat` and `concurrency`, whose checks sit in the CLI so a flag
    and a config value are checked identically.
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
    baseline: Literal["", "none", "previous"] = ""
    repeat: int = 1
    min_delta: float | None = None
    concurrency: int = 1
    keep_workspace: bool = False
    full_output: bool = False
    max_file_bytes: int = Field(default=DEFAULT_LIMITS.max_file_bytes, gt=0)
    max_files: int = Field(default=DEFAULT_LIMITS.max_files, gt=0)
    max_total_bytes: int = Field(default=DEFAULT_LIMITS.max_total_bytes, gt=0)


def find_config_file(start: Path) -> Path | None:
    """Search `start` and its parents for skill-lens.toml."""
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
