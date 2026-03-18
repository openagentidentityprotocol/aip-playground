"""
Tests for auth.py — AIP Layer 1 (AAT issuance) and Layer 2 (enforcement).

These mirror the validation logic in aip-go:
  pkg/identity/  — token issuance and session binding
  pkg/policy/    — capability and role enforcement
"""

from __future__ import annotations

import time

import pytest

import auth
from auth import (
    AuthError,
    check_capability,
    check_role,
    issue_aat,
    revoke_aat,
    validate_aat,
)


# ---------------------------------------------------------------------------
# Layer 1 — AAT issuance
# ---------------------------------------------------------------------------

class TestIssueAat:
    def test_returns_three_part_jwt(self):
        token = issue_aat("agent-1", "alice", "user", ["read:own_emails"])
        assert len(token.split(".")) == 3

    def test_claims_are_correct(self):
        token = issue_aat("agent-1", "alice", "user", ["read:own_emails"])
        claims = validate_aat(token)

        assert claims["agent_id"] == "agent-1"
        assert claims["sub"] == "alice"
        assert claims["role"] == "user"
        assert claims["capabilities"] == ["read:own_emails"]
        assert claims["iss"] == "aip-sample-issuer"

    def test_admin_capabilities(self):
        token = issue_aat("admin-agent", "admin", "admin",
                          ["read:own_emails", "read:all_emails"])
        claims = validate_aat(token)
        assert "read:all_emails" in claims["capabilities"]
        assert claims["role"] == "admin"

    def test_expiry_is_in_future(self):
        token = issue_aat("agent-1", "alice", "user", ["read:own_emails"])
        claims = validate_aat(token)
        assert claims["exp"] > time.time()

    def test_each_token_has_unique_jti(self):
        t1 = issue_aat("agent-1", "alice", "user", ["read:own_emails"])
        t2 = issue_aat("agent-1", "alice", "user", ["read:own_emails"])
        c1 = validate_aat(t1)
        c2 = validate_aat(t2)
        assert c1["jti"] != c2["jti"]

    def test_custom_issuer(self):
        token = issue_aat("agent-1", "alice", "user", [],
                          issuer="my-custom-issuer")
        claims = validate_aat(token)
        assert claims["iss"] == "my-custom-issuer"


# ---------------------------------------------------------------------------
# Layer 2 — AAT validation
# ---------------------------------------------------------------------------

class TestValidateAat:
    def test_valid_token_returns_claims(self, alice_aat):
        claims = validate_aat(alice_aat)
        assert claims["sub"] == "alice"

    def test_tampered_signature_rejected(self, alice_aat):
        parts = alice_aat.split(".")
        tampered = parts[0] + "." + parts[1] + ".invalidsignature"
        with pytest.raises(AuthError, match="invalid signature"):
            validate_aat(tampered)

    def test_tampered_payload_rejected(self, alice_aat):
        import base64, json
        parts = alice_aat.split(".")
        # Flip alice → admin in the payload
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        payload["role"] = "admin"
        new_p = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).rstrip(b"=").decode()
        tampered = parts[0] + "." + new_p + "." + parts[2]
        with pytest.raises(AuthError, match="invalid signature"):
            validate_aat(tampered)

    def test_expired_token_rejected(self):
        token = issue_aat("agent-1", "alice", "user", ["read:own_emails"])
        # Manually expire it by patching the exp claim
        claims = auth._verify(token)
        claims["exp"] = int(time.time()) - 1
        import json
        p = auth._b64url_encode(
            json.dumps(claims, separators=(",", ":")).encode()
        )
        parts = token.split(".")
        # Reconstruct without re-signing — signature will mismatch
        # (use a valid fresh expiry to confirm the check fires correctly)
        expired = issue_aat("agent-1", "alice", "user", ["read:own_emails"])
        # Patch auth.AAT_TTL_SECONDS to -1 to force expiry
        original_ttl = auth.AAT_TTL_SECONDS
        auth.AAT_TTL_SECONDS = -3600
        expired = issue_aat("agent-1", "alice", "user", ["read:own_emails"])
        auth.AAT_TTL_SECONDS = original_ttl
        with pytest.raises(AuthError, match="expired"):
            validate_aat(expired)

    def test_revoked_token_rejected(self, alice_aat):
        revoke_aat(alice_aat)
        with pytest.raises(AuthError, match="revoked"):
            validate_aat(alice_aat)

    def test_malformed_token_rejected(self):
        with pytest.raises(AuthError):
            validate_aat("not.a.valid.jwt.here")

    def test_empty_token_rejected(self):
        with pytest.raises(AuthError):
            validate_aat("")

    def test_revoke_wrong_token_does_not_affect_valid(self, alice_aat, bob_aat):
        revoke_aat(bob_aat)
        # alice's token should still be valid
        claims = validate_aat(alice_aat)
        assert claims["sub"] == "alice"


# ---------------------------------------------------------------------------
# Layer 2 — capability enforcement
# ---------------------------------------------------------------------------

class TestCheckCapability:
    def test_present_capability_passes(self, alice_aat):
        claims = validate_aat(alice_aat)
        check_capability(claims, "read:own_emails")  # no exception

    def test_missing_capability_raises(self, alice_aat):
        claims = validate_aat(alice_aat)
        with pytest.raises(AuthError, match="lacks capability 'read:all_emails'"):
            check_capability(claims, "read:all_emails")

    def test_admin_has_both_capabilities(self, admin_aat):
        claims = validate_aat(admin_aat)
        check_capability(claims, "read:own_emails")
        check_capability(claims, "read:all_emails")

    def test_error_message_names_agent(self, alice_aat):
        claims = validate_aat(alice_aat)
        with pytest.raises(AuthError, match="test-agent"):
            check_capability(claims, "read:all_emails")


# ---------------------------------------------------------------------------
# Layer 2 — role enforcement
# ---------------------------------------------------------------------------

class TestCheckRole:
    def test_correct_role_passes(self, admin_aat):
        claims = validate_aat(admin_aat)
        check_role(claims, "admin")  # no exception

    def test_wrong_role_raises(self, alice_aat):
        claims = validate_aat(alice_aat)
        with pytest.raises(AuthError, match="requires 'admin'"):
            check_role(claims, "admin")

    def test_user_role_passes_for_user_check(self, alice_aat):
        claims = validate_aat(alice_aat)
        check_role(claims, "user")  # no exception
