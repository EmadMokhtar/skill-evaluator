"""One-shot rename of skill-eval to skill-lens. Deleted once it has run.

Placeholders are substituted in first so that names which merely *start* with
the old name -- the GitHub repository skill-evaluator, the shipped skill
writing-skill-evals, and archive filenames -- come through untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Order matters: longest first, so skill-evaluator is claimed before skill-evals.
PROTECTED = [
    ("skill-evaluator", "\x00REPO\x00"),
    ("skill-evals", "\x00SKILLNAME\x00"),
    ("skill-eval-design", "\x00ARCHDESIGN\x00"),
    ("skill-eval-m0", "\x00ARCHM0\x00"),
    ("skill-eval-m1", "\x00ARCHM1\x00"),
    ("skill-eval-m2", "\x00ARCHM2\x00"),
    ("skill-eval-m3", "\x00ARCHM3\x00"),
    ("skill-eval-m4", "\x00ARCHM4\x00"),
    ("skill-eval-m5", "\x00ARCHM5\x00"),
]

RENAMES = [("skill-eval", "skill-lens"), ("skill_eval", "skill_lens")]

EXCLUDED_PREFIX = "docs/superpowers/"


def rewrite(text: str) -> str:
    for original, placeholder in PROTECTED:
        text = text.replace(original, placeholder)
    for old, new in RENAMES:
        text = text.replace(old, new)
    for original, placeholder in PROTECTED:
        text = text.replace(placeholder, original)
    return text


def main() -> None:
    listing = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    changed = 0
    for name in listing:
        if name.startswith(EXCLUDED_PREFIX):
            continue
        path = REPO_ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        rewritten = rewrite(text)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")
            changed += 1
    print(f"rewrote {changed} files")


if __name__ == "__main__":
    main()
