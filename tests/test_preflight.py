"""Check for the key before spending, not after."""

import pytest

from skill_eval.runners.preflight import MissingAPIKey, check_api_key


def test_a_present_key_passes():
    check_api_key("openai:gpt-4o-mini", {"OPENAI_API_KEY": "sk-test"})


def test_a_missing_key_is_reported_with_the_variable_name():
    with pytest.raises(MissingAPIKey) as exc:
        check_api_key("openai:gpt-4o-mini", {})
    assert "OPENAI_API_KEY" in str(exc.value)


def test_an_empty_key_counts_as_missing():
    with pytest.raises(MissingAPIKey):
        check_api_key("anthropic:claude-sonnet-4-6", {"ANTHROPIC_API_KEY": ""})


def test_the_message_never_suggests_putting_secrets_in_config():
    with pytest.raises(MissingAPIKey) as exc:
        check_api_key("openai:gpt-4o-mini", {})
    message = str(exc.value)
    assert "skill-eval.toml" in message
    assert "never" in message.lower()


def test_an_unknown_provider_is_not_blocked():
    # Better to attempt the run and report the provider's own error than to
    # refuse a provider we simply have not catalogued.
    check_api_key("some-new-provider:model", {})


def test_a_model_without_a_provider_prefix_is_not_blocked():
    check_api_key("gpt-4o-mini", {})
