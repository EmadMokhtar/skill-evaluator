"""Structural matching of an author's mapping against a call's arguments.

One implementation for the two places an eval file names arguments: a
`trajectory.call_args` entry (`contains:` / `equals:`) and a mock tool's
`returns:` lookup (`when:`). Both are matched against the argument dict the
runner recorded -- never a serialised string, so key order and whitespace
cannot matter -- and both must agree on what "the same value" means, or an
author would learn two rules for one idea. Imports nothing from the project,
so `models.py` and the evaluators can both use it.
"""

from __future__ import annotations

from typing import Any


def same_scalar(expected: Any, actual: Any) -> bool:
    """Equality without Python's bool-is-an-int rule.

    `True == 1` in Python, so an author who wrote `limit: 1` would otherwise
    pass on a call that sent `true`, and a YAML `true` would answer a model's
    `1`. A bool only ever equals a bool. Every other scalar compares as JSON
    would: `"1"` never equals `1`, and `1` equals `1.0`.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    return bool(expected == actual)


def structural_match(expected: Any, actual: Any, *, exact: bool) -> bool:
    """Structural match of `expected` against a call's recorded arguments.

    A mapping matches when every key it names is present with a matching
    value; under `exact` it must also name every key the call carried. A
    list matches element by element at the same length -- containment is
    not subsetting, so `[bug]` does not match `[bug, urgent]`. Anything else
    is a scalar and must be equal.
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        if exact and set(expected) != set(actual):
            return False
        return all(
            key in actual and structural_match(value, actual[key], exact=exact)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(
                structural_match(e, a, exact=exact) for e, a in zip(expected, actual, strict=True)
            )
        )
    if isinstance(actual, (dict, list)):
        return False
    return same_scalar(expected, actual)
