# AIP Email Demo — Tutorials

Step-by-step walkthroughs for every way to run and connect to this demo.

---

## Tutorial 1 — CLI demo (`main.py`)

No dependencies beyond Python 3.9+. No web server needed.

```bash
python3 main.py               # run all four scenarios
python3 main.py --demo user       # alice's inbox only
python3 main.py --demo admin      # all inboxes (admin)
python3 main.py --demo forbidden  # policy denial demo
python3 main.py --demo invalid    # tampered token demo
```

What you'll see:

1. Human authenticates → `authenticate_user()` returns a user record
2. AIP Layer 1 issues a signed AAT (JWT) for the agent
3. The agent calls a tool on `mcp_server.py` over stdio, passing the AAT
4. AIP Layer 2 validates the token, enforces role/capability policy
5. The audit log is printed at the end

---

## Tutorial 2 — Web UI (`webapp.py`)

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Start the server

```bash
uvicorn webapp:app --reload --port 8000
```

### 3. Open the browser

Go to **http://localhost:8000** and log in with one of the demo accounts:

| Username | Password | Role |
|---|---|---|
| `alice` | `alice123` | user |
| `bob` | `bob123` | user |
| `admin` | `admin123` | admin |

**User view** — only shows emails addressed to the logged-in user.
**Admin view** — shows all inboxes + the live audit log.

---

## Tutorial 3 — Connect Claude Desktop (or Cursor)

`webapp.py` exposes an MCP server at `GET /aip-playground-mcp` (SSE transport) alongside the
browser UI. Claude connects to it via URL — no subprocess, no extra process to manage.

### The full AIP flow Claude will follow

```
User: "Get alice's emails"
  │
  ▼
Claude calls: authenticate(username="alice", password="alice123")
  │  AIP Layer 1 — issues a signed AAT scoped to alice's role + capabilities
  │  Returns: { "aat": "eyJ...", "role": "user", "capabilities": ["read:own_emails"] }
  │
  ▼
Claude calls: list_my_emails(aat="eyJ...")
  │  AIP Layer 2 — validates AAT signature, checks read:own_emails capability
  │  Returns: alice's emails only (data isolation enforced)
  │
  ▼
Claude: "Alice has 2 emails: Q1 Report from boss@example.com, ..."
```

### 1. Start the web server

```bash
source .venv/bin/activate
uvicorn webapp:app --port 8000
```

Leave this running. The MCP endpoint is now live at `http://localhost:8000/aip-playground-mcp`.

### 2. Add the server to Claude Desktop

Open your Claude Desktop config:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add the entry under `mcpServers`:

```json
{
  "mcpServers": {
    "aip-email": {
      "url": "http://localhost:8000/aip-playground-mcp"
    }
  }
}
```

### 3. Add the server to Cursor

Add to `.cursor/mcp.json` in your project root (or `~/.cursor/mcp.json` globally):

```json
{
  "mcpServers": {
    "aip-email": {
      "url": "http://localhost:8000/aip-playground-mcp"
    }
  }
}
```

### 4. Restart the client

Quit and reopen Claude Desktop or Cursor. The `aip-email` server should appear
as connected in the MCP panel.

### 5. Try it

Start a new conversation:

> "Using the aip-email tools, authenticate as alice with password alice123 and then show me her emails."

Claude will:
1. Call `authenticate` → receive a signed AAT (AIP Layer 1)
2. Call `list_my_emails` with that AAT → receive alice's emails (AIP Layer 2 enforced)

### 6. Try a policy violation

> "Now try to get all emails using alice's token."

Claude will call `list_all_emails` with alice's AAT. AIP Layer 2 will reject it:
alice's token doesn't carry the `read:all_emails` capability. Claude will report
the denial.

> "Now authenticate as admin with password admin123 and get all emails."

Claude authenticates as admin → receives an AAT with `read:all_emails` +
`admin` role → `list_all_emails` succeeds.

### 7. Watch the audit log

While the server is running, open the audit page at
**http://localhost:8000** → log in as admin → click **Audit Log**.

Every tool call Claude makes — allowed and denied — appears here in real time,
tagged `transport: mcp-http`.

---

## Tutorial 4 — Write your own agent

Use the MCP client from `main.py` as a starting point:

