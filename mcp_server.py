"""
AIP-aware MCP Server (stdio transport, JSON-RPC 2.0)

Architecture — MCP server as a client of the web API:

  ┌─────────────────────────────────────────────────────────┐
  │                     AI Agent (client)                   │
  └──────────────────────────┬──────────────────────────────┘
                             │  JSON-RPC over stdio
  ┌──────────────────────────▼──────────────────────────────┐
  │          Layer 2: AIP Enforcement (this server)         │
  │   • Validates AAT signature & expiry on every call      │
  │   • Checks required capabilities                        │
  │   • Enforces role-based data isolation                  │
  │   • Writes immutable audit log                          │
  └──────────────────────────┬──────────────────────────────┘
                             │  HTTP GET + Authorization: Bearer <aat>
  ┌──────────────────────────▼──────────────────────────────┐
  │   webapp.py  /api/emails/mine  /api/emails/all          │
  │   (web server — single authoritative data backend)      │
  └──────────────────────────┬──────────────────────────────┘
                             │
  ┌──────────────────────────▼──────────────────────────────┐
  │          data.py  (email store)                         │
  └─────────────────────────────────────────────────────────┘

The MCP server no longer imports data.py directly.  Instead it forwards the
agent's AAT to the web server's JSON API as a Bearer token.  The web server
runs its own AIP Layer 2 enforcement (validate_aat, check_capability,
check_role) before returning data — so enforcement happens twice:

  1. Here (fast, local — before the HTTP round-trip is even made)
  2. In webapp.py (authoritative — the web server trusts nothing)

This mirrors how a real microservice architecture works: the MCP server is
just one client of the backend; the backend enforces its own policy
independently.

Configuration
-------------
Set WEBAPP_BASE_URL to wherever webapp.py is running (default: localhost:8000).

    export WEBAPP_BASE_URL=http://localhost:8000
    python3 mcp_server.py

MCP protocol reference: https://modelcontextprotocol.io/specification
AIP spec: https://github.com/openagentidentityprotocol/agentidentityprotocol
"""

from __future__ import annotations

import json
import os
import sys
import datetime
import urllib.error
import urllib.request
from typing import Any, Optional

from auth import validate_aat, check_capability, AuthError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WEBAPP_BASE_URL = os.environ.get("WEBAPP_BASE_URL", "http://localhost:8000").rstrip("/")

# ---------------------------------------------------------------------------
# Audit log (immutable append-only, mirrors AIP spec requirement)
# ---------------------------------------------------------------------------

AUDIT_LOG_PATH = "audit.jsonl"


def _audit(event: str, agent_id: str, tool: str, outcome: str, detail: str = "") -> None:
    record = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "event": event,
        "agent_id": agent_id,
        "tool": tool,
        "outcome": outcome,
        "detail": detail,
        "transport": "mcp",
    }
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# HTTP helper — call the webapp JSON API with the agent's AAT as Bearer token
# ---------------------------------------------------------------------------


def _api_get(path: str, aat: str) -> dict:
    """
    Call a webapp JSON API endpoint, forwarding the AAT as a Bearer token.

    Raises:
        AuthError  — if the web server returns 401 or 403
        RuntimeError — for network errors or unexpected status codes
    """
    url = f"{WEBAPP_BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {aat}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = {}
        try:
            body = json.loads(exc.read())
        except Exception:
            pass
        if exc.code in (401, 403):
            raise AuthError(body.get("error", f"HTTP {exc.code}")) from exc
        raise RuntimeError(f"webapp returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"cannot reach webapp at {WEBAPP_BASE_URL} — is it running? ({exc.reason})"
        ) from exc


# ---------------------------------------------------------------------------
# Tool definitions (advertised to MCP clients via tools/list)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "list_my_emails",
        "description": (
            "List emails belonging to the authenticated user. "
            "Requires capability: read:own_emails. "
            f"Data is fetched from {WEBAPP_BASE_URL}/api/emails/mine."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "aat": {
                    "type": "string",
                    "description": "Agent Authentication Token (AAT) issued by the AIP identity layer.",
                }
            },
            "required": ["aat"],
        },
    },
    {
        "name": "list_all_emails",
        "description": (
            "List all emails in the system (admin only). "
            "Requires capability: read:all_emails and role admin. "
            f"Data is fetched from {WEBAPP_BASE_URL}/api/emails/all."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "aat": {
                    "type": "string",
                    "description": "Agent Authentication Token (AAT) with admin role.",
                }
            },
            "required": ["aat"],
        },
    },
]

# ---------------------------------------------------------------------------
# AIP Layer 2 enforcement + web API dispatch
# ---------------------------------------------------------------------------


def _enforce_and_run(tool_name: str, args: dict) -> Any:
    """
    AIP Layer 2 enforcement gate, then delegate to the webapp JSON API.

    Steps:
      1. Validate the AAT locally (signature + expiry + revocation)
      2. Check required capabilities locally (fast-fail before HTTP)
      3. Forward the AAT to the webapp API as a Bearer token
         — the webapp re-enforces policy independently
      4. Audit the decision
      5. Return the result
    """
    aat = args.get("aat", "")
    agent_id = "<unknown>"

    try:
        # Step 1 — local AAT validation
        claims = validate_aat(aat)
        agent_id = claims.get("agent_id", "<unknown>")
        user_id = claims.get("sub")

        if tool_name == "list_my_emails":
            # Step 2 — local capability check (avoids unnecessary HTTP round-trip)
            check_capability(claims, "read:own_emails")

            # Step 3 — delegate to webapp API
            result = _api_get("/api/emails/mine", aat)

            # Step 4 — audit allow
            _audit("tool_call", agent_id, tool_name, "allow", f"user={user_id}")

            # Step 5 — return
            return result

        elif tool_name == "list_all_emails":
            # Step 2 — local capability + role check
            check_capability(claims, "read:all_emails")
            role = claims.get("role")
            if role != "admin":
                raise AuthError(f"role '{role}' is not permitted to list all emails")

            # Step 3 — delegate to webapp API
            result = _api_get("/api/emails/all", aat)

            # Step 4 — audit allow
            _audit("tool_call", agent_id, tool_name, "allow", f"role={role}")

            # Step 5 — return
            return result

        else:
            raise ValueError(f"unknown tool '{tool_name}'")

    except (AuthError, ValueError) as exc:
        _audit("tool_call", agent_id, tool_name, "deny", str(exc))
        raise


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
                "protocolVersion": "1984-11-11",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "aip-email-server",
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
            result = _enforce_and_run(tool_name, tool_args)
            return _ok(
                rid,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(result, indent=2)}
                    ]
                },
            )
        except AuthError as exc:
            return _err(rid, -32001, f"AIP policy denied: {exc}")
        except RuntimeError as exc:
            return _err(rid, -32603, f"backend error: {exc}")
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
