"""
Tests for webapp.py — browser routes, JSON API, and MCP-over-HTTP dispatch.

Three auth paths tested:
  1. Browser session  — POST /login → session cookie → /inbox, /admin, /audit
  2. JSON API         — GET /api/emails/* with Authorization: Bearer <aat>
  3. MCP over HTTP    — _mcp_dispatch() unit tests + SSE endpoint handshake
"""

from __future__ import annotations

import json

import pytest

from auth import issue_aat
import webapp
from webapp import _mcp_dispatch, _mcp_tool_call


# ---------------------------------------------------------------------------
# Browser routes — human session (no AAT)
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"login" in resp.content.lower()

    def test_valid_login_redirects_to_inbox(self, client):
        resp = client.post("/login",
                           data={"username": "alice", "password": "alice123"},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/inbox"

    def test_invalid_password_redirects_with_error(self, client):
        resp = client.post("/login",
                           data={"username": "alice", "password": "wrong"},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert "error" in resp.headers["location"].lower()

    def test_unknown_user_redirects_with_error(self, client):
        resp = client.post("/login",
                           data={"username": "nobody", "password": "x"},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert "error" in resp.headers["location"].lower()

    def test_logout_clears_session(self, authed_client_alice):
        authed_client_alice.get("/logout", follow_redirects=False)
        resp = authed_client_alice.get("/inbox", follow_redirects=False)
        assert resp.status_code == 302


class TestInbox:
    def test_unauthenticated_redirects(self, client):
        resp = client.get("/inbox", follow_redirects=False)
        assert resp.status_code == 302

    def test_alice_sees_inbox(self, authed_client_alice):
        resp = authed_client_alice.get("/inbox")
        assert resp.status_code == 200
        assert b"Q1 Report" in resp.content

    def test_alice_does_not_see_bobs_emails(self, authed_client_alice):
        resp = authed_client_alice.get("/inbox")
        assert b"Project Kickoff" not in resp.content

    def test_admin_can_view_inbox(self, authed_client_admin):
        resp = authed_client_admin.get("/inbox")
        assert resp.status_code == 200


class TestAdminView:
    def test_unauthenticated_redirects(self, client):
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 302

    def test_user_role_denied(self, authed_client_alice):
        resp = authed_client_alice.get("/admin", follow_redirects=False)
        assert resp.status_code == 302
        assert "denied" in resp.headers["location"].lower()

    def test_admin_sees_all_emails(self, authed_client_admin):
        resp = authed_client_admin.get("/admin")
        assert resp.status_code == 200
        # All three users' emails should appear
        assert b"Q1 Report" in resp.content        # alice
        assert b"Project Kickoff" in resp.content  # bob
        assert b"Security Alert" in resp.content   # admin


class TestAuditView:
    def test_user_role_denied(self, authed_client_alice):
        resp = authed_client_alice.get("/audit", follow_redirects=False)
        assert resp.status_code == 302

    def test_admin_can_view_audit(self, authed_client_admin):
        resp = authed_client_admin.get("/audit")
        assert resp.status_code == 200


class TestRootRedirect:
    def test_root_redirects_to_login_when_unauthenticated(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_root_redirects_to_inbox_when_authenticated(self, authed_client_alice):
        resp = authed_client_alice.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/inbox" in resp.headers["location"]


# ---------------------------------------------------------------------------
# JSON API routes — agent Bearer token (AIP Layer 2)
# ---------------------------------------------------------------------------

class TestApiListMyEmails:
    def test_no_token_returns_401(self, client):
        resp = client.get("/api/emails/mine")
        assert resp.status_code == 401

    def test_valid_user_aat_returns_own_emails(self, client, alice_aat):
        resp = client.get("/api/emails/mine",
                          headers={"Authorization": f"Bearer {alice_aat}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        for email in data["emails"]:
            assert email["to"] == "alice@example.com"

    def test_admin_aat_returns_admin_emails(self, client, admin_aat):
        resp = client.get("/api/emails/mine",
                          headers={"Authorization": f"Bearer {admin_aat}"})
        assert resp.status_code == 200
        data = resp.json()
        for email in data["emails"]:
            assert email["to"] == "admin@example.com"

    def test_tampered_token_returns_401(self, client):
        resp = client.get("/api/emails/mine",
                          headers={"Authorization": "Bearer eyJ.bad.token"})
        assert resp.status_code == 401

    def test_wrong_capability_returns_403(self, client):
        # Token has no capability at all
        aat = issue_aat("agent", "alice", "user", [])
        resp = client.get("/api/emails/mine",
                          headers={"Authorization": f"Bearer {aat}"})
        assert resp.status_code == 403


class TestApiListAllEmails:
    def test_no_token_returns_401(self, client):
        resp = client.get("/api/emails/all")
        assert resp.status_code == 401

    def test_user_aat_returns_403(self, client, alice_aat):
        """User token lacks read:all_emails capability — AIP Layer 2 denies."""
        resp = client.get("/api/emails/all",
                          headers={"Authorization": f"Bearer {alice_aat}"})
        assert resp.status_code == 403

    def test_admin_aat_returns_all_emails(self, client, admin_aat):
        resp = client.get("/api/emails/all",
                          headers={"Authorization": f"Bearer {admin_aat}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 5

    def test_capability_without_role_returns_403(self, client):
        """Having read:all_emails capability but wrong role is still denied."""
        aat = issue_aat("sneaky-agent", "alice", "user",
                        ["read:own_emails", "read:all_emails"])
        resp = client.get("/api/emails/all",
                          headers={"Authorization": f"Bearer {aat}"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# MCP over HTTP/SSE — _mcp_dispatch unit tests
# ---------------------------------------------------------------------------

class TestMcpDispatch:
    def test_initialize_returns_server_info(self):
        resp = _mcp_dispatch({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        })
        assert resp["result"]["serverInfo"]["name"] == "aip-email-mcp"
        assert "protocolVersion" in resp["result"]

    def test_notifications_initialized_returns_none(self):
        resp = _mcp_dispatch({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        assert resp is None

    def test_tools_list_returns_three_tools(self):
        resp = _mcp_dispatch({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/list", "params": {},
        })
        names = {t["name"] for t in resp["result"]["tools"]}
        assert names == {"authenticate", "list_my_emails", "list_all_emails"}

    def test_unknown_method_returns_error(self):
        resp = _mcp_dispatch({
            "jsonrpc": "2.0", "id": 3,
            "method": "no/such/method", "params": {},
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32601


class TestMcpToolCall:
    """Unit tests for _mcp_tool_call — AIP Layer 1 and Layer 2 in the HTTP path."""

    def test_authenticate_valid_user(self, tmp_path):
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        result = _mcp_tool_call("authenticate",
                                {"username": "alice", "password": "alice123"})
        assert "aat" in result
        assert result["role"] == "user"
        assert "read:own_emails" in result["capabilities"]

    def test_authenticate_admin_gets_extra_capability(self, tmp_path):
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        result = _mcp_tool_call("authenticate",
                                {"username": "admin", "password": "admin123"})
        assert "read:all_emails" in result["capabilities"]
        assert result["role"] == "admin"

    def test_authenticate_bad_credentials_raises(self, tmp_path):
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        with pytest.raises(ValueError, match="invalid username or password"):
            _mcp_tool_call("authenticate",
                           {"username": "alice", "password": "wrong"})

    def test_list_my_emails_with_valid_aat(self, tmp_path):
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        aat = issue_aat("test-agent", "alice", "user", ["read:own_emails"])
        result = _mcp_tool_call("list_my_emails", {"aat": aat})
        assert result["count"] == 2
        for email in result["emails"]:
            assert email["to"] == "alice@example.com"

    def test_list_my_emails_missing_capability_raises(self, tmp_path):
        from auth import AuthError
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        aat = issue_aat("test-agent", "alice", "user", [])  # no capabilities
        with pytest.raises(AuthError, match="lacks capability"):
            _mcp_tool_call("list_my_emails", {"aat": aat})

    def test_list_all_emails_admin(self, tmp_path):
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        aat = issue_aat("admin-agent", "admin", "admin",
                        ["read:own_emails", "read:all_emails"])
        result = _mcp_tool_call("list_all_emails", {"aat": aat})
        assert result["count"] == 5

    def test_list_all_emails_user_denied(self, tmp_path):
        from auth import AuthError
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        aat = issue_aat("test-agent", "alice", "user", ["read:own_emails"])
        with pytest.raises(AuthError):
            _mcp_tool_call("list_all_emails", {"aat": aat})

    def test_full_layer1_to_layer2_flow(self, tmp_path):
        """
        Simulates the full Claude Desktop flow:
          authenticate (Layer 1) → aat → list_my_emails (Layer 2)
        """
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        # Layer 1: Claude calls authenticate tool
        auth_result = _mcp_tool_call("authenticate",
                                     {"username": "alice", "password": "alice123",
                                      "agent_name": "claude-agent"})
        aat = auth_result["aat"]

        # Layer 2: Claude calls list_my_emails with the issued AAT
        email_result = _mcp_tool_call("list_my_emails", {"aat": aat})
        assert email_result["count"] == 2

    def test_aat_from_authenticate_carries_correct_claims(self, tmp_path):
        from auth import validate_aat
        webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
        result = _mcp_tool_call("authenticate",
                                {"username": "alice", "password": "alice123",
                                 "agent_name": "my-agent"})
        claims = validate_aat(result["aat"])
        assert claims["sub"] == "alice"
        assert claims["agent_id"] == "my-agent"
        assert claims["role"] == "user"


# ---------------------------------------------------------------------------
# MCP SSE endpoint — HTTP transport handshake
# ---------------------------------------------------------------------------

class TestMcpSseEndpoint:
    # Starlette's sync TestClient cannot cleanly cancel an async SSE generator,
    # so we don't stream from /aip-playground-mcp here. MCP dispatch logic is
    # covered by TestMcpDispatch and TestMcpToolCall above.

    def test_messages_endpoint_unknown_session_returns_404(self, client):
        resp = client.post("/aip-playground-mcp/messages",
                           params={"sessionId": "does-not-exist"},
                           json={"jsonrpc": "2.0", "id": 1,
                                 "method": "tools/list", "params": {}})
        assert resp.status_code == 404
