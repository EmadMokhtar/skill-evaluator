"""Shared strict-bool YAML loading.

PyYAML's `SafeLoader` implements YAML 1.1, whose implicit resolvers treat bare
`yes`/`no`/`on`/`off` (in any case) as booleans, in addition to `true`/`false`.
That is surprising for humans authoring YAML by hand: a SKILL.md frontmatter
field like ``name: on`` or an eval assertion like ``value: yes`` is meant as
the string `"on"`/`"yes"`, but PyYAML silently turns it into the Python
boolean `True`. Downstream code then either coerces it back to the string
`"True"` (wrong value) or fails a strict-typed validation (confusing error).

`StrictBoolLoader` narrows the implicit bool resolver to only the
unambiguous `true`/`True`/`TRUE`/`false`/`False`/`FALSE` forms, so
`yes`/`no`/`on`/`off` parse as plain strings instead. Everything else about
`SafeLoader` (including `true`/`false` still resolving to real booleans) is
unchanged.
"""

from __future__ import annotations

import re
from typing import Any

import yaml


class StrictBoolLoader(yaml.SafeLoader):
    """SafeLoader that only treats true/false as booleans.

    Plain YAML 1.1 (what PyYAML implements) also resolves bare yes/no/on/off
    to booleans, which silently corrupts values like ``value: yes`` or
    ``name: on`` into ``True``. Authors write those as plain strings, so
    narrow the implicit bool resolver to the unambiguous true/false forms.
    """


StrictBoolLoader.yaml_implicit_resolvers = {
    first_char: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
StrictBoolLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def safe_load(text: str) -> Any:
    """Load YAML text using `StrictBoolLoader`."""
    return yaml.load(text, Loader=StrictBoolLoader)
