import skill_eval


def test_version_is_exposed():
    assert isinstance(skill_eval.__version__, str)
    assert skill_eval.__version__
