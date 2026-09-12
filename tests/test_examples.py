"""The shipped examples must always parse and be well formed.

They are no longer run through FakeRunner: their assertions describe real model
behaviour now, so the zero-cost check is that discovery and schema validation
work on real files. The full run path is covered by the cassette tier.
"""

from pathlib import Path

from skill_lens.cases.loader import load_cases_for_skill
from skill_lens.config import Config, load_config
from skill_lens.skills.loader import load_skills

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_every_example_skill_is_discovered():
    names = [skill.name for skill in load_skills(EXAMPLES)]
    assert names == ["csv-report", "greeting", "order-support"]


def test_every_example_skill_has_at_least_one_case():
    # This call also exercises the loader's cross-reference validation (an
    # undeclared trajectory tool or a duplicate tool name raises CaseParseError
    # -- see tests/test_case_loader.py), so a regression here surfaces as this
    # test erroring rather than needing its own dedicated example-only check.
    for skill in load_skills(EXAMPLES):
        assert load_cases_for_skill(skill), f"{skill.name} has no eval cases"


def test_greeting_stays_at_the_version_that_makes_the_comparative_example_work():
    # `--baseline previous` resolves the skill's *earlier* version from git
    # history, so the shipped example only demonstrates a comparison because
    # 1.0.0 is on main and the working copy declares something later. Do not
    # revert this bump, and do not reuse 1.1.0 for an unrelated edit -- bump
    # again instead, so every version in history stays distinct.
    greeting = next(s for s in load_skills(EXAMPLES) if s.name == "greeting")
    assert greeting.version == "1.1.0"


def test_the_example_config_parses_and_sets_what_it_claims():
    config = load_config(path=EXAMPLES / "skill-lens.toml")
    assert config.default_runner == "pydantic-ai"
    assert config.judge == "pydantic-ai"
    assert config.concurrency == 4
    assert config.min_pass_rate == 1.0
    assert config.per_skill_min == {"order-support": 1.0}
    assert config.allow_scripts is True
    assert config.script_sandbox == "auto"


def test_the_example_config_mentions_every_key():
    # Live or commented out, every key skill-lens knows must appear, so the
    # file stays the one place a reader can see the whole surface.
    text = (EXAMPLES / "skill-lens.toml").read_text(encoding="utf-8")
    for field in Config.model_fields:
        assert field in text, f"{field} is missing from examples/skill-lens.toml"