```python
from auth import issue_aat
from data import authenticate_user
import subprocess, sys, json

# 1. Authenticate the human
user = authenticate_user("alice", "alice123")

# 2. Issue an AAT for your agent (Layer 1)
aat = issue_aat(
    agent_id="my-custom-agent",
    user_id=user["id"],
    role=user["role"],
    capabilities=["read:own_emails"],
)

# 3. Start the MCP server subprocess
proc = subprocess.Popen(
    [sys.executable, "mcp_server.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
)

def rpc(method, params=None, id=1):
    proc.stdin.write(json.dumps({"jsonrpc":"2.0","id":id,"method":method,"params":params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())

# 4. Handshake
rpc("initialize", {"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"my-agent","version":"0.1"}})

# 5. Call a tool — AIP Layer 2 enforces policy inside mcp_server.py
resp = rpc("tools/call", {"name": "list_my_emails", "arguments": {"aat": aat}}, id=2)
emails = json.loads(resp["result"]["content"][0]["text"])
print(f"Got {emails['count']} email(s)")

proc.stdin.close()
proc.wait()
```

---

## Tutorial 5 — MCP server + web server together (microservice mode)

Run both processes so the stdio MCP server calls the web server's JSON API
instead of reading `data.py` directly.

```bash
# Terminal 1 — web server (authoritative data + browser UI + MCP over HTTP)
source .venv/bin/activate
uvicorn webapp:app --port 8000

# Terminal 2 — stdio MCP server (AIP enforcement proxy, forwards to web server)
export WEBAPP_BASE_URL=http://localhost:8000
python3 mcp_server.py
```

In this mode enforcement runs **twice**: once locally in the MCP server
(fast-fail), and again in the web server (authoritative). The web server
trusts nothing — it re-validates the AAT on every request regardless of
who called it.

---

## Tutorial 6 — External enforcement with aip-go

