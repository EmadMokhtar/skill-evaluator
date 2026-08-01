"""Cost capture must degrade, never explode."""

from dataclasses import dataclass

from skill_eval.runners.pricing import calculate_cost, provider_of


@dataclass
class Usage:
    input_tokens: int = 1000
    output_tokens: int = 500
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 1


def test_provider_is_the_prefix_of_the_model_string():
    assert provider_of("openai:gpt-4o-mini") == "openai"
    assert provider_of("anthropic:claude-sonnet-4-6") == "anthropic"
    assert provider_of("bare-model-name") == ""


def test_a_known_model_is_priced():
    cost, note = calculate_cost(Usage(), "gpt-4o-mini", "openai")
    assert cost > 0
    assert note == ""


def test_an_unknown_model_costs_zero_and_explains_why():
    # A missing price is a gap in a pricing table, not a failure of the run.
    cost, note = calculate_cost(Usage(), "no-such-model-exists", "openai")
    assert cost == 0.0
    assert "no price data" in note
    assert "no-such-model-exists" in note


def test_an_unknown_provider_costs_zero_and_explains_why():
    cost, note = calculate_cost(Usage(), "gpt-4o-mini", "")
    assert cost == 0.0
    assert note != ""
