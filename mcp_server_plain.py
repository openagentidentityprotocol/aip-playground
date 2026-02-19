"""
Plain MCP Server — no AIP enforcement (stdio transport, JSON-RPC 2.0)

This is the same email service as mcp_server.py but with ALL AIP Layer 2
enforcement removed.  It is the "before" picture that illustrates the problem
AIP solves:

  • No token validation  — any caller gets whatever they ask for
  • No capability checks — no concept of what an agent is allowed to do
  • No role enforcement  — any caller can request all emails
  • No audit trail       — nothing is logged

This is the topology described in the AIP spec as "god mode": an MCP server
connected directly to a client with full, undifferentiated access.

                          ┌──────────────┐
                          │  MCP Client  │
                          └──────┬───────┘
                                 │  JSON-RPC over stdio — no enforcement
                          ┌──────▼───────┐
                          │  this server │  ← tools are wide open
                          └──────┬───────┘
                                 │
                          ┌──────▼───────┐
                          │  data store  │
                          └──────────────┘

In production you would put the AIP proxy (or the enforcement embedded in
mcp_server.py) in front of a server like this one.  See MCP_CLIENT_GUIDE.md,
section "Where the AIP proxy enforcement layer fits".

MCP protocol reference: https://modelcontextprotocol.io/specification
AIP spec: https://github.com/openagentidentityprotocol/agentidentityprotocol

Run:
    python3 mcp_server_plain.py
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

from data import get_emails_for_user, get_all_emails, USERS

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------
# Note: no `aat` parameter — callers pass user_id directly with zero verification.

TOOLS = [
    {
        "name": "list_my_emails",
        "description": (
            "List emails for a given user. "
            "Pass the user_id of the account whose emails you want to read. "
            "WARNING: no authentication or authorisation is enforced."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "ID of the user whose emails to fetch (alice, bob, admin).",
                }
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "list_all_emails",
        "description": (
            "List every email in the system. "
            "WARNING: no authentication or authorisation is enforced — "
            "any caller receives all emails."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

# ---------------------------------------------------------------------------
# Tool handlers — direct data access, no policy gate
# ---------------------------------------------------------------------------


def _run(tool_name: str, args: dict) -> Any:
    """Execute the requested tool with no enforcement whatsoever."""
    if tool_name == "list_my_emails":
        user_id = args.get("user_id", "")
        user = USERS.get(user_id)
        if not user:
            raise ValueError(f"unknown user_id '{user_id}'")
        emails = get_emails_for_user(user["email"])
        return {"emails": emails, "count": len(emails)}

    if tool_name == "list_all_emails":
        emails = get_all_emails()
        return {"emails": emails, "count": len(emails)}

    raise ValueError(f"unknown tool '{tool_name}'")


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 MCP dispatcher
# ---------------------------------------------------------------------------


def _ok(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def handle_request(req: dict) -> Optional[dict]:
    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    if method == "initialize":
        return _ok(
            rid,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "plain-email-server",
                    "version": "0.1.0",
                },
            },
        )

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        try:
            result = _run(tool_name, tool_args)
            return _ok(
                rid,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(result, indent=2)}
                    ]
                },
            )
        except Exception as exc:
            return _err(rid, -32603, f"internal error: {exc}")

    return _err(rid, -32601, f"method not found: {method}")


# ---------------------------------------------------------------------------
# stdio transport loop
# ---------------------------------------------------------------------------


def run_server() -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = _err(None, -32700, f"parse error: {exc}")
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        resp = handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_server()