Use the [aip-go](https://github.com/openagentidentityprotocol/aip-go) proxy to wrap
`mcp_server_plain.py` with AIP Layer 2 enforcement — from Cursor, Claude Desktop, or
the command line. The Python server handles data only; the Go proxy handles policy.

```
AI Client (Cursor / Claude Desktop / CLI)
  │  stdio JSON-RPC
  ▼
aip-go proxy         ← Layer 2: tool allowlist, role rules, audit log
  │  stdio JSON-RPC
  ▼
mcp_server_plain.py  ← data only, zero enforcement
  │
  ▼
data.py              ← in-memory email store
```

### Why `mcp_server_plain.py`?

`mcp_server_plain.py` accepts `user_id` directly — no AAT, no token validation, no role
checks. It is the correct target for an external proxy. `mcp_server.py` already bundles
AIP internally; wrapping it with aip-go would enforce policy twice.

| Server | Enforcement | Use with |
|---|---|---|
| `mcp_server_plain.py` | None — data only | aip-go proxy |
| `mcp_server.py` | Bundled Layer 1 + 2 | Standalone CLI demo |

---

### Step 1 — Build aip-go

```bash
git clone https://github.com/openagentidentityprotocol/aip-go
cd aip-go
make build
# binary at: ./bin/aip
```

Note the full path to the binary — you'll need it in every config below.

---

### Step 2 — Create a policy file

Save this to `~/.config/aip/playground-policy.yaml`:

```yaml
apiVersion: aip.io/v1alpha1
kind: AgentPolicy
metadata:
  name: playground-policy
spec:
  mode: enforce
  allowed_tools:
    - list_my_emails
    - list_all_emails
  tool_rules:
    - tool: list_all_emails
      action: ask   # prompt for approval before running
```

This policy:
- **allows** `list_my_emails` silently
- **prompts** before `list_all_emails` (admin-level read)
- **blocks** any other tool not in `allowed_tools`

Change `action: ask` to `action: block` to deny admin reads outright, or remove the rule
to allow silently.

---

### Step 3 — Test from the command line

Verify the proxy works before wiring it into a client.

**Pipe a single request:**

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | \
  /path/to/aip-go/bin/aip \
  --policy ~/.config/aip/playground-policy.yaml \
  --target "python3 /path/to/sample-application/mcp_server_plain.py" \
  --verbose
```

**Interactive session via named pipes:**

```bash
# Terminal 1 — start the proxy
mkfifo /tmp/aip_in /tmp/aip_out
/path/to/aip-go/bin/aip \
  --policy ~/.config/aip/playground-policy.yaml \
  --target "python3 /path/to/sample-application/mcp_server_plain.py" \
  --verbose \
  < /tmp/aip_in > /tmp/aip_out &
cat /tmp/aip_out &

# Terminal 2 — send JSON-RPC messages
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' > /tmp/aip_in
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' > /tmp/aip_in
echo '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_my_emails","arguments":{"user_id":"alice"}}}' > /tmp/aip_in
```

Expected: `list_my_emails` returns Alice's emails. Try a blocked tool:

```bash
echo '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"nonexistent_tool","arguments":{}}}' > /tmp/aip_in
```

Expected: error response with code `-32001` (policy denied).

**View the audit log:**

```bash
# All decisions
cat aip-audit.jsonl | jq '.'

# Blocked calls only
cat aip-audit.jsonl | jq 'select(.decision == "BLOCK")'

# Tool call summary
cat aip-audit.jsonl | jq -r '.tool' | sort | uniq -c | sort -rn
```

---

### Step 4 — Connect Cursor

**Generate the config entry automatically:**

```bash
/path/to/aip-go/bin/aip --generate-cursor-config \
  --policy ~/.config/aip/playground-policy.yaml \
  --target "python3 /path/to/sample-application/mcp_server_plain.py"
```

Copy the output and paste it into `~/.cursor/mcp.json` (or `.cursor/mcp.json` in your
project root). It will look like:

```json
{
  "mcpServers": {
    "aip-playground": {
      "command": "/path/to/aip-go/bin/aip",
      "args": [
        "--policy", "/Users/<you>/.config/aip/playground-policy.yaml",
        "--target", "python3 /path/to/sample-application/mcp_server_plain.py"
      ]
    }
  }
}
```

Restart Cursor. The `aip-playground` server should appear in the MCP panel.

**Try it in Cursor:**

> "List alice's emails."

Cursor calls `list_my_emails` with `user_id=alice` → aip-go checks the allowlist →
forwards to `mcp_server_plain.py` → returns alice's emails.

> "List all emails."

Cursor calls `list_all_emails` → aip-go triggers the `action: ask` rule → a native OS
dialog appears asking you to approve or deny. Approve → emails returned. Deny →
`-32001` error reported to Cursor.

---

### Step 5 — Connect Claude Desktop

Add to your Claude Desktop config manually:

| OS | Config path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "aip-playground": {
      "command": "/path/to/aip-go/bin/aip",
      "args": [
        "--policy", "/Users/<you>/.config/aip/playground-policy.yaml",
        "--target", "python3 /path/to/sample-application/mcp_server_plain.py"
      ]
    }
  }
}
```

Restart Claude Desktop. Start a conversation:

> "Using aip-playground tools, list emails for alice."

Claude calls `list_my_emails` → aip-go enforces → data returned.

> "Now list all emails."

Claude calls `list_all_emails` → `action: ask` dialog fires. The playground policy
requires human approval for admin reads even when Claude is the caller.

---

### Step 6 — Monitor mode (dry run)

To test a policy without actually blocking anything, change `mode: enforce` to
`mode: monitor` in `playground-policy.yaml`. All calls pass through; violations are
logged but not blocked. Useful for validating a new policy before enabling enforcement.

```yaml
spec:
  mode: monitor   # was: enforce
  ...
```

---

### Troubleshooting

| Issue | Fix |
|---|---|
| `Policy file not found` | Use an absolute path to `playground-policy.yaml` |
| `Empty response from proxy` | Run the `--target` command on its own to verify it works |
| `Permission denied` | `chmod +x /path/to/aip-go/bin/aip` |
| `action: ask` auto-denies | Expected in headless/CI — use `action: allow` for automation |
| `-32001` on allowed tool | Check spelling in `allowed_tools` matches `tools/list` output |

Enable `--verbose` and redirect stderr to a file for full message flow:

```bash
/path/to/aip-go/bin/aip \
  --policy ~/.config/aip/playground-policy.yaml \
  --target "python3 /path/to/sample-application/mcp_server_plain.py" \
  --verbose 2>aip-debug.log
```

See **[implementation.md](implementation.md)** for Docker and Kubernetes deployment patterns.
