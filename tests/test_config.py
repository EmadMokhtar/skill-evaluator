import pytest

from skill_eval.config import Config, ConfigError, find_config_file, load_config

TOML = """
default_runner = "fake"
min_pass_rate = 0.8
fail_on_error = false
reporters = ["console", "json"]

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
    assert config.reporters == ["console", "json"]
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


def test_explicit_directory_path_raises_config_error(tmp_path):
    directory = tmp_path / "config_dir"
    directory.mkdir()
    with pytest.raises(ConfigError, match="is not a file"):
        load_config(path=directory)
