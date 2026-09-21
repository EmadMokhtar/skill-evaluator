"""Serve a case's mock tools to an agent product over MCP on stdio.

The framework adapters register a case's `tools:` as callables inside their
own agent loop. A product runner has no loop to reach into; what a product
can take is an MCP server named in a config file. This module is that
server. `ProductRunner` writes a spec file listing the case's tools and a
config file naming this module, hands the config to the product, and the
product starts `python -m skill_lens.mcp_bridge <spec>` itself and talks
JSON-RPC 2.0 to it over stdin/stdout, one message per line.

The server does exactly what a mock tool does everywhere else: `tools/list`
returns the declared names, descriptions and schemas verbatim, and
`tools/call` returns `returns` verbatim whatever the arguments were -- a
model that passed the wrong arguments is an eval signal, not a reason to
raise. Every `tools/list` and `tools/call` is appended to the record file
the spec names, so the runner can tell a product that never connected from
a model that never called.

Imports nothing from the rest of the project on purpose: the product starts
this module in its own child process, and the less it needs the fewer ways
that start can fail. `--check` exercises the request handlers in-process,
which is how preflight proves this module starts under `sys.executable`
before any case spends quota.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO, Any

# The MCP protocol revisions this server speaks. It only serves tools, whose
# wire shape is identical across all three, so the client's revision is
# echoed back when it is one of these and the newest is offered otherwise.
PROTOCOL_VERSIONS: tuple[str, ...] = ("2024-11-05", "2025-03-26", "2025-06-18")
SERVER_NAME = "skill-lens"

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def load_spec(path: Path) -> dict[str, Any]:
    """Read the spec `ProductRunner` wrote: `tools`, `record`, `version`."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not isinstance(spec.get("tools"), list):
        raise ValueError(f"{path}: not a bridge spec (expected an object with a tools list)")
    return spec


class Bridge:
    """The request handlers, apart from the transport so `--check` and the tests
    can drive them without a process."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._tools: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in _named_tools(spec)}
        record = spec.get("record")
        self._record = Path(record) if isinstance(record, str) and record else None
        self._version = str(spec.get("version", ""))

    def _note(self, event: dict[str, Any]) -> None:
        """Append one line to the record file. Best effort: a record that
        cannot be written must not stop the tool from answering."""
        if self._record is None:
            return
        try:
            with self._record.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
        except OSError:
            pass

    def handle(self, message: Any) -> dict[str, Any] | None:
        """One JSON-RPC message in, at most one response out (None for a
        notification or for a request that needs no reply)."""
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "expected a JSON-RPC request object")
        method = message.get("method")
        request_id = message.get("id")
        if "id" not in message:
            # A notification (`notifications/initialized`, `notifications/
            # cancelled`): nothing to answer.
            return None
        if not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, "missing method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1]
            return _result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": self._version},
                },
            )
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            self._note({"event": "list"})
            return _result(
                request_id,
                {
                    "tools": [
                        {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "inputSchema": tool["input_schema"],
                        }
                        for tool in self._tools.values()
                    ]
                },
            )
        if method == "tools/call":
            name = params.get("name")
            tool = self._tools.get(name) if isinstance(name, str) else None
            if tool is None:
                return _error(request_id, INVALID_PARAMS, f"unknown tool: {name!r}")
            arguments = params.get("arguments")
            self._note(
                {
                    "event": "call",
                    "name": name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                }
            )
            return _result(
                request_id,
                {"content": [{"type": "text", "text": str(tool.get("returns", ""))}]},
            )
        return _error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _named_tools(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The spec's tool entries that are objects with a string name; anything
    else is not a tool and is skipped, by the handlers and by `check` alike."""
    return [
        tool
        for tool in spec["tools"]
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(bridge: Bridge, stdin: IO[bytes], stdout: IO[bytes]) -> None:
    """Answer requests from `stdin` on `stdout` until `stdin` closes.

    One JSON message per line each way, and nothing but JSON on stdout: the
    product reads stdout as the protocol stream, so a stray print would
    break the connection. A line that is not JSON gets a parse error with a
    null id, as JSON-RPC prescribes; a JSON array is answered per element,
    for a client on a revision that still batches.
    """
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            responses: list[dict[str, Any] | None] = [
                _error(None, PARSE_ERROR, "the request line is not JSON")
            ]
        else:
            messages = parsed if isinstance(parsed, list) else [parsed]
            responses = [bridge.handle(message) for message in messages]
        for response in responses:
            if response is None:
                continue
            stdout.write(json.dumps(response).encode("utf-8") + b"\n")
            stdout.flush()


def check(spec: dict[str, Any]) -> None:
    """Prove the handlers answer: `initialize`, then `tools/list` naming every
    tool in the spec. Raises `RuntimeError` naming what was wrong."""
    bridge = Bridge({**spec, "record": ""})
    initialised = bridge.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSIONS[-1]},
        }
    )
    if initialised is None or "result" not in initialised:
        raise RuntimeError(f"initialize failed: {initialised}")
    listed = bridge.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in (listed or {}).get("result", {}).get("tools", [])]
    expected = [tool["name"] for tool in _named_tools(spec)]
    if names != expected:
        raise RuntimeError(f"tools/list returned {names}, expected {expected}")


def main(argv: list[str]) -> int:
    """`python -m skill_lens.mcp_bridge <spec>` serves; `--check <spec>` proves
    the module starts and lists the spec's tools, then exits."""
    checking = argv[:1] == ["--check"]
    paths = argv[1:] if checking else argv
    if len(paths) != 1:
        print("usage: python -m skill_lens.mcp_bridge [--check] <spec.json>", file=sys.stderr)
        return 2
    try:
        spec = load_spec(Path(paths[0]))
    except (OSError, ValueError) as exc:
        print(f"skill-lens mcp bridge: cannot read the spec: {exc}", file=sys.stderr)
        return 2
    if checking:
        try:
            check(spec)
        except RuntimeError as exc:
            print(f"skill-lens mcp bridge: {exc}", file=sys.stderr)
            return 1
        print("ok")
        return 0
    serve(Bridge(spec), sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
