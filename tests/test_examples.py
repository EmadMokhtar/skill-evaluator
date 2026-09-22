"""The shipped examples must always parse and be well formed.

They are no longer run through FakeRunner: their assertions describe real model
behaviour now, so the zero-cost check is that discovery and schema validation
work on real files. The full run path is covered by the cassette tier.
"""

import subprocess
import sys
from pathlib import Path

from skill_lens.cases.loader import load_cases_for_skill
from skill_lens.config import Config, load_config
from skill_lens.skills.loader import load_skills

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_every_example_skill_is_discovered():
    names = [skill.name for skill in load_skills(EXAMPLES)]
    assert names == ["csv-report", "greeting", "log-triage", "order-support"]


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


def test_log_triage_bundles_a_script_and_a_reference():
    skill = next(s for s in load_skills(EXAMPLES) if s.name == "log-triage")
    assert skill.bundle_root == (EXAMPLES / "log-triage").resolve()
    from skill_lens.bundle import SkillBundle

    assert SkillBundle(skill.bundle_root).listing() == [
        "references/report-format.md",
        "scripts/count_levels.py",
    ]


def test_the_log_triage_script_counts_levels_with_the_standard_library_only(tmp_path):
    # The eval's expected counts are computed by this script; if it drifts,
    # the recorded cassette and the assertions drift with it.
    (tmp_path / "app.log").write_text(
        "2026-01-01 INFO start\n2026-01-01 ERROR db down\n2026-01-01 WARN slow\n"
        "2026-01-01 ERROR db down again\n2026-01-01 INFO done\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(EXAMPLES / "log-triage" / "scripts" / "count_levels.py"), "app.log"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout == "ERROR: 2\nINFO: 2\nWARN: 1\n"


def test_order_support_pulls_its_tools_from_the_shared_library():
    # The five tool-bearing cases share two contracts and set their own
    # scenario through `returns:` -- the case the library feature exists for.
    skill = next(s for s in load_skills(EXAMPLES / "order-support"))
    cases = {case.name: case for case in load_cases_for_skill(skill)}
    refuses = cases["refuses a refund outside the return window"]
    refunds = cases["refunds an order inside the return window"]
    assert [t.name for t in refuses.tools] == ["lookup_order", "issue_refund"]
    assert refuses.tools[0].description == refunds.tools[0].description
    assert '"days_since_delivery": 45' in refuses.tools[0].returns
    assert '"days_since_delivery": 3' in refunds.tools[0].returns
    assert (EXAMPLES / "shared-tools" / "order-api.yaml").is_file()


def test_order_support_answers_the_two_order_case_by_argument():
    # One case, two orders: the mock's reply depends on which id the skill
    # asked for, which a single canned `returns:` could never express.
    skill = next(s for s in load_skills(EXAMPLES / "order-support"))
    cases = {case.name: case for case in load_cases_for_skill(skill)}
    lookup = cases["refunds only the order still inside the window"].tools[0]
    assert [entry.when for entry in lookup.returns] == [
        {"order_id": "1234"},
        {"order_id": "5678"},
    ]
    assert '"days_since_delivery": 45' in lookup.returns[0].value
    assert '"days_since_delivery": 3' in lookup.returns[1].value
