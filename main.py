"""
AIP Sample Application - Demo CLI

Demonstrates the full Agent Identity Protocol flow:

  1. Human authenticates (username + password)
  2. AIP Layer 1 issues a signed Agent Authentication Token (AAT)
  3. An "agent" uses the AAT to call tools on the MCP server
  4. AIP Layer 2 (inside the MCP server) validates the token and enforces policy:
       - user role  → can only see their own emails
       - admin role → can see all emails

Run:
    python main.py

Or target a specific scenario:
    python main.py --demo user      # show user-scoped flow
    python main.py --demo admin     # show admin-scoped flow
    python main.py --demo forbidden # show policy denial
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from typing import Dict, List, Optional

from auth import issue_aat, AuthError
from data import authenticate_user

# ---------------------------------------------------------------------------
# ANSI colour helpers (degrade gracefully on non-TTY)
# ---------------------------------------------------------------------------

_USE_COLOUR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _USE_COLOUR:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(t):   return _c("1", t)
def green(t):  return _c("32", t)
def red(t):    return _c("31", t)
def yellow(t): return _c("33", t)
def cyan(t):   return _c("36", t)
def dim(t):    return _c("2", t)


# ---------------------------------------------------------------------------
# MCP client (communicates with mcp_server.py over stdio subprocess)
# ---------------------------------------------------------------------------


class MCPClient:
    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "mcp_server.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self._counter = 0
        self._initialize()

    def _send(self, method: str, params: dict = None, notify: bool = False) -> Optional[dict]:
        self._counter += 1
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        if not notify:
            req["id"] = self._counter

        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()

        if notify:
            return None

        raw = self._proc.stdout.readline()
        return json.loads(raw)

    def _initialize(self) -> None:
        resp = self._send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "aip-demo-client", "version": "0.1.0"},
            },
        )
        assert "result" in resp, f"initialize failed: {resp}"
        self._send("notifications/initialized", notify=True)

    def list_tools(self) -> List[dict]:
        resp = self._send("tools/list")
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        resp = self._send("tools/call", {"name": name, "arguments": arguments})
        return resp

    def close(self) -> None:
        self._proc.stdin.close()
        self._proc.wait()


# ---------------------------------------------------------------------------
# Pretty printing helpers
# ---------------------------------------------------------------------------

def _print_section(title: str) -> None:
    print()
    print(bold(f"{'─' * 60}"))
    print(bold(f"  {title}"))
    print(bold(f"{'─' * 60}"))


def _print_aat_claims(aat: str) -> None:
    import base64
    import json as _json

    parts = aat.split(".")
    if len(parts) != 3:
        return
    padding = 4 - len(parts[1]) % 4
    if padding != 4:
        parts[1] += "=" * padding
    try:
        claims = _json.loads(base64.urlsafe_b64decode(parts[1]))
        print(dim("  AAT claims:"))
        for k, v in claims.items():
            print(dim(f"    {k}: {v}"))
    except Exception:
        pass


def _print_emails(emails: List[dict]) -> None:
    for e in emails:
        print(f"  {cyan(e['id'])}  {bold(e['subject'])}")
        print(f"       From: {e['from']}  →  To: {e['to']}")
        print(f"       {dim(e['timestamp'])}  {e['body']}")
        print()


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------


def demo_user(client: MCPClient) -> None:
    _print_section("SCENARIO 1 — User agent: list own emails")

    # Step 1: Authenticate
    print(yellow("Step 1: Human authenticates as 'alice' (password: alice123)"))
    user = authenticate_user("alice", "alice123")
    assert user, "authentication failed"
    print(f"  Authenticated: {user['name']} <{user['email']}> role={user['role']}")

    # Step 2: Issue AAT (Layer 1)
    print(yellow("\nStep 2: AIP Layer 1 issues a signed Agent Authentication Token (AAT)"))
    aat = issue_aat(
        agent_id="email-assistant-v1",
        user_id=user["id"],
        role=user["role"],
        capabilities=["read:own_emails"],
    )
    print(f"  AAT (JWT):  {aat[:60]}…")
    _print_aat_claims(aat)

    # Step 3: Agent calls MCP tool with AAT
    print(yellow("\nStep 3: Agent calls 'list_my_emails' on MCP server (with AAT)"))
    resp = client.call_tool("list_my_emails", {"aat": aat})

    if "error" in resp:
        print(red(f"  DENIED: {resp['error']['message']}"))
        return

    result = json.loads(resp["result"]["content"][0]["text"])
    print(green(f"  ALLOWED — {result['count']} email(s) returned (alice's own inbox):\n"))
    _print_emails(result["emails"])


def demo_admin(client: MCPClient) -> None:
    _print_section("SCENARIO 2 — Admin agent: list ALL emails")

    # Step 1: Authenticate
    print(yellow("Step 1: Human authenticates as 'admin' (password: admin123)"))
    user = authenticate_user("admin", "admin123")
    assert user, "authentication failed"
    print(f"  Authenticated: {user['name']} <{user['email']}> role={user['role']}")

    # Step 2: Issue AAT (Layer 1) with elevated capabilities
    print(yellow("\nStep 2: AIP Layer 1 issues AAT with admin capabilities"))
    aat = issue_aat(
        agent_id="admin-audit-agent-v1",
        user_id=user["id"],
        role=user["role"],
        capabilities=["read:own_emails", "read:all_emails"],
    )
    print(f"  AAT (JWT):  {aat[:60]}…")
    _print_aat_claims(aat)

    # Step 3: Agent calls admin tool
    print(yellow("\nStep 3: Agent calls 'list_all_emails' on MCP server (with AAT)"))
    resp = client.call_tool("list_all_emails", {"aat": aat})

    if "error" in resp:
        print(red(f"  DENIED: {resp['error']['message']}"))
        return

    result = json.loads(resp["result"]["content"][0]["text"])
    print(green(f"  ALLOWED — {result['count']} email(s) returned (all users' inboxes):\n"))
    _print_emails(result["emails"])


def demo_forbidden(client: MCPClient) -> None:
    _print_section("SCENARIO 3 — Policy denial: user tries to call admin-only tool")

    # Authenticate as a regular user
    print(yellow("Step 1: Human authenticates as 'bob' (password: bob123)"))
    user = authenticate_user("bob", "bob123")
    assert user, "authentication failed"
    print(f"  Authenticated: {user['name']} <{user['email']}> role={user['role']}")

    # Issue AAT with only user-level capabilities
    print(yellow("\nStep 2: AIP Layer 1 issues AAT with user capabilities only"))
    aat = issue_aat(
        agent_id="rogue-agent-v1",
        user_id=user["id"],
        role=user["role"],
        capabilities=["read:own_emails"],  # does NOT include read:all_emails
    )
    print(f"  AAT (JWT):  {aat[:60]}…")
    _print_aat_claims(aat)

    # Attempt to call admin tool
    print(yellow("\nStep 3: Agent attempts 'list_all_emails' (requires admin capability)"))
    resp = client.call_tool("list_all_emails", {"aat": aat})

    if "error" in resp:
        print(red(f"  DENIED by AIP Layer 2: {resp['error']['message']}"))
        print(green("\n  ✓ AIP policy enforcement worked correctly."))
        print(green("    The underlying data store was never reached."))
        print(green("    This denial is recorded in audit.jsonl."))
    else:
        print(red("  BUG: request was unexpectedly allowed!"))


def demo_invalid_token(client: MCPClient) -> None:
    _print_section("SCENARIO 4 — Invalid / tampered AAT")

    print(yellow("Step 1: Simulate a tampered or forged AAT"))
    fake_aat = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkFBVCJ9.eyJzdWIiOiJhbGljZSIsInJvbGUiOiJhZG1pbiJ9.invalidsignature"
    print(f"  Forged AAT: {fake_aat[:60]}…")

    print(yellow("\nStep 2: Agent calls 'list_all_emails' with forged AAT"))
    resp = client.call_tool("list_all_emails", {"aat": fake_aat})

    if "error" in resp:
        print(red(f"  DENIED by AIP Layer 2: {resp['error']['message']}"))
        print(green("\n  ✓ Cryptographic validation caught the tampered token."))
    else:
        print(red("  BUG: forged token was accepted!"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AIP sample application — email service demo"
    )
    parser.add_argument(
        "--demo",
        choices=["user", "admin", "forbidden", "invalid", "all"],
        default="all",
        help="Which scenario to run (default: all)",
    )
    args = parser.parse_args()

    print()
    print(bold("=" * 60))
    print(bold("  Agent Identity Protocol (AIP) — Email Service Demo"))
    print(bold("  github.com/openagentidentityprotocol/agentidentityprotocol"))
    print(bold("=" * 60))
    print()
    print(textwrap.dedent("""\
    Architecture:
      Human → authenticate → AIP Layer 1 (issues AAT)
                                       ↓
      Agent → tools/call + AAT → AIP Layer 2 (validates, enforces)
                                       ↓
                               Email data store
    """))

    client = MCPClient()

    try:
        if args.demo in ("user", "all"):
            demo_user(client)

        if args.demo in ("admin", "all"):
            demo_admin(client)

        if args.demo in ("forbidden", "all"):
            demo_forbidden(client)

        if args.demo in ("invalid", "all"):
            demo_invalid_token(client)

        _print_section("Audit log (audit.jsonl)")
        try:
            with open("audit.jsonl") as f:
                for line in f:
                    record = json.loads(line)
                    outcome_fmt = (
                        green(record["outcome"])
                        if record["outcome"] == "allow"
                        else red(record["outcome"])
                    )
                    print(
                        f"  {dim(record['ts'])}  "
                        f"agent={cyan(record['agent_id'])}  "
                        f"tool={bold(record['tool'])}  "
                        f"outcome={outcome_fmt}  "
                        f"{dim(record.get('detail', ''))}"
                    )
        except FileNotFoundError:
            print("  (no audit log yet)")

        print()
        print(bold("=" * 60))
        print(bold("  Demo complete."))
        print(bold("=" * 60))
        print()
    finally:
        client.close()


if __name__ == "__main__":
    main()
