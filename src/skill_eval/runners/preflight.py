"""Verify the provider's API key is present before any spend."""

from __future__ import annotations

from collections.abc import Mapping

from skill_eval.runners.pricing import provider_of

PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google-gla": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "CO_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class MissingAPIKey(Exception):
    """Raised when the environment variable a model needs is unset."""


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
            f"Export it in your environment -- skill-eval never reads secrets "
            f"from skill-eval.toml."
        )
