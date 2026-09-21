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

An argv element `--mcp-config=<path>` or `--additional-mcp-config=@<path>` --
the two spellings the presets use -- makes the fake act as an MCP client the
way a product does: it reads the config, starts the one server it names,
sends `initialize`, `notifications/initialized` and `tools/list`, and calls
the tool FAKE_PRODUCT_MCP_CALL names (if set) with `{"order_id": "A-17"}`.
What it saw goes under `mcp` in the record. FAKE_PRODUCT_MCP=ignore reads
the config but never starts the server, like a product whose MCP support
was switched off.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MCP_CONFIG_PREFIXES = ("--mcp-config=", "--additional-mcp-config=@")


def _mcp_session(argv: list[str]) -> dict[str, Any] | None:
    config_path = None
    for element in argv:
        for prefix in MCP_CONFIG_PREFIXES:
            if element.startswith(prefix):
                config_path = element[len(prefix) :]
    if config_path is None:
        return None
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if os.environ.get("FAKE_PRODUCT_MCP") == "ignore":
        return {"config": config, "ignored": True}
    ((name, server),) = config["mcpServers"].items()
    process = subprocess.Popen(
        [server["command"], *server["args"]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None

    def send(message: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        process.stdin.flush()

    def ask(message: dict[str, Any]) -> dict[str, Any]:
        send(message)
        return json.loads(process.stdout.readline())

    initialised = ask(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fake-product", "version": "1.2.3"},
            },
        }
    )
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    listed = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    call = None
    tool = os.environ.get("FAKE_PRODUCT_MCP_CALL")
    if tool:
        call = ask(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"order_id": "A-17"}},
            }
        )
    process.stdin.close()
    process.wait(timeout=30)
    return {
        "config": config,
        "server": name,
        "initialize": initialised.get("result"),
        "tools": [tool["name"] for tool in listed["result"]["tools"]],
        "call": call.get("result") if call else None,
        "exit_code": process.returncode,
    }


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        if os.environ.get("FAKE_PRODUCT_VERSION_FAILS"):
            print("cannot start", file=sys.stderr)
            return 1
        print("fake 1.2.3")
        return 0
    prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
    # A product connects to its MCP servers whether or not anyone is watching.
    mcp = _mcp_session(argv)
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
                {
                    "cwd": str(Path.cwd()),
                    "argv": argv,
                    "prompt": prompt,
                    "skill_files": files,
                    "mcp": mcp,
                }
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
