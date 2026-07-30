"""Load skill-eval.toml. Secrets never live here — only env vars."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONFIG_FILENAME = "skill-eval.toml"


class ConfigError(Exception):
    """Raised when a config file is missing or invalid."""


class Config(BaseModel):
    """Run defaults; every field is overridable by a CLI flag."""

    model_config = ConfigDict(extra="forbid")

    default_runner: str = "fake"
    min_pass_rate: float = 1.0
    fail_on_error: bool = True
    per_skill_min: dict[str, float] = Field(default_factory=dict)
    reporters: list[str] = Field(default_factory=lambda: ["console"])


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
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {path}: {exc}") from exc
