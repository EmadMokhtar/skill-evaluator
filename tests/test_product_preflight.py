"""Everything a product runner refuses before any case runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from skill_lens.cases.loader import parse_cases_file
from skill_lens.models import EvalCase, Skill, ToolSpec, TrajectorySpec
from skill_lens.runners.product import TRUST_NOTE, Product, ProductRunner, ProductSetupError
from skill_lens.runners.traces import parse_claude_code

FAKE = Path(__file__).parent / "fake_product.py"


def _product(**overrides) -> Product:
    fields = dict(
        name="claude-code",
        argv=(sys.executable, str(FAKE), "-p", "{prompt}"),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse_claude_code,
        version_command=(sys.executable, str(FAKE), "--version"),
    )
    fields.update(overrides)
    return Product(**fields)


def _skill(name="ping") -> Skill:
    return Skill(name=name, description="d", instructions="i", path=Path("/tmp/x"), markdown="m")


def _case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "t")
    return EvalCase(**kwargs)


def test_a_clean_run_records_the_executable_version_and_trust():
    status = ProductRunner(_product()).preflight([_skill()], {"ping": [_case()]})
    assert status.name == "claude-code"
    assert status.executable == sys.executable
    assert status.version == "fake 1.2.3"
    assert status.trust == TRUST_NOTE


def test_a_missing_executable_names_the_runner_and_the_command():
    product = _product(argv=("no-such-product-xyz", "-p", "{prompt}"), version_command=None)
    with pytest.raises(
        ProductSetupError, match=r"runner claude-code: 'no-such-product-xyz' is not on PATH"
    ):
        ProductRunner(product).preflight([], {})


def test_the_message_says_where_a_cli_command_is_configured():
    product = _product(
        name="cli", argv=("no-such-product-xyz", "{prompt}"), parse=None, version_command=None
    )
    with pytest.raises(ProductSetupError, match=r"\[runners\.cli\] command"):
        ProductRunner(product).preflight([], {})


def test_a_version_probe_that_fails_is_a_setup_error(monkeypatch):
    monkeypatch.setenv("FAKE_PRODUCT_VERSION_FAILS", "1")
    with pytest.raises(
        ProductSetupError, match=r"runner claude-code: .*--version exited with code 1: cannot start"
    ):
        ProductRunner(_product()).preflight([], {})


def test_a_generic_product_has_no_version_probe():
    product = _product(name="cli", parse=None, version_command=None)
    assert ProductRunner(product).preflight([], {}).version == ""


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b"])
def test_a_skill_name_that_is_not_one_directory_name_is_refused(name):
    with pytest.raises(ProductSetupError, match=r"cannot be a directory name"):
        ProductRunner(_product()).preflight([_skill(name)], {name: [_case()]})


def test_a_case_with_mock_tools_is_refused_naming_case_and_runner():
    case = _case(name="uses tools", tools=[ToolSpec(name="lookup")])
    with pytest.raises(ProductSetupError) as info:
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})
    assert "case 'uses tools' of skill 'ping' declares tools:" in str(info.value)
    assert "claude-code runner cannot provide" in str(info.value)


def test_a_generic_product_refuses_trajectory_and_offered():
    product = _product(name="cli", parse=None, version_command=None)
    with pytest.raises(ProductSetupError, match=r"trajectory:.*cli runner records no tool calls"):
        ProductRunner(product).preflight(
            [_skill()], {"ping": [_case(trajectory=TrajectorySpec(called=["x"]))]}
        )
    with pytest.raises(ProductSetupError, match=r"mode: offered.*cli runner cannot observe"):
        ProductRunner(product).preflight([_skill()], {"ping": [_case(mode="offered")]})


def test_a_preset_accepts_trajectory_and_offered():
    cases = [_case(trajectory=TrajectorySpec(called=["Bash"])), _case(name="o", mode="offered")]
    ProductRunner(_product()).preflight([_skill()], {"ping": cases})  # no raise


def test_only_the_cases_given_are_inspected():
    # A case the orchestrator filtered out never reaches preflight; nothing
    # here re-discovers it.
    ProductRunner(_product()).preflight([_skill()], {})  # no raise, no cases


def test_a_referenced_tool_is_refused_like_an_inline_one(tmp_path):
    # `ref:` resolves in the case loader, so preflight sees a ToolSpec and
    # refuses it with the same message -- nothing product-specific to add.
    (tmp_path / "lib.yaml").write_text("tools:\n  - name: lookup\n", encoding="utf-8")
    path = tmp_path / "ping.eval.yaml"
    path.write_text(
        "tool_libraries: [lib.yaml]\ncases:\n  - name: uses tools\n    task: t\n"
        "    tools:\n      - ref: lookup\n",
        encoding="utf-8",
    )
    (case,) = parse_cases_file(path)
    with pytest.raises(
        ProductSetupError, match="case 'uses tools' of skill 'ping' declares tools:"
    ):
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})
