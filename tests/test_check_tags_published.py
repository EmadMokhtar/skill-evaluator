"""Unit tests for scripts/check_tags_published.py.

The script is not an importable package, so it is loaded from its path, the
way tests/test_check_docs_updated.py loads its script. The pure rules are
tested directly; the network and git calls are replaced on the module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_tags_published.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_tags_published", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: a dataclass under `from __future__ import
    # annotations` resolves its field types through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()

V = check.TagFormat("v", "")


def T(name: str, version: str, commit: str = "0" * 40):
    return check.ReleaseTag(name, version, commit)


# --- pyproject -----------------------------------------------------------------


def test_tag_format_derives_the_version_from_the_prefix_and_suffix():
    fmt = check.read_tag_format('[tool.commitizen]\ntag_format = "v$version"\n')
    assert fmt == check.TagFormat("v", "")
    assert fmt.version("v0.21.0") == "0.21.0"
    assert fmt.version("v0.22.0rc1") == "0.22.0rc1"
    assert fmt.version("0.21.0") is None, "no prefix"
    assert fmt.version("v0") is None, "not a version"
    assert check.TagFormat("rel-", "-final").version("rel-1.2.3-final") == "1.2.3"
    assert check.TagFormat("rel-", "-final").version("rel-1.2.3") is None, "no suffix"


def test_a_tag_format_without_the_version_placeholder_is_unavailable():
    with pytest.raises(check.CheckUnavailable):
        check.read_tag_format('[tool.commitizen]\ntag_format = "release"\n')


def test_a_pyproject_without_the_keys_is_unavailable():
    with pytest.raises(check.CheckUnavailable):
        check.read_tag_format('[project]\nname = "x"\n')
    with pytest.raises(check.CheckUnavailable):
        check.read_project_name('[tool.commitizen]\ntag_format = "v$version"\n')


def test_the_real_pyproject_parses():
    text = check.PYPROJECT.read_text(encoding="utf-8")
    assert check.read_project_name(text) == "skill-lens"
    assert check.read_tag_format(text).version("v0.21.0") == "0.21.0"


# --- git ls-remote -------------------------------------------------------------


def test_parse_ls_remote_prefers_the_peeled_commit_and_sorts_by_version():
    output = (
        "aaa\trefs/tags/v0.10.0\n"
        "bbb\trefs/tags/v0.10.0^{}\n"
        "ccc\trefs/tags/v0.9.1\n"
        "ddd\trefs/tags/v0\n"
        "eee\trefs/tags/other\n"
        "fff\trefs/heads/main\n"
    )
    tags, ignored = check.parse_ls_remote(output, V)
    assert tags == [T("v0.9.1", "0.9.1", "ccc"), T("v0.10.0", "0.10.0", "bbb")]
    # `other` is outside the prefix and so not a release tag at all; `v0` is
    # under it and not a version, so it is reported rather than dropped.
    assert ignored == ["v0"]


def test_parse_ls_remote_with_no_tags_is_empty():
    assert check.parse_ls_remote("", V) == ([], [])


# --- PyPI ----------------------------------------------------------------------


def test_pypi_versions_separates_releases_with_files_from_empty_ones():
    payload = {"releases": {"0.20.0": [{"filename": "a.whl"}], "0.21.0": [], "0.22.0": [{}]}}
    assert check.pypi_versions(payload) == ({"0.20.0", "0.22.0"}, {"0.21.0"})


def test_pypi_versions_of_a_project_with_no_releases_is_empty():
    assert check.pypi_versions({"releases": {}}) == (set(), set())
    assert check.pypi_versions({}) == (set(), set())


# --- spent-tags.txt ------------------------------------------------------------


def test_parse_spent_reads_tags_with_reasons_and_skips_comments():
    text = "# header\n\nv0.3.0  # 2026-01-01: build failed after the tag\nv0.4.0\n"
    assert check.parse_spent(text) == {
        "v0.3.0": "2026-01-01: build failed after the tag",
        "v0.4.0": "",
    }


def test_the_spent_file_exists_and_lists_nothing():
    """v0.21.0 is recoverable (its run can be re-run), so it is not spent."""
    assert check.parse_spent(check.SPENT_FILE.read_text(encoding="utf-8")) == {}


# --- the verdict ---------------------------------------------------------------


def test_assess_sorts_every_tag_into_one_bucket():
    tags = [
        T("v0.1.0", "0.1.0"),
        T("v0.2.0", "0.2.0"),
        T("v0.3.0", "0.3.0"),
        T("v0.4.0", "0.4.0"),
        T("v0.5.0", "0.5.0"),
    ]
    verdict = check.assess(
        tags,
        with_files={"0.1.0", "0.3.0"},
        without_files={"0.5.0"},
        spent={"v0.2.0": "spent", "v0.3.0": "stale"},
    )
    assert verdict.published == 1
    assert [t.name for t in verdict.accepted] == ["v0.2.0"]
    assert [t.name for t in verdict.stale] == ["v0.3.0"]
    assert [t.name for t in verdict.missing] == ["v0.4.0"]
    assert [t.name for t in verdict.empty] == ["v0.5.0"]


def test_assess_with_everything_published_has_no_findings():
    verdict = check.assess([T("v1.0.0", "1.0.0")], {"1.0.0"}, set(), {})
    assert verdict == check.Verdict([], [], [], [], 1)
