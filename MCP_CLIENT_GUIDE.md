# Connecting an MCP Client to the AIP Email Server

This guide shows how any MCP-compatible client — Claude Desktop, Cursor, a
custom agent, or a raw terminal — can connect to the email MCP servers and
call their tools.

Two server files are provided so you can see the contrast between an
unprotected server and one with AIP enforcement active:

| File | AIP enforcement | Use |
|---|---|---|
| `mcp_server_plain.py` | ❌ None — fully open | Baseline / "before" picture |
| `mcp_server.py` | ✅ Full Layer 2 | AIP demo / production pattern |

---

## The plain server (`mcp_server_plain.py`)

`mcp_server_plain.py` is a normal, unprotected MCP server.  It has no
knowledge of AIP, no token validation, no capability checks, and no audit log.
Any caller can request any user's emails or all emails in the system simply by
supplying a `user_id` string — there is nothing to stop them.

This is the "god mode" baseline the AIP specification is designed to replace.

### Tools exposed

| Tool | Input | Returns |
|---|---|---|
| `list_my_emails` | `user_id` (string) | Emails for that user — no auth check |
| `list_all_emails` | _(none)_ | Every email in the system — no auth check |

### Running the plain server

```bash
python3 mcp_server_plain.py
```

### Claude Desktop config (plain server)

```json
{
  "mcpServers": {
    "email-plain": {
      "command": "python3",
      "args": ["/absolute/path/to/sample-application/mcp_server_plain.py"]
    }
  }
}
```

### Raw terminal — plain server example

```bash
python3 mcp_server_plain.py
```

Paste line by line:

**Initialize**
```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}
```
**Initialized notification**
```json
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
```
**List alice's emails — no token needed**
```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_my_emails","arguments":{"user_id":"alice"}}}
```
**List ALL emails — no token, no role check**
```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_all_emails","arguments":{}}}
```

Both calls succeed immediately.  Any agent, or any attacker who has gained
control of an agent through prompt injection, can read every email in the
system.

---

## Side-by-side comparison

| Behaviour | `mcp_server_plain.py` | `mcp_server.py` |
|---|---|---|
| Requires a signed AAT | No | Yes — rejected if missing or invalid |
| Validates token signature | No | Yes — HMAC-SHA256 |
| Checks token expiry | No | Yes |
| Checks revocation list | No | Yes |
| Enforces capabilities | No | Yes — `read:own_emails` / `read:all_emails` |
| Enforces role | No | Yes — `list_all_emails` requires `admin` |
| Data isolation | None — caller picks any `user_id` | Enforced — server resolves user from AAT `sub` claim |
| Audit log | None | Yes — every allow/deny written to `audit.jsonl` |
| What a prompt-injected agent can do | Read any inbox or all inboxes | Only what its AAT permits; denied calls are logged |

---

## How the server is invoked

`mcp_server.py` uses the **stdio transport**: the client spawns it as a
subprocess and communicates over its stdin/stdout with newline-delimited
JSON-RPC 2.0 messages.  There is no separate port to open; the MCP client
manages the process lifetime.

```
MCP client
  │  spawn subprocess
  ▼
python3 mcp_server.py
  │  JSON-RPC 2.0 over stdin / stdout
  ▼
AIP Layer 2 enforcement  (validate AAT → check capability → audit → data)
```

---

## Prerequisites

1. **Python 3.9+** installed and on your PATH.
2. The project directory cloned/downloaded so `auth.py`, `data.py`, and
   `mcp_server.py` are all in the same folder.
3. No pip dependencies are needed for the MCP server itself.

---

## Step 1 — Obtain an AAT

Every tool call must include a signed `aat` argument.  Obtain one by running
the helper script below from inside the project directory, or by calling
`issue_aat()` from `auth.py` directly in your own code.

### Option A — one-liner (terminal)

```bash
python3 - <<'EOF'
from auth import issue_aat

# --- User token (alice, read own emails only) ---
print(issue_aat(
    agent_id="my-agent-v1",
    user_id="alice",
    role="user",
    capabilities=["read:own_emails"],
))
EOF
```

### Option B — admin token

