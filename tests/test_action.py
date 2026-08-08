"""Keep action.yml and the CLI from drifting apart.

Two documented surfaces are only safe if something fails the build when they
disagree. The second test is the one that matters: it fires when a future flag
is added to `run` and the action is forgotten.
"""

from __future__ import annotations

from pathlib import Path

from typer.main import get_command

from skill_eval.cli import app
from skill_eval.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION = REPO_ROOT / "action.yml"

# Inputs about the environment the action runs in, not about the run itself.
ENVIRONMENT_INPUTS = {"install-spec", "working-directory", "step-summary"}

# `path` is a positional argument, not an option.
ARGUMENT_INPUTS = {"path"}

IGNORED_FLAGS = {"--help", "--install-completion", "--show-completion"}


def _action() -> dict:
    return safe_load(ACTION.read_text(encoding="utf-8"))


def _action_inputs() -> set[str]:
    return set(_action()["inputs"])


def _run_flags() -> set[str]:
    command = get_command(app).commands["run"]
    return {
        opt
        for param in command.params
        if param.param_type_name == "option"
        for opt in param.opts
        if opt.startswith("--") and opt not in IGNORED_FLAGS
    }


def test_every_action_input_maps_to_a_real_cli_flag():
    flags = _run_flags()
    for name in _action_inputs() - ENVIRONMENT_INPUTS - ARGUMENT_INPUTS:
        assert f"--{name}" in flags, f"action input {name!r} has no matching CLI flag"


def test_every_cli_flag_is_exposed_as_an_action_input():
    inputs = _action_inputs()
    for flag in _run_flags():
        name = flag.removeprefix("--")
        assert name in inputs, f"CLI flag {flag} is not exposed as an action.yml input"


def test_every_action_input_is_described():
    for name, spec in _action()["inputs"].items():
        assert spec.get("description"), f"action input {name!r} has no description"


def test_the_action_declares_its_report_outputs():
    outputs = set(_action()["outputs"])
    assert {"exit-code", "passed", "pass-rate"} <= outputs


def test_every_cli_backed_input_is_actually_forwarded_to_the_command():
    """Name-matching alone would pass an input that is declared, wired into the
    step's env, and then never handed to the CLI -- it would silently do nothing.
    """
    step = next(s for s in _action()["runs"]["steps"] if s.get("id") == "run")
    script = step["run"]
    env = step.get("env", {})
    for name in _action_inputs() - ENVIRONMENT_INPUTS - ARGUMENT_INPUTS:
        reference = "${{ inputs." + name + " }}"
        variable = next((k for k, v in env.items() if v.strip() == reference), None)
        assert variable is not None, f"input {name!r} is not exposed to the run step's env"
        assert f'add --{name} "${variable}"' in script, (
            f"input {name!r} reaches the step as ${variable} but is never passed to skill-eval"
        )
