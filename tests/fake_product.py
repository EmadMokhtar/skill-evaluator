"""A stand-in for an agent product's CLI, driven by environment variables.

Started by the tests as `python fake_product.py -p <prompt> ...`. It records
what it saw to the JSON file named by FAKE_PRODUCT_RECORD -- its cwd, its
argv, the prompt after `-p`, and every file under the directory named by
FAKE_PRODUCT_SKILLS_DIR (relative to cwd) -- then behaves as FAKE_PRODUCT_MODE
says:

  ok       print the file named by FAKE_PRODUCT_TRACE to stdout, exit 0 (default)
  sleep    sleep 60 s; the runner's timeout must kill it
  exit3    print "boom" to stderr, exit 3
  garbage  print text that is no JSON at all, exit 0
  huge     print the trace, then FAKE_PRODUCT_BYTES bytes of "x", exit 0

`--version` as the first argument prints "fake 1.2.3" and exits 0, or exits 1
when FAKE_PRODUCT_VERSION_FAILS is set. Nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        if os.environ.get("FAKE_PRODUCT_VERSION_FAILS"):
            print("cannot start", file=sys.stderr)
            return 1
        print("fake 1.2.3")
        return 0
    prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
    record = os.environ.get("FAKE_PRODUCT_RECORD")
    if record:
        skills_dir = os.environ.get("FAKE_PRODUCT_SKILLS_DIR", "")
        root = Path.cwd() / skills_dir if skills_dir else None
        files = (
            sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
            if root is not None and root.is_dir()
            else []
        )
        Path(record).write_text(
            json.dumps(
                {"cwd": str(Path.cwd()), "argv": argv, "prompt": prompt, "skill_files": files}
            ),
            encoding="utf-8",
        )
    mode = os.environ.get("FAKE_PRODUCT_MODE", "ok")
    if mode == "sleep":
        time.sleep(60)
        return 0
    if mode == "exit3":
        print("boom", file=sys.stderr)
        return 3
    if mode == "garbage":
        print("this is not json")
        return 0
    trace = Path(os.environ["FAKE_PRODUCT_TRACE"]).read_text(encoding="utf-8")
    sys.stdout.write(trace)
    if mode == "huge":
        sys.stdout.write("\n" + "x" * int(os.environ.get("FAKE_PRODUCT_BYTES", "100000")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
