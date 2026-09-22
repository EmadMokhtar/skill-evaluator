"""What a framework runner verifies before any spend.

Two things: the provider's API key is present, and every name a `trajectory:`
block uses is a tool the runner will actually offer the case.
"""

from __future__ import annotations

from collections.abc import Mapping

from skill_lens.models import EvalCase
from skill_lens.runners.pricing import provider_of
from skill_lens.runners.tools import BUILTIN_TOOL_NAMES

PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "CO_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class MissingAPIKey(Exception):
    """Raised when the environment variable a model needs is unset."""


class UnsupportedBaseURL(Exception):
    """Raised when a `base_url` is set for a model that cannot take one.

    A setup error, refused in the adapter's preflight before any spend: the
    provider's client has no endpoint to point at (or the model is an object
    the caller already built), so the URL would be silently ignored and the
    run would look local while spending against the provider's default.
    """


class UndeclaredTool(Exception):
    """Raised when a `trajectory:` names a tool the runner cannot offer the case.

    An authoring error, not an eval signal: a check on a tool the agent was
    never given can never pass, so it says nothing about the skill.
    """


def check_api_key(model: str, environ: Mapping[str, str]) -> None:
    """Raise MissingAPIKey when `model`'s provider has no key in `environ`.

    An unrecognised provider prefix is not blocked: reporting the provider's
    own error beats refusing a provider we have not catalogued.
    """
    variable = PROVIDER_ENV_VARS.get(provider_of(model))
    if variable is None:
        return
    if not environ.get(variable):
        raise MissingAPIKey(
            f"{variable} is not set, and model {model!r} needs it. "
            f"Export it in your environment -- skill-lens never reads secrets "
            f"from skill-lens.toml."
        )


def check_trajectory_names(runner: str, cases_by_skill: Mapping[str, list[EvalCase]]) -> None:
    """Raise UndeclaredTool unless every trajectory name is a tool the case offers.

    Covers `called`, `forbidden`, `order` and each `call_args` entry's `tool`.

    The rule is the runner's, not the loader's. A framework runner (`fake`,
    `pydantic-ai`, `langchain`) offers a case its mock `tools:` and, when
    the case has a `workspace:`, the six built-ins -- nothing else, so a
    name outside that set is a check that can never pass. A product runner
    offers the product's own tools, which skill-lens cannot list, so
    `ProductRunner.preflight` makes no such check. The loader cannot know
    which runner a case will run under -- one invocation may run the same
    case through both kinds -- so it accepts any name and each runner's
    `preflight` decides for the cases planned for it.

    `runner` is the runner's name, so a multi-runner invocation says which
    one refused.
    """
    for skill_name, cases in cases_by_skill.items():
        for case in cases:
            if case.trajectory is None:
                continue
            declared = {tool.name for tool in case.tools}
            if case.workspace is not None:
                # The built-ins are real tools the agent can call, so a
                # trajectory may name them -- but only in a case that has them.
                declared |= set(BUILTIN_TOOL_NAMES)
            for field_name, names in (
                ("called", case.trajectory.called),
                ("forbidden", case.trajectory.forbidden),
                ("order", case.trajectory.order),
                ("call_args", [entry.tool for entry in case.trajectory.call_args]),
            ):
                for name in names:
                    if name in declared:
                        continue
                    hint = (
                        " Built-in workspace and bundle tools only exist in a case with a "
                        "'workspace:' block."
                        if name in BUILTIN_TOOL_NAMES
                        else ""
                    )
                    raise UndeclaredTool(
                        f"runner {runner}: case {case.name!r} of skill {skill_name!r} "
                        f"trajectory.{field_name} names {name!r}, which is not declared in "
                        f"this case's tools.{hint}"
                    )
