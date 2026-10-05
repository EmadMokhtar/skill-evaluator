"""The error an eval file raises when it cannot be read or is wrong.

Alone in its own module so the loader and the `evals.json` converter can both
raise it: the loader imports the converter, so the converter cannot import the
loader.
"""

from __future__ import annotations


class CaseParseError(Exception):
    """Raised when an eval file is missing or cannot be parsed."""
