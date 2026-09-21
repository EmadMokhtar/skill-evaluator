"""The stdio MCP server a product starts to reach a case's mock tools."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from skill_lens.mcp_bridge import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSIONS,
    SERVER_NAME,
    Bridge,
    check,
    load_spec,
    main,
    serve,
)

SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
    "additionalProperties": False,
}


def _spec(record: Path | None = None) -> dict:
    return {
        "version": "1.2.3",
        "record": str(record) if record else "",
        "tools": [
            {
                "name": "lookup_order",
                "description": "Look up an order.",
                "input_schema": SCHEMA,
                "returns": '{"status": "shipped"}',
            },
            {"name": "issue_refund", "description": "", "input_schema": SCHEMA, "returns": "ok"},
        ],
    }


def _request(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _events(record: Path) -> list[dict]:
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


# --- the handlers ---


def test_initialize_echoes_a_known_protocol_version_and_offers_tools():
    bridge = Bridge(_spec())
    for version in PROTOCOL_VERSIONS:
        reply = bridge.handle(_request("initialize", {"protocolVersion": version}))
        assert reply == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.2.3"},
            },
        }


def test_initialize_offers_the_newest_version_to_an_unknown_client_revision():
    reply = Bridge(_spec()).handle(_request("initialize", {"protocolVersion": "1999-01-01"}))
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSIONS[-1]
    reply = Bridge(_spec()).handle(_request("initialize"))
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSIONS[-1]


def test_tools_list_is_the_spec_verbatim_in_order():
    reply = Bridge(_spec()).handle(_request("tools/list"))
    assert reply["result"] == {
        "tools": [
            {"name": "lookup_order", "description": "Look up an order.", "inputSchema": SCHEMA},
            {"name": "issue_refund", "description": "", "inputSchema": SCHEMA},
        ]
    }


def test_tools_call_returns_the_canned_text_whatever_the_arguments():
    bridge = Bridge(_spec())
    for arguments in ({"order_id": "A-17"}, {"wrong": 1}, {}, None, "not a dict"):
        reply = bridge.handle(
            _request("tools/call", {"name": "lookup_order", "arguments": arguments})
        )
        assert reply["result"] == {"content": [{"type": "text", "text": '{"status": "shipped"}'}]}


def test_tools_call_on_an_unknown_tool_is_invalid_params():
    reply = Bridge(_spec()).handle(_request("tools/call", {"name": "nope"}))
    assert reply["error"]["code"] == INVALID_PARAMS
    assert "nope" in reply["error"]["message"]
    reply = Bridge(_spec()).handle(_request("tools/call", {}))
    assert reply["error"]["code"] == INVALID_PARAMS


def test_ping_is_answered_and_a_notification_is_not():
    bridge = Bridge(_spec())
    assert bridge.handle(_request("ping")) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert bridge.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None


def test_an_unsupported_method_is_method_not_found():
    reply = Bridge(_spec()).handle(_request("resources/list"))
    assert reply["error"]["code"] == METHOD_NOT_FOUND
    assert reply["id"] == 1


def test_a_message_that_is_not_a_request_object_is_an_error_not_a_raise():
    reply = Bridge(_spec()).handle("hello")
    assert reply["error"]["code"] == -32600 and reply["id"] is None
    reply = Bridge(_spec()).handle({"jsonrpc": "2.0", "id": 7})
    assert reply["error"]["code"] == -32600 and reply["id"] == 7


def test_every_list_and_call_is_recorded_in_order(tmp_path):
    record = tmp_path / "calls.jsonl"
    bridge = Bridge(_spec(record))
    bridge.handle(_request("initialize"))  # not recorded
    bridge.handle(_request("tools/list"))
    bridge.handle(_request("tools/call", {"name": "issue_refund", "arguments": {"a": 1}}))
    bridge.handle(_request("tools/call", {"name": "nope"}))  # refused, not recorded
    bridge.handle(_request("tools/call", {"name": "lookup_order", "arguments": "raw"}))
    assert _events(record) == [
        {"event": "list"},
        {"event": "call", "name": "issue_refund", "arguments": {"a": 1}},
        {"event": "call", "name": "lookup_order", "arguments": {}},
    ]


def test_an_unwritable_record_never_stops_an_answer(tmp_path):
    bridge = Bridge(_spec(tmp_path / "missing-dir" / "calls.jsonl"))
    reply = bridge.handle(_request("tools/call", {"name": "issue_refund"}))
    assert reply["result"]["content"][0]["text"] == "ok"


# --- the transport ---


def _serve(lines: list[object]) -> list[dict]:
    stdin = io.BytesIO(
        b"".join(
            (line if isinstance(line, bytes) else json.dumps(line).encode()) + b"\n"
            for line in lines
        )
    )
    stdout = io.BytesIO()
    serve(Bridge(_spec()), stdin, stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_serve_answers_one_line_per_request_and_nothing_for_notifications():
    replies = _serve(
        [
            _request("initialize", {"protocolVersion": "2025-06-18"}, 1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            b"",
            _request("tools/list", None, 2),
        ]
    )
    assert [reply["id"] for reply in replies] == [1, 2]


def test_serve_answers_a_non_json_line_with_a_parse_error_and_keeps_going():
    replies = _serve([b"garbage", _request("ping", None, 3)])
    assert replies[0]["error"]["code"] == PARSE_ERROR and replies[0]["id"] is None
    assert replies[1] == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_serve_answers_a_batch_per_element():
    replies = _serve([[_request("ping", None, 1), _request("ping", None, 2)]])
    assert [reply["id"] for reply in replies] == [1, 2]


def test_serve_writes_only_json_to_stdout():
    stdout = io.BytesIO()
    serve(Bridge(_spec()), io.BytesIO(b"garbage\n{}\n"), stdout)
    for line in stdout.getvalue().splitlines():
        json.loads(line)


# --- the entry point ---


def test_check_passes_for_a_spec_and_ignores_a_malformed_entry():
    check(_spec())
    # An entry with no name is not a tool; it is skipped by the handlers and
    # by the check alike, so the two still agree.
    check({**_spec(), "tools": [{"name": "x", "input_schema": {}}, {"input_schema": {}}, 3]})
    assert Bridge({"tools": [{"input_schema": {}}, 3]}).handle(_request("tools/list"))[
        "result"
    ] == {"tools": []}


def test_load_spec_refuses_what_is_not_a_spec(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a bridge spec"):
        load_spec(path)
    path.write_text(json.dumps(_spec()), encoding="utf-8")
    assert load_spec(path)["tools"][0]["name"] == "lookup_order"


def test_main_check_exits_0_on_a_good_spec_and_2_on_a_bad_path(tmp_path, capsys):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(_spec()), encoding="utf-8")
    assert main(["--check", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert main(["--check", str(tmp_path / "missing.json")]) == 2
    assert "cannot read the spec" in capsys.readouterr().err
    assert main([]) == 2
    assert main(["--check"]) == 2


def test_the_module_serves_as_a_subprocess_under_this_interpreter(tmp_path):
    """The real thing: `python -m skill_lens.mcp_bridge <spec>` over pipes,
    the way a product starts it."""
    record = tmp_path / "calls.jsonl"
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(_spec(record)), encoding="utf-8")
    requests = [
        _request("initialize", {"protocolVersion": "2025-06-18"}, 1),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _request("tools/list", None, 2),
        _request("tools/call", {"name": "lookup_order", "arguments": {"order_id": "A-17"}}, 3),
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "skill_lens.mcp_bridge", str(path)],
        input="".join(json.dumps(request) + "\n" for request in requests).encode(),
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    replies = [json.loads(line) for line in completed.stdout.decode().splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, 3]
    assert replies[2]["result"]["content"] == [{"type": "text", "text": '{"status": "shipped"}'}]
    assert _events(record) == [
        {"event": "list"},
        {"event": "call", "name": "lookup_order", "arguments": {"order_id": "A-17"}},
    ]
    # Stdin closed, so the server exited on its own -- what lets a product
    # that never killed it leave nothing behind.


def test_the_module_imports_nothing_from_the_project():
    """The product starts this module in its own child process; the fewer
    imports, the fewer ways that start can fail."""
    source = Path(__import__("skill_lens.mcp_bridge", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )
    assert "from skill_lens" not in source and "import skill_lens" not in source
