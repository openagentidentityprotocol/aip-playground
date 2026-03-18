"""
Tests for the MCP servers.

test_mcp_server_*  — AIP-enforced stdio server (mcp_server.py)
test_plain_*       — Plain stdio server with no enforcement (mcp_server_plain.py)
                     This is the server that aip-go wraps in production.

aip-go integration note
-----------------------
mcp_server_plain.py implements a standard MCP stdio server with zero
enforcement. The aip-go proxy (github.com/openagentidentityprotocol/aip-go)
sits in front of it and adds:
  - Tool allowlists (only declared tools can be called)
  - Parameter validation via regex patterns
  - Human-in-the-loop approval for sensitive operations
  - DLP response scanning
  - Immutable JSONL audit log

The plain server tests below confirm it exposes the right protocol so
aip-go can wrap it without modification.
"""

from __future__ import annotations

import json
import subprocess
import sys
import os
from pathlib import Path

import pytest
from unittest.mock import patch

# Project root so we can import modules directly
PROJECT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT))

from auth import issue_aat, AuthError
from data import authenticate_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StdioMCPClient:
    """Minimal MCP client over subprocess stdio — mirrors main.py's MCPClient."""

    def __init__(self, script: str, env: dict = None):
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        self._proc = subprocess.Popen(
            [sys.executable, str(PROJECT / script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=full_env,
            cwd=str(PROJECT),
        )
        self._counter = 0

    def send(self, method: str, params: dict = None, notify: bool = False) -> dict | None:
        self._counter += 1
        req = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            req["id"] = self._counter
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        if notify:
            return None
        raw = self._proc.stdout.readline()
        return json.loads(raw)

    def initialize(self):
        resp = self.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest-client", "version": "0.1"},
        })
        assert "result" in resp
        self.send("notifications/initialized", notify=True)

    def call_tool(self, name: str, args: dict) -> dict:
        return self.send("tools/call", {"name": name, "arguments": args})

    def list_tools(self) -> list:
        resp = self.send("tools/list")
        return resp["result"]["tools"]

    def close(self):
        self._proc.stdin.close()
        self._proc.wait(timeout=5)


def _result_json(resp: dict) -> dict:
    """Extract the JSON payload from a successful tool/call response."""
    assert "result" in resp, f"Expected result, got: {resp}"
    return json.loads(resp["result"]["content"][0]["text"])


# ---------------------------------------------------------------------------
# AIP-enforced MCP server (mcp_server.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def mcp(tmp_path):
    """Spin up mcp_server.py with audit log in tmp dir."""
    client = StdioMCPClient("mcp_server.py",
                            env={"AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl")})
    client.initialize()
    yield client
    client.close()


class TestMcpServerProtocol:
    def test_tools_list_contains_expected_tools(self, mcp):
        tools = mcp.list_tools()
        names = {t["name"] for t in tools}
        assert names == {"list_my_emails", "list_all_emails"}

    def test_tools_require_aat_parameter(self, mcp):
        tools = {t["name"]: t for t in mcp.list_tools()}
        for name in ("list_my_emails", "list_all_emails"):
            required = tools[name]["inputSchema"]["required"]
            assert "aat" in required

    def test_unknown_method_returns_error(self, mcp):
        resp = mcp.send("no/such/method")
        assert "error" in resp
        assert resp["error"]["code"] == -32601


