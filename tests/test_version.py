import skill_lens


def test_version_is_exposed():
    assert isinstance(skill_lens.__version__, str)
    assert skill_lens.__version__
