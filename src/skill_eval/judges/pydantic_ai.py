"""The PydanticAI judge — the second and last module that imports a framework.

Everything the core sees is a plain `JudgeVerdict`. Provider failures are
reported through `JudgeVerdict.error`, never raised, so `JudgeEvaluator` can
tell an infra problem (errored) apart from a low score (failed).

The transient-retry, dependency-check and model-name helpers are imported from
the runner adapter rather than duplicated: both modules are already inside the
framework boundary, and a second copy of the retry policy would be a second
thing to keep in step.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from skill_eval.judges.prompt import SYSTEM_PROMPT, render_request
from skill_eval.models import JudgeOutput, JudgeRequest, JudgeVerdict
from skill_eval.runners.pricing import calculate_cost, provider_of
from skill_eval.runners.pydantic_ai import (
    DEFAULT_MODEL,
    _is_transient,
    _model_name,
    _require_pydantic_ai,
)


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
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._retries = retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep

    def _model_settings(self) -> Any:
        """Temperature 0 for determinism; 'unset' for models that reject it."""
        from pydantic_ai.settings import ModelSettings

        if self._temperature == "unset":
            return None
        return ModelSettings(temperature=float(self._temperature))

    def _build_agent(self) -> Any:
        from pydantic_ai import Agent

        return Agent(self._model, instructions=SYSTEM_PROMPT, output_type=JudgeOutput)

    def _run_with_retries(self, agent: Any, prompt: str) -> Any:
        settings = self._model_settings()
        delay = self._retry_backoff_seconds
        attempt = 0
        while True:
            try:
                return agent.run_sync(prompt, model_settings=settings)
            except Exception as exc:
                if attempt >= self._retries or not _is_transient(exc):
                    raise
                self._sleep(delay)
                delay *= 2
                attempt += 1

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
