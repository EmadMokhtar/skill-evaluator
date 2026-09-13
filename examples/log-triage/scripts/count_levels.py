"""Count log lines per level. Standard library only.

Usage: count_levels.py <logfile>

Each line is `<timestamp> <LEVEL> <message>`. Prints `LEVEL: n`, one per
level, sorted by level name, so the output is stable for an eval to assert on.
"""

import sys
from collections import Counter
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: count_levels.py <logfile>", file=sys.stderr)
        return 2
    counts: Counter[str] = Counter()
    for line in Path(argv[1]).read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 2:
            counts[parts[1]] += 1
    for level in sorted(counts):
        print(f"{level}: {counts[level]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