```bash
python3 - <<'EOF'
from auth import issue_aat

print(issue_aat(
    agent_id="my-admin-agent-v1",
    user_id="admin",
    role="admin",
    capabilities=["read:own_emails", "read:all_emails"],
))
EOF
```

> **Security note:** In a real AIP deployment the token would be issued by a
> dedicated Token Issuer backed by the agent's registered key pair.  In this
> demo `issue_aat()` plays that role.  Keep `AIP_SECRET_KEY` in sync between
> the process that issues tokens and `mcp_server.py` (both read the same env
> var).

---

## Step 2 — Configure your MCP client

### Claude Desktop

Add the following block to your Claude Desktop config file.

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "aip-email": {
      "command": "python3",
      "args": ["/absolute/path/to/sample-application/mcp_server.py"],
      "env": {
        "AIP_SECRET_KEY": "dev-secret-change-in-production"
      }
    }
  }
}
```

Replace `/absolute/path/to/sample-application` with the real path on your
machine.  Restart Claude Desktop — the tools `list_my_emails` and
`list_all_emails` will appear in the tool picker.

When Claude calls a tool it must pass the `aat` argument.  You can prime it
with a system prompt:

```
You have access to an email service.
When the user asks about their emails, call list_my_emails with this aat:
eyJhbGciOiJIUzI1NiIsInR5cCI6IkFBVCJ9.<your-token-here>
```

---

### Cursor / VS Code (MCP extension)

In your workspace `.cursor/mcp.json` (or VS Code equivalent):

```json
{
  "mcpServers": {
    "aip-email": {
      "command": "python3",
      "args": ["mcp_server.py"],
      "cwd": "/absolute/path/to/sample-application",
      "env": {
        "AIP_SECRET_KEY": "dev-secret-change-in-production"
      }
    }
  }
}
```

---

### Custom agent / Python client

Wire up the same `MCPClient` class already used by `main.py`:

```python
import json, subprocess, sys

class MCPClient:
    def __init__(self, script="mcp_server.py"):
        self._proc = subprocess.Popen(
            [sys.executable, script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self._id = 0
        self._handshake()

    def _rpc(self, method, params=None, notify=False):
        self._id += 1
        req = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            req["id"] = self._id
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        if notify:
            return None
        return json.loads(self._proc.stdout.readline())

    def _handshake(self):
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "my-agent", "version": "1.0"},
        })
        self._rpc("notifications/initialized", notify=True)

    def call(self, tool, **kwargs):
        return self._rpc("tools/call", {"name": tool, "arguments": kwargs})

    def close(self):
        self._proc.stdin.close()
        self._proc.wait()
```

Usage:

```python
from auth import issue_aat

aat = issue_aat(
    agent_id="my-agent-v1",
    user_id="alice",
    role="user",
    capabilities=["read:own_emails"],
)

client = MCPClient()
resp = client.call("list_my_emails", aat=aat)
print(resp["result"]["content"][0]["text"])
client.close()
```

---

### Raw terminal (manual testing)

You can drive the server by hand — useful for debugging:

```bash
python3 mcp_server.py
```

Then paste these lines one at a time (press Enter after each):

**1. Initialize**
```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}
```

**2. Initialized notification** (no response expected)
```json
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
```

**3. List available tools**
```json
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

**4. Call `list_my_emails` with a user AAT**
```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_my_emails","arguments":{"aat":"<YOUR_USER_AAT>"}}}
```

**5. Call `list_all_emails` with an admin AAT**
```json
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_all_emails","arguments":{"aat":"<YOUR_ADMIN_AAT>"}}}
```

---

## Step 3 — Tool reference

### `list_my_emails`

Returns emails belonging to the user encoded in the AAT.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `aat` | string | ✅ | Signed AAT with `read:own_emails` capability |

**AIP enforcement:** validates token signature → checks `read:own_emails`
capability → resolves user from `sub` claim → returns only that user's emails.

**Example response:**
```json
{
  "emails": [
    {
      "id": "e1",
      "to": "alice@example.com",
      "from": "boss@example.com",
      "subject": "Q1 Report",
      "body": "Please review the Q1 report attached.",
      "timestamp": "2025-01-10T09:00:00"
    }
  ],
  "count": 1
}
```

