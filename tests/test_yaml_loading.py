from skill_eval.yaml_loading import StrictBoolLoader, safe_load


def test_bare_yes_no_on_off_parse_as_strings():
    data = safe_load("a: yes\nb: no\nc: on\nd: off\n")
    assert data == {"a": "yes", "b": "no", "c": "on", "d": "off"}


def test_true_false_still_parse_as_bool():
    data = safe_load("a: true\nb: false\nc: True\nd: FALSE\n")
    assert data == {"a": True, "b": False, "c": True, "d": False}


def test_strict_bool_loader_is_a_safe_loader_subclass():
    import yaml

    assert issubclass(StrictBoolLoader, yaml.SafeLoader)
