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

`webapp.py` exposes an MCP server at `GET /mcp` (SSE transport) alongside the
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
