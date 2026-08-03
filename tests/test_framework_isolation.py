"""No agent-framework type may appear outside the two adapter modules.

The rule is about *importing the framework*, not about the string
`pydantic_ai` appearing in a file: `cli.py` legitimately writes
`from skill_eval.runners.pydantic_ai import ...`, which is an import of our own
module. So this matches top-level `import pydantic_ai` / `from pydantic_ai...`
forms only.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "skill_eval"

ALLOWED = {
    Path("runners/pydantic_ai.py"),
    Path("judges/pydantic_ai.py"),
}

FRAMEWORK_IMPORT = re.compile(r"^\s*(?:from|import)\s+pydantic_ai\b", re.MULTILINE)


def test_only_the_two_adapters_import_the_agent_framework():
    offenders = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC) not in ALLOWED
        and FRAMEWORK_IMPORT.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == []


def test_both_allowed_adapters_actually_exist():
    # Guards against the allowlist quietly outliving the modules it names,
    # which would turn this test into a permanent vacuous pass.
    for relative in ALLOWED:
        assert (SRC / relative).is_file()
