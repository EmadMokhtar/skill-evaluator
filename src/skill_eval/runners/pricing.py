"""Turn provider usage into USD, without ever failing a run over it."""

from __future__ import annotations

from typing import Any


def provider_of(model: str) -> str:
    """The provider prefix of a model string: 'openai:gpt-4o-mini' -> 'openai'."""
    return model.split(":", 1)[0] if ":" in model else ""


def calculate_cost(usage: Any, model_name: str, provider_id: str) -> tuple[float, str]:
    """Price `usage`, returning (cost_usd, note).

    A blank note means the price is real. Any lookup problem -- an unpriced
    model, an unknown provider, a missing pricing package -- yields 0.0 and an
    explanatory note, because cost is reporting metadata and must never be the
    reason a run errors.
    """
    try:
        from genai_prices import calc_price
    except ImportError:  # pragma: no cover - genai-prices ships with pydantic-ai
        return 0.0, "genai-prices is not installed; cost not calculated"

    try:
        calculation = calc_price(usage, model_name, provider_id=provider_id)
    except Exception as exc:
        label = f"{provider_id}:{model_name}" if provider_id else model_name
        return 0.0, f"no price data for {label} ({type(exc).__name__})"

    return float(calculation.total_price), ""
