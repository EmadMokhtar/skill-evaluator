"""Keep action.yml and the CLI from drifting apart.

Two documented surfaces are only safe if something fails the build when they
disagree. The second test is the one that matters: it fires when a future flag
is added to `run` and the action is forgotten.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.main import get_command

from skill_lens.cli import app
from skill_lens.yaml_loading import safe_load

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

    Most inputs pass through the `add` helper as `--flag value`. A three-state
    boolean flag (present, absent, or its `--no-` twin) cannot be expressed that
    way, so those go through `add_flag` instead -- either forwarding form counts
    as "actually reaches the CLI". A list input (`runner`) goes through
    `add_list`, which emits the flag once per comma-separated entry.
    """
    step = next(s for s in _action()["runs"]["steps"] if s.get("id") == "run")
    script = step["run"]
    env = step.get("env", {})
    for name in _action_inputs() - ENVIRONMENT_INPUTS - ARGUMENT_INPUTS:
        reference = "${{ inputs." + name + " }}"
        variable = next((k for k, v in env.items() if v.strip() == reference), None)
        assert variable is not None, f"input {name!r} is not exposed to the run step's env"
        assert (
            f'add --{name} "${variable}"' in script
            or f'add_flag --{name} "${variable}"' in script
            or f'add_list --{name} "${variable}"' in script
        ), f"input {name!r} reaches the step as ${variable} but is never passed to skill-lens"


def _bash_helper(script: str, name: str) -> str:
    """One function definition out of the step's script, brace to brace.

    The `run: |` block is dedented by YAML, so helpers start at column 0 and
    close with a `}` on its own line.
    """
    start = script.index(f"{name}() {{")
    end = script.index("\n}", start) + len("\n}")
    return script[start:end]


def _split_by_the_action(value: str) -> list[str]:
    step = next(s for s in _action()["runs"]["steps"] if s.get("id") == "run")
    helper = _bash_helper(step["run"], "add_list")
    # A loop, not one `printf "%s\\n" "${{args[@]}}"`: printf with no arguments
    # still prints one empty line, which would make an empty result look like
    # a single empty flag.
    probe = (
        f'{helper}\nargs=()\nadd_list --runner "$1"\n'
        'for a in "${args[@]}"; do printf "%s\\n" "$a"; done'
    )
    completed = subprocess.run(
        ["bash", "-c", probe, "bash", value], capture_output=True, text=True, check=True
    )
    return completed.stdout.split("\n")[:-1]


def test_a_comma_separated_runner_input_becomes_one_flag_per_entry():
    assert _split_by_the_action("pydantic-ai, langchain") == [
        "--runner",
        "pydantic-ai",
        "--runner",
        "langchain",
    ]


def test_a_single_runner_input_is_forwarded_as_before():
    assert _split_by_the_action("fake") == ["--runner", "fake"]


def test_an_empty_runner_input_adds_nothing():
    assert _split_by_the_action("") == []


def test_a_runner_input_is_never_expanded_as_a_path_pattern(tmp_path):
    # An unquoted expansion would glob: with a file named `fake` in the
    # working directory, `runner: *` would select a real runner instead of
    # reaching the CLI as the unknown name it is.
    (Path.cwd() / "fake").write_text("", encoding="utf-8")
    assert _split_by_the_action("*") == ["--runner", "*"]