class TestMcpServerAipEnforcement:
    """
    AIP Layer 2 enforcement tests — mirrors aip-go's policy/enforcement logic.
    These match the error codes used by aip-go: -32001 for policy denial.
    """

    def test_user_can_list_own_emails(self, mcp):
        aat = issue_aat("test-agent", "alice", "user", ["read:own_emails"])
        resp = mcp.call_tool("list_my_emails", {"aat": aat})
        data = _result_json(resp)
        assert data["count"] == 2
        for email in data["emails"]:
            assert email["to"] == "alice@example.com"

    def test_admin_can_list_all_emails(self, mcp):
        aat = issue_aat("admin-agent", "admin", "admin",
                        ["read:own_emails", "read:all_emails"])
        resp = mcp.call_tool("list_all_emails", {"aat": aat})
        data = _result_json(resp)
        assert data["count"] == 5

    def test_user_denied_list_all_emails(self, mcp):
        """User without read:all_emails capability is denied — AIP Layer 2."""
        aat = issue_aat("rogue-agent", "alice", "user", ["read:own_emails"])
        resp = mcp.call_tool("list_all_emails", {"aat": aat})
        assert "error" in resp
        assert resp["error"]["code"] == -32001
        assert "lacks capability" in resp["error"]["message"]

    def test_missing_aat_rejected(self, mcp):
        resp = mcp.call_tool("list_my_emails", {"aat": ""})
        assert "error" in resp

    def test_tampered_token_rejected(self, mcp):
        """Cryptographic validation catches token tampering — mirrors aip-go -32008/-32009."""
        fake = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkFBVCJ9"
                ".eyJzdWIiOiJhbGljZSIsInJvbGUiOiJhZG1pbiJ9"
                ".invalidsignature")
        resp = mcp.call_tool("list_all_emails", {"aat": fake})
        assert "error" in resp
        assert resp["error"]["code"] in (-32001, -32603)

    def test_data_isolation_alice_only_sees_own_emails(self, mcp):
        aat = issue_aat("test-agent", "alice", "user", ["read:own_emails"])
        resp = mcp.call_tool("list_my_emails", {"aat": aat})
        data = _result_json(resp)
        recipients = {e["to"] for e in data["emails"]}
        assert recipients == {"alice@example.com"}

    def test_data_isolation_bob_only_sees_own_emails(self, mcp):
        aat = issue_aat("bob-agent", "bob", "user", ["read:own_emails"])
        resp = mcp.call_tool("list_my_emails", {"aat": aat})
        data = _result_json(resp)
        recipients = {e["to"] for e in data["emails"]}
        assert recipients == {"bob@example.com"}

    def test_wrong_role_denied_even_with_capability(self, mcp):
        """Capability alone is not enough — role must also match."""
        aat = issue_aat("escalated-agent", "alice", "user",
                        ["read:own_emails", "read:all_emails"])
        resp = mcp.call_tool("list_all_emails", {"aat": aat})
        assert "error" in resp


# ---------------------------------------------------------------------------
# Plain MCP server (mcp_server_plain.py)
#
# This server has NO enforcement — it is the correct server to wrap with
# aip-go. These tests confirm the MCP protocol is implemented correctly
# so aip-go can proxy it without modification.
# ---------------------------------------------------------------------------

@pytest.fixture
def plain_mcp():
    client = StdioMCPClient("mcp_server_plain.py")
    client.initialize()
    yield client
    client.close()


class TestPlainMcpServer:
    """
    The plain server demonstrates the 'before AIP' baseline.
    All tool calls succeed as long as user_id is valid.
    aip-go enforces policy on top of this server externally.
    """

    def test_tools_list_contains_expected_tools(self, plain_mcp):
        tools = plain_mcp.list_tools()
        names = {t["name"] for t in tools}
        assert names == {"list_my_emails", "list_all_emails"}

    def test_tools_accept_user_id_not_aat(self, plain_mcp):
        """Plain server uses user_id directly — no AAT needed."""
        tools = {t["name"]: t for t in plain_mcp.list_tools()}
        props = tools["list_my_emails"]["inputSchema"]["properties"]
        assert "user_id" in props
        assert "aat" not in props

    def test_any_user_can_list_own_emails(self, plain_mcp):
        resp = plain_mcp.call_tool("list_my_emails", {"user_id": "alice"})
        data = _result_json(resp)
        assert data["count"] == 2

    def test_any_user_can_list_all_emails(self, plain_mcp):
        """
        Without enforcement, any user_id can call list_all_emails.
        This is the vulnerability that aip-go's allowlist/policy addresses.
        """
        resp = plain_mcp.call_tool("list_all_emails", {})
        data = _result_json(resp)
        assert data["count"] == 5

    def test_unknown_user_returns_error(self, plain_mcp):
        resp = plain_mcp.call_tool("list_my_emails", {"user_id": "nobody"})
        assert "error" in resp

    def test_data_isolation_by_user_id(self, plain_mcp):
        alice = _result_json(plain_mcp.call_tool("list_my_emails", {"user_id": "alice"}))
        bob = _result_json(plain_mcp.call_tool("list_my_emails", {"user_id": "bob"}))
        alice_ids = {e["id"] for e in alice["emails"]}
        bob_ids = {e["id"] for e in bob["emails"]}
        assert alice_ids.isdisjoint(bob_ids)
