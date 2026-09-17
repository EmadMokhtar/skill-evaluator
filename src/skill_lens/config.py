"""Load skill-lens.toml. Secrets never live here — only env vars."""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from skill_lens.models import SandboxMode
from skill_lens.runners.product import (
    DEFAULT_INVOKE,
    DEFAULT_SKILLS_DIR,
    PRESETS,
    PROMPT_PLACEHOLDER,
    Product,
)
from skill_lens.runners.product import (
    DEFAULT_MAX_OUTPUT_BYTES as PRODUCT_DEFAULT_MAX_OUTPUT_BYTES,
)
from skill_lens.runners.product import (
    DEFAULT_TIMEOUT_SECONDS as PRODUCT_DEFAULT_TIMEOUT_SECONDS,
)
from skill_lens.scripts import (
    DEFAULT_INTERPRETERS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    ScriptPolicy,
)
from skill_lens.workspace import DEFAULT_LIMITS, PathRefused, check_relative_path

CONFIG_FILENAME = "skill-lens.toml"
DEFAULT_MODEL = "openai:gpt-4o-mini"
PRODUCT_NAMES: tuple[str, ...] = (*PRESETS, "cli")


class ConfigError(Exception):
    """Raised when a config file is missing or invalid."""


class ProductSettings(BaseModel):
    """One `[runners.<name>]` table: how a product runner or judge is started.

    `command` replaces the preset's whole argv (and is required for `cli`,
    which has no preset); `args` is appended to whichever argv results. Two
    knobs with two meanings: drop an isolation flag with `command`, add a
    model with `args`. `skills_dir` and `invoke` are accepted for `cli` only;
    a preset is the verified spelling for its product. No secret lives here:
    the product reads its own auth.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str] | None = None
    args: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(
        default=PRODUCT_DEFAULT_TIMEOUT_SECONDS, gt=0, allow_inf_nan=False
    )
    max_output_bytes: int = Field(default=PRODUCT_DEFAULT_MAX_OUTPUT_BYTES, gt=0)
    skills_dir: str | None = None
    invoke: str | None = None

    @field_validator("command")
    @classmethod
    def _one_prompt_element(cls, value: list[str] | None) -> list[str] | None:
        """The prompt is substituted as one whole argv element, never through a shell."""
        if value is None:
            return None
        if (
            value.count(PROMPT_PLACEHOLDER) != 1
            or not value[0].strip()
            or value[0] == PROMPT_PLACEHOLDER
        ):
            raise ValueError(
                f"must name an executable and contain exactly one element equal to "
                f"{PROMPT_PLACEHOLDER}"
            )
        return value

    @field_validator("args")
    @classmethod
    def _no_prompt_element_in_args(cls, value: list[str]) -> list[str]:
        """`args` is appended after `command`'s one prompt element, never in its place."""
        if PROMPT_PLACEHOLDER in value:
            raise ValueError(
                f"must not contain {PROMPT_PLACEHOLDER}; the prompt's place is fixed by command"
            )
        return value

    @field_validator("skills_dir")
    @classmethod
    def _inside_the_working_directory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            check_relative_path(value)
        except PathRefused as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("invoke")
    @classmethod
    def _carries_the_task(cls, value: str | None) -> str | None:
        if value is not None and "{task}" not in value:
            raise ValueError('must contain "{task}"')
        return value