---

### `list_all_emails`

Returns all emails in the system.  Admin role and `read:all_emails` capability
required.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `aat` | string | ✅ | Signed AAT with `read:all_emails` capability **and** `admin` role |

**AIP enforcement:** validates token → checks `read:all_emails` capability →
checks `role == "admin"` → returns full email list.

**Denied example** (user token calling admin tool):
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "error": {
    "code": -32001,
    "message": "AIP policy denied: agent 'my-agent-v1' lacks capability 'read:all_emails'"
  }
}
```

---

## AIP enforcement summary

Every tool call goes through the same gate regardless of which client sends it:

```
tools/call received
      │
      ▼
validate_aat(aat)          ← signature OK? not expired? not revoked?
      │  fail → -32001 error + audit deny
      ▼
check_capability(claims)   ← does the token carry the required capability?
      │  fail → -32001 error + audit deny
      ▼
check_role(claims)         ← (admin tools only) role == "admin"?
      │  fail → -32001 error + audit deny
      ▼
data store access          ← isolated to the token's sub / role
      │
      ▼
audit log (allow) + result returned to client
```

All decisions — allow and deny — are appended to `audit.jsonl` in the working
directory.  When the web server (`webapp.py`) is also running, both HTTP and
MCP events appear in the same log, tagged with `"transport": "http"` or
`"transport": "mcp"`.

---

## Where the AIP proxy enforcement layer fits

### This demo vs. production AIP

In this sample application, Layer 2 enforcement is **embedded directly inside
`mcp_server.py`**.  `_enforce_and_run()` validates the AAT and checks policy
before every tool call, all within the same Python process.  This keeps the
demo self-contained and easy to follow, but it is not how AIP is intended to
be deployed in production.

The AIP specification describes enforcement as a **separate proxy process**
that sits transparently between the MCP client and the real MCP server.  The
server itself remains completely unmodified — it never knows the proxy exists.

```
┌──────────────┐     ┌───────────────────────┐     ┌───────────────────┐
│  MCP Client  │────▶│  AIP Proxy (Layer 2)  │────▶│  mcp_server.py    │
│ Claude / etc │◀────│  policy enforcement   │◀────│  (unmodified)     │
└──────────────┘     └───────────────────────┘     └───────────────────┘
                              │
                              ▼
                        audit.jsonl
```

### This demo (embedded enforcement)

```
MCP Client
    │  JSON-RPC over stdio
    ▼
mcp_server.py
    ├── _enforce_and_run()   ← Layer 2 lives HERE, inside the server
    │       validate_aat()
    │       check_capability()
    │       check_role()
    │       _audit()
    └── data store (emails)
```

**Tradeoff:** Simple and portable — one file, no extra process — but the
enforcement logic is coupled to the server.  Changing policy means changing
server code.

### Production AIP (external proxy)

The reference implementation is the
[Go proxy](https://github.com/openagentidentityprotocol/agentidentityprotocol/tree/main/implementations/go-proxy).
It wraps any MCP server command with a single binary:

```bash
./bin/aip --policy policy.yaml -- python3 mcp_server.py
```

The proxy intercepts every JSON-RPC message on stdin/stdout, evaluates it
against a YAML policy file, then either forwards it to the real server or
blocks it — without the server code changing at all.

```
MCP Client
    │  JSON-RPC over stdio
    ▼
aip (go-proxy)                    ← Layer 2, external process
    │  policy.yaml evaluation:
    │    • tool allowlist check
    │    • AAT signature + expiry
    │    • argument regex validation
    │    • rate limiting
    │    • DLP scanning (redact secrets from responses)
    │    • human-in-the-loop approval (action: ask)
    │    • audit log write
    │  pass or block ↓
    ▼
python3 mcp_server.py             ← real server, completely unchanged
    └── data store (emails)
```

The policy file controls everything without touching server code:

```yaml
# policy.yaml — example for this email server
apiVersion: aip.io/v1alpha1
kind: AgentPolicy
metadata:
  name: email-server-policy
