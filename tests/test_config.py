from pathlib import Path

import pytest

from skill_lens.config import Config, ConfigError, find_config_file, load_config
from skill_lens.workspace import DEFAULT_LIMITS

TOML = """
default_runner = "fake"
min_pass_rate = 0.8
fail_on_error = false

[per_skill_min]
pdf = 1.0
"""


def test_defaults_when_no_config_file(tmp_path):
    config = load_config(start=tmp_path)
    assert config == Config()
    assert config.min_pass_rate == 1.0
    assert config.fail_on_error is True
    assert config.default_runner == "fake"


def test_loads_values_from_an_explicit_path(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text(TOML)
    config = load_config(path=path)
    assert config.min_pass_rate == 0.8
    assert config.fail_on_error is False
    assert config.per_skill_min == {"pdf": 1.0}


def test_discovers_config_by_searching_upward(tmp_path):
    (tmp_path / "skill-lens.toml").write_text(TOML)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config_file(nested) == tmp_path / "skill-lens.toml"
    assert load_config(start=nested).min_pass_rate == 0.8


def test_find_returns_none_when_absent(tmp_path):
    assert find_config_file(tmp_path) is None


def test_explicit_missing_path_raises(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(path=tmp_path / "nope.toml")


def test_malformed_toml_raises_with_path(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text("min_pass_rate = [unclosed\n")
    with pytest.raises(ConfigError, match="skill-lens.toml"):
        load_config(path=path)


def test_unknown_keys_are_rejected(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text('mistyped_key = "x"\n')
    with pytest.raises(ConfigError, match="mistyped_key"):
        load_config(path=path)


def test_reporters_field_was_removed_and_is_now_rejected(tmp_path):
    """Item 6: Config.reporters was validated but completely ignored -- the
    CLI hardcodes console output and keys JSON off --json-output. Rather
    than leave a validated-but-inert config key that silently misleads
    users, it is removed until M4 reintroduces it behind a real reporter
    registry. A config file that still sets it must now be rejected as an
    unknown key, same as any other typo.
    """
    path = tmp_path / "skill-lens.toml"
    path.write_text('reporters = ["console", "json"]\n')
    with pytest.raises(ConfigError, match="reporters"):
        load_config(path=path)


def test_explicit_directory_path_raises_config_error(tmp_path):
    directory = tmp_path / "config_dir"
    directory.mkdir()
    with pytest.raises(ConfigError, match="is not a file"):
        load_config(path=directory)


def test_isolate_cwd_fixture_chdirs_into_a_fresh_tmp_path(tmp_path):
    """Item 5: an autouse fixture in tests/conftest.py must chdir into an
    empty tmp_path before every test, so load_config's upward-search fallback
    (Path.cwd() to /) never depends on ambient files in whatever directory
    pytest happened to be invoked from.
    """
    assert Path.cwd() == tmp_path.resolve()


def test_default_discovery_with_no_explicit_start_uses_isolated_cwd():
    """With the isolation fixture active, load_config() with no explicit
    path/start must not see any real skill-lens.toml on the actual machine,
    since cwd has been chdir'd into an empty per-test directory.
    """
    assert load_config() == Config()


def test_non_ascii_config_loads_regardless_of_platform_encoding(tmp_path):
    # Regression test: config files are UTF-8; read_text() must pin the encoding
    # so a non-ASCII value doesn't fail under a non-UTF-8 platform default.
    path = tmp_path / "skill-lens.toml"
    path.write_text('default_runner = "fake"\n\n[per_skill_min]\n"café" = 1.0\n', encoding="utf-8")
    config = load_config(path=path)
    assert config.per_skill_min == {"café": 1.0}


def test_non_utf8_config_raises_config_error_not_raw_decode_error(tmp_path):
    # Regression test: read_text() sits outside the TOMLDecodeError guard, so an
    # unreadable or non-UTF-8 config surfaced a raw UnicodeDecodeError traceback
    # instead of the clean exit-2 authoring error the loaders already produce.
    path = tmp_path / "skill-lens.toml"
    path.write_bytes(b'default_runner = "\xff\xfe"\n')
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(path=path)


def test_config_reads_model_and_retry_settings(tmp_path):
    config_file = tmp_path / "skill-lens.toml"
    config_file.write_text(
        'default_runner = "pydantic-ai"\n'
        'model = "openai:gpt-4.1-mini"\n'
        "temperature = 0.2\n"
        "retries = 3\n"
        "retry_backoff_seconds = 0.5\n",
        encoding="utf-8",
    )
    settings = load_config(path=config_file)
    assert settings.default_runner == "pydantic-ai"
    assert settings.model == "openai:gpt-4.1-mini"
    assert settings.temperature == 0.2
    assert settings.retries == 3
    assert settings.retry_backoff_seconds == 0.5


def test_config_defaults_never_spend_money():
    settings = Config()
    assert settings.default_runner == "fake"
    assert settings.model == "openai:gpt-4o-mini"
    assert settings.temperature == 0.0
    assert settings.retries == 2


def test_config_temperature_accepts_unset_for_reasoning_models(tmp_path):
    # TOML has no null literal, and omitting the key must keep meaning "use the
    # default", so "unset" is the only way to say "send no temperature at all" --
    # which GPT-5-family and o-series models require.
    config_file = tmp_path / "skill-lens.toml"
    config_file.write_text('temperature = "unset"\n', encoding="utf-8")
    assert load_config(path=config_file).temperature == "unset"


def test_config_rejects_a_nonsense_temperature(tmp_path):
    config_file = tmp_path / "skill-lens.toml"
    config_file.write_text('temperature = "hot"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=config_file)


def test_the_built_in_judge_default_never_spends_money():
    settings = load_config()
    assert settings.judge == "fake"
    assert settings.judge_model == ""


def test_a_project_can_opt_into_real_judging(tmp_path):
    (tmp_path / "skill-lens.toml").write_text(
        'judge = "pydantic-ai"\njudge_model = "openai:gpt-4o"\n', encoding="utf-8"
    )
    settings = load_config(path=tmp_path / "skill-lens.toml")
    assert settings.judge == "pydantic-ai"
    assert settings.judge_model == "openai:gpt-4o"


def test_an_unknown_config_key_is_still_rejected(tmp_path):
    (tmp_path / "skill-lens.toml").write_text('judg = "pydantic-ai"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=tmp_path / "skill-lens.toml")


def test_the_comparative_fields_have_safe_defaults():
    settings = Config()
    assert settings.baseline == ""
    assert settings.repeat == 1
    assert settings.min_delta is None


def test_an_unknown_baseline_kind_is_a_config_error(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text('baseline = "yesterday"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=path)


def test_concurrency_defaults_to_one_and_can_be_set(tmp_path):
    """Default 1 for the same reason judge defaults to "fake": upgrading must
    never change spend or behavior on its own."""
    assert Config().concurrency == 1
    path = tmp_path / "skill-lens.toml"
    path.write_text("concurrency = 8\n", encoding="utf-8")
    assert load_config(path=path).concurrency == 8


def test_workspace_settings_have_the_documented_defaults():
    settings = Config()
    assert settings.keep_workspace is False
    assert settings.max_file_bytes == DEFAULT_LIMITS.max_file_bytes
    assert settings.max_files == DEFAULT_LIMITS.max_files
    assert settings.max_total_bytes == DEFAULT_LIMITS.max_total_bytes


def test_workspace_settings_parse(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text(
        "keep_workspace = true\nmax_file_bytes = 42\nmax_files = 3\nmax_total_bytes = 99\n",
        encoding="utf-8",
    )
    settings = load_config(path=path)
    assert settings.keep_workspace is True
    assert (settings.max_file_bytes, settings.max_files, settings.max_total_bytes) == (42, 3, 99)


@pytest.mark.parametrize("key", ["max_file_bytes", "max_files", "max_total_bytes"])
@pytest.mark.parametrize("value", [0, -1])
def test_a_non_positive_cap_is_a_config_error(tmp_path, key, value):
    path = tmp_path / "skill-lens.toml"
    path.write_text(f"{key} = {value}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=path)


def test_full_output_defaults_off_and_loads_from_the_file(tmp_path):
    assert Config().full_output is False
    path = tmp_path / "skill-lens.toml"
    path.write_text("full_output = true\n", encoding="utf-8")
    assert load_config(path=path).full_output is True


def test_script_defaults_are_off_auto_and_the_documented_numbers(tmp_path):
    config = load_config(start=tmp_path)
    assert config.allow_scripts is False
    assert config.script_sandbox == "auto"
    assert config.script_timeout_seconds == 30.0
    assert config.max_script_output_bytes == 20_000
    assert config.script_interpreters == {"py": ["python3"], "sh": ["bash"]}


def test_script_keys_load_from_toml(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text(
        'allow_scripts = true\nscript_sandbox = "required"\nscript_timeout_seconds = 5\n'
        "max_script_output_bytes = 100\n\n[script_interpreters]\n"
        'py = ["python3", "-X", "utf8"]\nrb = ["ruby"]\n',
        encoding="utf-8",
    )
    config = load_config(path=path)
    assert config.allow_scripts is True
    assert config.script_sandbox == "required"
    assert config.script_timeout_seconds == 5.0
    assert config.max_script_output_bytes == 100
    # A partial table replaces the default, like any TOML table would.
    assert config.script_interpreters == {"py": ["python3", "-X", "utf8"], "rb": ["ruby"]}


def test_interpreter_keys_are_normalised_to_a_bare_lower_case_extension(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text('[script_interpreters]\n".PY" = ["python3"]\n', encoding="utf-8")
    assert load_config(path=path).script_interpreters == {"py": ["python3"]}


@pytest.mark.parametrize(
    "toml",
    [
        "[script_interpreters]\npy = []\n",
        '[script_interpreters]\n"" = ["python3"]\n',
        'script_sandbox = "firejail"\n',
        "script_timeout_seconds = 0\n",
        # TOML spells infinity and not-a-number as bare words, and `gt=0`
        # alone lets `inf` through; a `wait(timeout=inf)` never expires.
        "script_timeout_seconds = inf\n",
        "script_timeout_seconds = nan\n",
        "max_script_output_bytes = -1\n",
    ],
)
def test_invalid_script_settings_are_config_errors(tmp_path, toml):
    path = tmp_path / "skill-lens.toml"
    path.write_text(toml, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=path)


def test_script_policy_carries_every_setting_as_tuples(tmp_path):
    path = tmp_path / "skill-lens.toml"
    path.write_text(
        'script_sandbox = "off"\nscript_timeout_seconds = 2\nmax_script_output_bytes = 9\n'
        '[script_interpreters]\npy = ["python3", "-B"]\n',
        encoding="utf-8",
    )
    policy = load_config(path=path).script_policy()
    assert policy.sandbox == "off"
    assert policy.timeout_seconds == 2.0
    assert policy.max_output_bytes == 9
    assert policy.interpreters == {"py": ("python3", "-B")}
