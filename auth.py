"""
Agent Identity Protocol (AIP) - Authentication Module

Implements the two-layer AIP architecture:
  Layer 1: Identity - issues signed Agent Authentication Tokens (AAT)
  Layer 2: Enforcement - validates AAT claims and applies policy

Reference: https://github.com/openagentidentityprotocol/agentidentityprotocol
"""

from __future__ import annotations

import hmac
import hashlib
import json
import time
import base64
import secrets
import os
from typing import Optional, List

# --- Simple HMAC-SHA256 JWT (no external dependencies) ---

SECRET_KEY = os.environ.get("AIP_SECRET_KEY", "dev-secret-change-in-production")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign(header: dict, payload: dict) -> str:
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    msg = f"{h}.{p}".encode()
    sig = hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def _verify(token: str) -> Optional[dict]:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    h, p, sig = parts
    msg = f"{h}.{p}".encode()
    expected = hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected), sig):
        return None
    try:
        return json.loads(_b64url_decode(p))
    except Exception:
        return None


# --- Layer 1: Identity - AAT Issuance ---

AAT_TTL_SECONDS = 3600  # 1 hour


def issue_aat(
    agent_id: str,
    user_id: str,
    role: str,
    capabilities: List[str],
    issuer: str = "aip-sample-issuer",
) -> str:
    """
    Issue a signed Agent Authentication Token (AAT).

    The AAT carries signed claims about the agent:
      - who issued its identity (iss)
      - which user it's acting on behalf of (sub / user_id)
      - what capabilities it declared (capabilities)
      - when it was issued and expires (iat, exp)
      - the agent's own identifier (agent_id)
      - the agent's role / permission level (role)

    In a production AIP deployment, this token would be signed by a
    dedicated Token Issuer backed by the agent's key pair registered
    with the AIP Registry.
    """
    now = int(time.time())
    payload = {
        # Standard JWT claims
        "iss": issuer,
        "sub": user_id,
        "iat": now,
        "exp": now + AAT_TTL_SECONDS,
        "jti": secrets.token_hex(8),  # unique token ID for revocation checks
        # AIP-specific claims
        "agent_id": agent_id,
        "role": role,
        "capabilities": capabilities,
    }
    header = {"alg": "HS256", "typ": "AAT"}
    return _sign(header, payload)


# --- Layer 2: Enforcement - AAT Validation ---

# Revoked token IDs (in production: check AIP Registry revocation list)
_revoked_jtis: set[str] = set()


def revoke_aat(token: str) -> None:
    """Revoke an AAT by adding its jti to the revocation list."""
    claims = _verify(token)
    if claims and "jti" in claims:
        _revoked_jtis.add(claims["jti"])


class AuthError(Exception):
    pass


def validate_aat(token: str) -> dict:
    """
    Layer 2 enforcement: cryptographically validate an AAT and return its claims.

    Checks performed (mirrors the AIP proxy validation logic):
      1. Signature verification
      2. Token expiry
      3. Revocation list check
    """
    claims = _verify(token)
    if claims is None:
        raise AuthError("invalid signature or malformed token")

    if time.time() > claims.get("exp", 0):
        raise AuthError("token expired")

    if claims.get("jti") in _revoked_jtis:
        raise AuthError("token revoked")

    return claims


def check_capability(claims: dict, required: str) -> None:
    """Assert that an agent's AAT grants a specific capability."""
    if required not in claims.get("capabilities", []):
        raise AuthError(
            f"agent '{claims.get('agent_id')}' lacks capability '{required}'"
        )


def check_role(claims: dict, required_role: str) -> None:
    """Assert that an agent's AAT carries the required role."""
    if claims.get("role") != required_role:
        raise AuthError(
            f"agent '{claims.get('agent_id')}' has role '{claims.get('role')}'"
            f", requires '{required_role}'"
        )