spec:
  # Only these two tools may be called at all
  allowed_tools:
    - list_my_emails
    - list_all_emails

  tool_rules:
    - tool: list_my_emails
      action: allow

    - tool: list_all_emails
      action: ask        # require human approval before returning all emails

  mode: enforce          # block anything not explicitly allowed
```

### Where each Layer 2 check lives in this demo

The table below maps every enforcement check the AIP spec requires to where it
is implemented in this sample, and where it would live in a production
proxy deployment:

| AIP Layer 2 check | This demo (`mcp_server.py`) | Production (go-proxy) |
|---|---|---|
| AAT signature verification | `validate_aat()` in `_enforce_and_run()` | Proxy verifies before forwarding |
| Token expiry | `validate_aat()` | Proxy checks `exp` claim |
| Revocation list | `_revoked_jtis` set in `auth.py` | Proxy queries AIP Registry |
| Capability check | `check_capability()` | Policy `allowed_tools` + AAT claims |
| Role check | `check_role()` | Policy rule + AAT `role` claim |
| Argument validation | _(not implemented in demo)_ | `allow_args` regex in policy YAML |
| Rate limiting | _(not implemented in demo)_ | `rate_limit` in policy YAML |
| DLP / secret redaction | _(not implemented in demo)_ | `dlp` config in policy YAML |
| Human-in-the-loop approval | _(not implemented in demo)_ | `action: ask` in policy YAML |
| Audit log | `_audit()` → `audit.jsonl` | Proxy writes to JSONL audit trail |
| Data isolation | Enforced in tool handler | Enforced in tool handler (unchanged) |

### How to add the proxy to this demo

If you want to run the production go-proxy **in front of** `mcp_server.py`
(the real intended topology), the steps are:

1. **Build or download** the `aip` binary from
   [implementations/go-proxy](https://github.com/openagentidentityprotocol/agentidentityprotocol/tree/main/implementations/go-proxy).

2. **Strip the embedded enforcement** from `mcp_server.py` — remove the AAT
   argument from both tools and replace `_enforce_and_run()` with direct calls
   to `get_emails_for_user()` / `get_all_emails()`.  The server becomes a
   plain, unprotected MCP server.

3. **Write a policy file** (`policy.yaml`) that expresses the same rules the
   proxy now enforces externally (see example above).

4. **Update your MCP client config** to point at the proxy binary instead of
   the Python server directly:

   ```json
   {
     "mcpServers": {
       "aip-email": {
         "command": "/path/to/aip",
         "args": [
           "--policy", "/path/to/policy.yaml",
           "--",
           "python3", "/path/to/mcp_server.py"
         ],
         "env": {
           "AIP_SECRET_KEY": "dev-secret-change-in-production"
         }
       }
     }
   }
   ```

   The MCP client now talks to `aip`, which intercepts every message, enforces
   policy, and forwards allowed calls to `python3 mcp_server.py` — exactly
   the production AIP topology.

### Why the demo bundles enforcement inside the server

| Concern | Embedded (this demo) | External proxy (production) |
|---|---|---|
| Dependencies | Zero — plain Python | Go binary required |
| Server changes needed | Yes — server is policy-aware | No — server is unchanged |
| Policy updates | Requires server code change | Edit `policy.yaml`, restart proxy |
| DLP / rate limiting | Not included | Built into proxy |
| Auditability | Single process audit log | Proxy owns the audit trail |
| Prompt injection defence | Partial — model can still craft AAT args | Full — proxy intercepts before model output reaches server |

The embedded approach is intentional for this sample: it makes the enforcement
logic visible and readable in one file.  In a real deployment the proxy
provides stronger guarantees because **the enforcement boundary is outside the
server process entirely** — a compromised or prompt-injected server cannot
bypass it.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AIP_SECRET_KEY` | `dev-secret-change-in-production` | HMAC key used to sign and verify AATs.  Must be the same value in every process that issues or validates tokens. |

Set it consistently across the token issuer and the server:

```bash
export AIP_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
# then start the server in the same shell, or pass it via the MCP client config
python3 mcp_server.py
```
