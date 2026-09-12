"""The LangChain judge -- the fourth and last module that imports a framework.

Everything the core sees is a plain `JudgeVerdict`. Provider failures are
reported through `JudgeVerdict.error`, never raised, so `JudgeEvaluator` can
tell an infra problem (errored) apart from a low score (failed).

The dependency check, the transient rule and the usage, model-name and cost
readers are imported from the runner adapter rather than duplicated, and the
retry loop comes from `runners/retry.py`: both modules are already inside the
framework boundary, and a second copy would be a second thing to keep in step.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from skill_lens.judges.prompt import SYSTEM_PROMPT, render_request
from skill_lens.models import JudgeOutput, JudgeRequest, JudgeVerdict
from skill_lens.runners.langchain import (
    DEFAULT_MODEL,
    _chat_model,
    _cost,
    _is_transient,
    _model_name,
    _require_langchain,
    _usage,
)
from skill_lens.runners.retry import run_with_retries


class LangChainJudge:
    """Grades a rubric with a real model, behind the framework-agnostic protocol."""

    name = "langchain"
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

    def _grade(self, request: JudgeRequest) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        # include_raw=True is the whole reason for this shape: without it
        # LangChain hands back only the parsed object, and tokens, cost and
        # the served model name -- what the report's judge-overhead line is
        # built from -- would be unreadable.
        grader = _chat_model(self._model, self._temperature).with_structured_output(
            JudgeOutput, include_raw=True
        )
        prompt = [SystemMessage(SYSTEM_PROMPT), HumanMessage(render_request(request))]
        return run_with_retries(
            lambda: grader.invoke(prompt),
            _is_transient,
            self._retries,
            self._retry_backoff_seconds,
            self._sleep,
        )

    def judge(self, request: JudgeRequest) -> JudgeVerdict:
        _require_langchain()
        configured = self._model if isinstance(self._model, str) else ""
        try:
            graded = self._grade(request)
            raw = graded["raw"]
            model_name = _model_name([raw], configured)
            parsed = graded.get("parsed")
            if parsed is None:
                # PydanticAI retries a malformed structured output internally;
                # LangChain hands it back. An unreadable verdict is an infra
                # signal, not a low score -- errored, never failed.
                return JudgeVerdict(
                    error=f"JudgeOutputInvalid: {graded.get('parsing_error')}", model=model_name
                )
            input_tokens, output_tokens = _usage([raw])
            cost_usd, cost_note = _cost(input_tokens, output_tokens, model_name, configured)
            return JudgeVerdict(
                checks=list(parsed.checks),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                cost_note=cost_note,
                model=model_name,
            )
        except Exception as exc:
            return JudgeVerdict(error=f"{type(exc).__name__}: {exc}", model=configured)
