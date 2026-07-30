from pathlib import Path

import pytest

from skill_eval.config import Config, ConfigError, find_config_file, load_config

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
    path = tmp_path / "skill-eval.toml"
    path.write_text(TOML)
    config = load_config(path=path)
    assert config.min_pass_rate == 0.8
    assert config.fail_on_error is False
    assert config.per_skill_min == {"pdf": 1.0}


def test_discovers_config_by_searching_upward(tmp_path):
    (tmp_path / "skill-eval.toml").write_text(TOML)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config_file(nested) == tmp_path / "skill-eval.toml"
    assert load_config(start=nested).min_pass_rate == 0.8


def test_find_returns_none_when_absent(tmp_path):
    assert find_config_file(tmp_path) is None


def test_explicit_missing_path_raises(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(path=tmp_path / "nope.toml")


def test_malformed_toml_raises_with_path(tmp_path):
    path = tmp_path / "skill-eval.toml"
    path.write_text("min_pass_rate = [unclosed\n")
    with pytest.raises(ConfigError, match="skill-eval.toml"):
        load_config(path=path)


def test_unknown_keys_are_rejected(tmp_path):
    path = tmp_path / "skill-eval.toml"
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
    path = tmp_path / "skill-eval.toml"
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
    path/start must not see any real skill-eval.toml on the actual machine,
    since cwd has been chdir'd into an empty per-test directory.
    """
    assert load_config() == Config()


def test_non_ascii_config_loads_regardless_of_platform_encoding(tmp_path):
    # Regression test: config files are UTF-8; read_text() must pin the encoding
    # so a non-ASCII value doesn't fail under a non-UTF-8 platform default.
    path = tmp_path / "skill-eval.toml"
    path.write_text('default_runner = "fake"\n\n[per_skill_min]\n"café" = 1.0\n', encoding="utf-8")
    config = load_config(path=path)
    assert config.per_skill_min == {"café": 1.0}
