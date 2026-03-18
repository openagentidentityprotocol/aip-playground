"""Shared fixtures for the AIP playground test suite."""

from __future__ import annotations

import pytest

import auth
from auth import issue_aat


# ---------------------------------------------------------------------------
# Auth fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_revoked_jtis():
    """Prevent revocation state from leaking between tests."""
    auth._revoked_jtis.clear()
    yield
    auth._revoked_jtis.clear()


@pytest.fixture
def alice_aat():
    return issue_aat("test-agent", "alice", "user", ["read:own_emails"])


@pytest.fixture
def admin_aat():
    return issue_aat("admin-agent", "admin", "admin",
                     ["read:own_emails", "read:all_emails"])


@pytest.fixture
def bob_aat():
    return issue_aat("bob-agent", "bob", "user", ["read:own_emails"])


# ---------------------------------------------------------------------------
# Webapp fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    """
    FastAPI TestClient with audit log redirected to a temp file so tests
    don't pollute the project-root audit.jsonl.
    """
    import webapp
    from starlette.testclient import TestClient

    webapp.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
    # Also redirect mcp_server audit log if it was imported
    try:
        import mcp_server
        mcp_server.AUDIT_LOG_PATH = str(tmp_path / "audit.jsonl")
    except ImportError:
        pass

    return TestClient(webapp.app, raise_server_exceptions=True)


@pytest.fixture
def authed_client_alice(client):
    """TestClient already logged in as alice (browser session)."""
    client.post("/login", data={"username": "alice", "password": "alice123"},
                follow_redirects=True)
    return client


@pytest.fixture
def authed_client_admin(client):
    """TestClient already logged in as admin (browser session)."""
    client.post("/login", data={"username": "admin", "password": "admin123"},
                follow_redirects=True)
    return client