class Config(BaseModel):
    """Run defaults for `skill-lens run`.

    `default_runner` (`--runner`, a string or a list of names -- every case
    runs through each; the flag, repeated, replaces the whole list), `model`
    (`--model`), `judge_model` (`--judge-model`) and `min_pass_rate`
    (`--min-pass-rate`) can be overridden by a CLI flag; the rest can only be
    set here. Secrets are never read from
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

    `allow_scripts` is the trust switch for M6 part 2: a `SKILL.md` under
    evaluation is unvetted code, and running the scripts bundled with it is a
    decision the operator of the run states, never something an eval file can
    turn on. `--allow-scripts` / `--no-allow-scripts` override it in either
    direction. The four `script_*` keys are repository policy with no per-run
    reason to vary -- config-only, validated here, like the workspace caps.
    `script_timeout_seconds` must be finite as well as positive: TOML
    spells infinity as a bare `inf`, `gt=0` accepts it, and a timeout of
    infinity is no timeout at all.
    Setting them while `allow_scripts` is false is the normal state of a
    repository that turns execution on only in one CI job.

    `runners` holds one `[runners.<name>]` table per product runner or judge
    (`copilot`, `claude-code`, `cli`); see `ProductSettings`. Config-only:
    which product a repository evaluates under, and how, is repository
    policy.
    """

    model_config = ConfigDict(extra="forbid")

    default_runner: str | list[str] = "fake"
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
    allow_scripts: bool = False
    script_sandbox: SandboxMode = "auto"
    # allow_inf_nan=False: TOML has a bare `inf`, `gt=0` accepts it, and a
    # `wait(timeout=inf)` never expires -- the timeout would be off.
    script_timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS, gt=0, allow_inf_nan=False
    )
    max_script_output_bytes: int = Field(default=DEFAULT_MAX_OUTPUT_BYTES, gt=0)
    script_interpreters: dict[str, list[str]] = Field(
        default_factory=lambda: {ext: list(argv) for ext, argv in DEFAULT_INTERPRETERS.items()}
    )
    runners: dict[str, ProductSettings] = Field(default_factory=dict)

    @field_validator("default_runner")
    @classmethod
    def _runner_list_is_well_formed(cls, value: str | list[str]) -> str | list[str]:
        """A list names every runner once and names at least one.

        A duplicate is refused rather than collapsed: the same (skill, case)
        would enter the pass rate twice, weighting one framework's vote double
        under --repeat and --baseline. An empty list would run nothing, which
        the gate fails -- but as "no cases ran", far from the cause.
        """
        if isinstance(value, str):
            return value
        if not value:
            raise ValueError("names no runner; give one name or a non-empty list")
        seen: set[str] = set()
        for name in value:
            if name in seen:
                raise ValueError(f"names {name!r} twice; each runner runs every case once")
            seen.add(name)
        return value

    @field_validator("script_interpreters")
    @classmethod
    def _normalise_interpreters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        """Keys become a bare lower-case extension; every argv must be non-empty.

        `SkillBundle.script` looks up the lower-cased extension without its
        dot, so `".PY"` and `"py"` have to be the same key or an author's
        spelling would silently never match.
        """
        normalised: dict[str, list[str]] = {}
        for key, argv in value.items():
            extension = key.strip().lstrip(".").lower()
            if not extension:
                raise ValueError(f"script_interpreters has an empty extension key {key!r}")
            if not argv:
                raise ValueError(
                    f'script_interpreters.{key} is empty; name an interpreter, e.g. ["python3"]'
                )
            normalised[extension] = list(argv)
        return normalised

    def script_policy(self) -> ScriptPolicy:
        """The script settings as the orchestrator consumes them.

        Does not look at `allow_scripts`: the CLI resolves that against its
        flag and decides whether to pass the policy at all.
        """
        return ScriptPolicy(
            sandbox=self.script_sandbox,
            timeout_seconds=self.script_timeout_seconds,
            max_output_bytes=self.max_script_output_bytes,
            interpreters={ext: tuple(argv) for ext, argv in self.script_interpreters.items()},
        )

    @field_validator("runners")
    @classmethod
    def _product_tables_are_well_formed(
        cls, value: dict[str, ProductSettings]
    ) -> dict[str, ProductSettings]:
        for key, settings in value.items():
            if key not in PRODUCT_NAMES:
                raise ValueError(
                    f"runners.{key}: unknown product; expected one of {', '.join(PRODUCT_NAMES)}"
                )
            if key != "cli" and (settings.skills_dir is not None or settings.invoke is not None):
                raise ValueError(
                    f"runners.{key}: skills_dir and invoke are fixed by the {key} preset; "
                    "describe a product with different spellings under runners.cli"
                )
        return value

    def product(self, name: str) -> Product:
        """The product a runner or judge named `name` starts: preset plus its table.

        Checked here rather than at load time because the name can arrive
        from `default_runner`, `judge` or the `--runner` flag, and only the
        first two are visible to the model.
        """
        if name not in PRODUCT_NAMES:
            raise ConfigError(f"unknown product: {name}")
        settings = self.runners.get(name, ProductSettings())
        if name == "cli":
            if settings.command is None:
                raise ConfigError(
                    "runner cli needs [runners.cli] command in skill-lens.toml, "
                    'e.g. command = ["my-agent", "--prompt", "{prompt}"]'
                )
            base = Product(
                name="cli",
                argv=tuple(settings.command),
                skills_dir=settings.skills_dir or DEFAULT_SKILLS_DIR,
                invoke=settings.invoke or DEFAULT_INVOKE,
                parse=None,
                version_command=None,
            )
        else:
            base = PRESETS[name]
            if settings.command is not None:
                base = replace(base, argv=tuple(settings.command))
        return replace(
            base,
            argv=(*base.argv, *settings.args),
            timeout_seconds=settings.timeout_seconds,
            max_output_bytes=settings.max_output_bytes,
        )


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
