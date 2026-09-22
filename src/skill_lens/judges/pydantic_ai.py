"""The PydanticAI judge — the second and last module that imports a framework.

Everything the core sees is a plain `JudgeVerdict`. Provider failures are
reported through `JudgeVerdict.error`, never raised, so `JudgeEvaluator` can
tell an infra problem (errored) apart from a low score (failed).

The transient rule, dependency check and model-name helpers are imported from
the runner adapter rather than duplicated, and the retry loop itself comes from
`runners/retry.py`: both modules are already inside the framework boundary, and
a second copy of the policy would be a second thing to keep in step.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from skill_lens.judges.prompt import SYSTEM_PROMPT, render_request
from skill_lens.models import JudgeOutput, JudgeRequest, JudgeVerdict
from skill_lens.runners.preflight import UnsupportedBaseURL
from skill_lens.runners.pricing import calculate_cost, provider_of
from skill_lens.runners.pydantic_ai import (
    DEFAULT_MODEL,
    _is_transient,
    _model_name,
    _require_pydantic_ai,
    resolve_model,
)
from skill_lens.runners.retry import run_with_retries


class PydanticAIJudge:
    """Grades a rubric with a real model, behind the framework-agnostic protocol."""

    name = "pydantic-ai"
    needs_api_key = True

    def __init__(
        self,
        model: Any = DEFAULT_MODEL,
        temperature: float | str = 0.0,
        retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = "",
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._retries = retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep
        self._base_url = base_url

    def preflight(self) -> None:
        """Refuse a `base_url` the judge's provider cannot take, before any spend.

        Resolving the model builds a client and nothing more, so the refusal
        lands here as a setup error rather than from the first rubric as an
        errored case. Returns None: a keyed judge has no product status.
        """
        if not self._base_url:
            return
        _require_pydantic_ai()
        try:
            resolve_model(self._model, self._base_url)
        except UnsupportedBaseURL as exc:
            raise UnsupportedBaseURL(f"judge {self.name}: {exc}") from exc
        except Exception as exc:
            # The framework's own refusal -- an unknown provider prefix, a
            # model id with no prefix -- has no RunResult to land in here, so
            # it becomes the setup error rather than a traceback.
            raise UnsupportedBaseURL(
                f"judge {self.name}: base_url {self._base_url!r} cannot apply to model "
                f"{self._model!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def _model_settings(self) -> Any:
        """Temperature 0 for determinism; 'unset' for models that reject it."""
        from pydantic_ai.settings import ModelSettings

        if self._temperature == "unset":
            return None
        return ModelSettings(temperature=float(self._temperature))

    def _build_agent(self) -> Any:
        from pydantic_ai import Agent

        return Agent(
            resolve_model(self._model, self._base_url),
            instructions=SYSTEM_PROMPT,
            output_type=JudgeOutput,
        )

    def _run_with_retries(self, agent: Any, prompt: str) -> Any:
        settings = self._model_settings()
        return run_with_retries(
            lambda: agent.run_sync(prompt, model_settings=settings),
            _is_transient,
            self._retries,
            self._retry_backoff_seconds,
            self._sleep,
        )

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        _require_pydantic_ai()
        configured = self._model if isinstance(self._model, str) else ""
        try:
            result = self._run_with_retries(self._build_agent(), render_request(request))
            messages = result.all_messages()
            usage = result.usage
            model_name = _model_name(messages, configured)
            cost_usd, cost_note = calculate_cost(usage, model_name, provider_of(configured))
            return JudgeVerdict(
                checks=list(result.output.checks),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=cost_usd,
                cost_note=cost_note,
                model=model_name,
            )
        except Exception as exc:
            return JudgeVerdict(error=f"{type(exc).__name__}: {exc}", model=configured)
