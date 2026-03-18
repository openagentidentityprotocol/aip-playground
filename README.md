# AIP Email Service — Sample Application

A minimal Python demo that shows the
[Agent Identity Protocol (AIP)](https://github.com/openagentidentityprotocol/agentidentityprotocol)
in action — with both a **CLI demo** and a **web UI**.

## What it demonstrates

| Concept | Where |
|---|---|
| **Layer 1 – Identity** — issues signed Agent Authentication Tokens (AAT) | `auth.py` |
| **Layer 2 – Enforcement** — validates AAT on every request, enforces policy | `mcp_server.py`, `webapp.py` |
| **Role-based data isolation** — user sees only own emails; admin sees all | `mcp_server.py`, `webapp.py` |
| **Capability checks** — tokens carry explicit capability claims | `auth.py` |
| **Immutable audit log** — every allow/deny appended to `audit.jsonl` | `mcp_server.py`, `webapp.py` |
| **Transport-agnosticism** — AAT works over stdio, HTTP Bearer, and MCP-over-HTTP | `auth.py` |
| **MCP stdio transport** — agent interface speaks JSON-RPC 2.0 | `mcp_server.py` |
| **MCP over HTTP/SSE** — Claude Desktop / Cursor connect via URL, no subprocess | `webapp.py` |
| **Separate auth paths** — human browser session vs. agent AAT are distinct | `webapp.py` |

## Architecture

```
                        auth.py + data.py
                       (shared, unchanged)
                        ┌──────┴──────┐
                        │             │
              mcp_server.py       webapp.py
           (AIP Layer 2 for    ┌── Browser routes ──────────────────────┐
            MCP stdio agents)  │  plain session {user_id, role}         │
                        │      │  no AAT — humans are not agents         │
                   main.py     ├── JSON API routes (/api/*) ─────────────┤
                (CLI demo)     │  Authorization: Bearer <aat>            │
                               │  AIP Layer 2 enforcement                │
                               ├── MCP over HTTP/SSE (/aip-playground-mcp)┤
                               │  Claude Desktop / Cursor connect here   │
                               │  authenticate tool → issues AAT (L1)    │
                               │  list_* tools → enforce AAT (L2)        │
                               └─────────────────────────────────────────┘
```

**Key insight:** `auth.py` is transport-agnostic. The same `validate_aat()` /
`check_capability()` / `check_role()` functions enforce policy across all three
agent paths: stdio MCP, HTTP Bearer API, and MCP-over-HTTP/SSE.

## Files

```
.
├── auth.py               AIP Layer 1: AAT issuance & Layer 2: validation helpers
├── data.py               In-memory email store and user registry
├── mcp_server.py         AIP-aware MCP server (stdio JSON-RPC 2.0 transport)
├── mcp_server_plain.py   Plain MCP server — no enforcement (baseline / aip-go target)
├── main.py               CLI demo — runs all four scenarios
├── webapp.py             FastAPI web UI + JSON API + MCP over HTTP/SSE
├── requirements.txt      Web UI dependencies
├── requirements-dev.txt  Test dependencies (pytest, httpx)
├── tutorials.md          Step-by-step walkthroughs
├── tests/                Test suite (94 tests)
│   ├── test_auth.py      AIP Layer 1 + Layer 2 unit tests
│   ├── test_data.py      Data store unit tests
│   ├── test_mcp.py       stdio MCP server + plain server integration tests
│   └── test_webapp.py    Browser routes, JSON API, MCP dispatch tests
└── audit.jsonl           Created at runtime; one JSON line per event
```

## Quick start

### Web UI

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn webapp:app --reload --port 8000
```

Open **http://localhost:8000** and sign in with any demo account:

| Username | Password | Role |
|---|---|---|
| `alice` | `alice123` | user — sees own inbox only |
| `bob` | `bob123` | user — sees own inbox only |
| `admin` | `admin123` | admin — sees all inboxes + audit log |

### CLI demo

```bash
python3 main.py             # run all four scenarios
python3 main.py --demo user       # user agent: own inbox only
python3 main.py --demo admin      # admin agent: all inboxes
python3 main.py --demo forbidden  # user tries admin tool → denied
python3 main.py --demo invalid    # forged token → denied
```

> The CLI requires no dependencies — plain Python 3.9+.

## Web UI pages and routes

### Browser routes

| Route | Access | Description |
|---|---|---|
| `/login` | Public | Login form |
| `/inbox` | Authenticated user | Own emails only (AIP data isolation) |
| `/admin` | Admin role | All emails across all users |
| `/audit` | Admin role | Live audit log — all transports |

### Agent routes

| Route | Auth | Description |
|---|---|---|
| `GET /api/emails/mine` | `Bearer <aat>` — `read:own_emails` | JSON API — caller's own emails |
| `GET /api/emails/all` | `Bearer <aat>` — `read:all_emails` + admin | JSON API — all emails |
| `GET /aip-playground-mcp` | None (SSE handshake) | MCP over HTTP/SSE — Claude Desktop / Cursor entry point |
| `POST /aip-playground-mcp/messages?sessionId=<id>` | Per-tool AAT | MCP JSON-RPC messages |

## Authentication paths

`webapp.py` has three completely separate authentication paths:

### 1. Browser (human) — session cookie
1. `POST /login` → `authenticate_user()` validates credentials → stores `{user_id, role}` in a signed session cookie. No AAT issued.
2. Every browser route reads `session["user_id"]`, checks `user["role"]` directly.
3. Audit records: `actor = user_id`, `transport = "http"`.

### 2. JSON API (agent) — Bearer token
1. Every `/api/*` route reads `Authorization: Bearer <aat>` → `validate_aat()` → `check_capability()` / `check_role()`.
2. Audit records: `actor = agent_id`, `transport = "http"`.

### 3. MCP over HTTP/SSE (agent) — per-tool AAT
1. Client opens `GET /aip-playground-mcp` → receives SSE stream with message endpoint URL.
2. Client POSTs JSON-RPC to `/aip-playground-mcp/messages?sessionId=<id>`.
3. `authenticate` tool: validates credentials, calls `issue_aat()` (Layer 1), returns signed AAT.
4. `list_my_emails` / `list_all_emails`: validate AAT (Layer 2), enforce capability/role.
5. Audit records: `actor = agent_id`, `transport = "mcp-http"`.

## AAT claims

An AAT is issued by an **agent** or registry to authenticate an agent (not a human) — for example, by `main.py` or a custom agent calling `issue_aat()`.  Human browser logins do **not** produce an AAT.

```json
{
  "iss": "aip-sample-issuer",
  "sub": "alice",
  "iat": 1736500000,
  "exp": 1736503600,
  "jti": "a3f8d1c2",
  "agent_id": "email-assistant-v1",
  "role": "user",
  "capabilities": ["read:own_emails"]
}
```

| Claim | Meaning |
|---|---|
| `iss` | Token Issuer (AIP Registry) |
| `sub` | Human user the agent acts on behalf of |
| `agent_id` | Unique identifier of the **agent** (not the human) |
| `role` | Permission level (`user` / `admin`) |
| `capabilities` | Explicit list of permitted operations |
| `jti` | Token ID — used for revocation checks |

## Audit log sample

Browser and API events share the same `audit.jsonl` file, distinguished by `transport` and `actor`:

```jsonl
{"ts":"...","event":"login",     "actor":"alice",            "action":"login",           "outcome":"allow","transport":"http"}
{"ts":"...","event":"page_view", "actor":"alice",            "action":"inbox",           "outcome":"allow","transport":"http"}
{"ts":"...","event":"page_view", "actor":"alice",            "action":"admin",           "outcome":"deny", "transport":"http"}
{"ts":"...","event":"tool_call", "actor":"claude-agent",     "action":"authenticate",    "outcome":"allow","transport":"mcp-http"}
{"ts":"...","event":"tool_call", "actor":"claude-agent",     "action":"list_my_emails",  "outcome":"allow","transport":"mcp-http"}
{"ts":"...","event":"tool_call", "actor":"email-assistant",  "action":"list_my_emails",  "outcome":"allow","transport":"mcp"}
{"ts":"...","event":"api_call",  "actor":"email-assistant",  "action":"api:list_my_emails","outcome":"allow","transport":"http"}
```

| `transport` | Source |
|---|---|
| `"http"` | Browser page views and direct `/api/*` calls |
| `"mcp"` | stdio MCP server (`mcp_server.py`) |
| `"mcp-http"` | MCP over HTTP/SSE (`/aip-playground-mcp`) |

TODO: Handl the point when agents are doing browser based actions.

## MCP server → web server: AAT vs session

This is a critical distinction — the browser session and the agent AAT are **completely separate**.

### Humans do not have AATs. Agents do not have sessions.

| | Browser (human) | JSON API agent | MCP-over-HTTP agent |
|---|---|---|---|
| Entry point | `/login` form | `/api/*` | `GET /aip-playground-mcp` |
| Identity carrier | Session cookie `{user_id, role}` | `Authorization: Bearer <aat>` | AAT via `authenticate` tool |
| `issue_aat()` called? | Never | By the agent externally | By the `authenticate` tool (Layer 1) |
| Validated by | `_session_user()` | `_bearer_claims()` + `validate_aat()` | `validate_aat()` per tool call |
| Audit transport | `"http"` | `"http"` | `"mcp-http"` |

```
Browser        → session cookie          → _session_user()           → user dict
stdio agent    → aat= tool argument      → mcp_server.py             → validate_aat()
API client     → Authorization: Bearer   → _bearer_claims()          → validate_aat()
MCP-HTTP agent → authenticate tool       → issue_aat() [Layer 1]
               → list_* tools + aat      → validate_aat() [Layer 2]
```

### The MCP server calls the web server

`mcp_server.py` has two operating modes. In **microservice mode** (`WEBAPP_BASE_URL` set), it forwards the agent's AAT to the web server's JSON API as a Bearer token and does not read `data.py` directly. In **standalone mode** (no `WEBAPP_BASE_URL`), it imports `data.py` directly — this is what `main.py` uses with no web server running.

```
AI Agent
  │  tools/call + aat
  ▼
mcp_server.py
  ├── validate_aat()          local fast-fail before any HTTP
  ├── check_capability()
  └── GET /api/emails/mine    HTTP, Authorization: Bearer <aat>
           │
           ▼
        webapp.py
           ├── validate_aat() re-enforces independently
           ├── check_capability()
           └── data.py        single authoritative data source
```

Enforcement runs **twice** — locally in the MCP server (fast-fail) and again in the web server (authoritative). The web server trusts nothing regardless of caller. This is the correct pattern for a microservice backend: one data source, multiple frontends, each enforcing the same policy independently.

### JSON API routes

| Route | Header | Required capability | Role | Returns |
|---|---|---|---|---|
| `GET /api/emails/mine` | `Authorization: Bearer <aat>` | `read:own_emails` | any | Caller's own emails |
| `GET /api/emails/all` | `Authorization: Bearer <aat>` | `read:all_emails` | `admin` | All emails |

Responses: `401` (missing/invalid token), `403` (insufficient capability or role), `200` JSON on success.

### Running both together

```bash
# Terminal 1 — web server
source .venv/bin/activate
uvicorn webapp:app --port 8000

# Terminal 2 — MCP server, pointed at the web server
export WEBAPP_BASE_URL=http://localhost:8000
python3 mcp_server.py
```

Set `WEBAPP_BASE_URL` to point the MCP server at any deployed instance of `webapp.py`.

---

## Relation to the AIP specification

This sample is a Python illustration of the concepts in
[aip-v1alpha1.md](https://github.com/openagentidentityprotocol/agentidentityprotocol/blob/main/spec/aip-v1alpha1.md).

The Go reference proxy [aip-go](https://github.com/openagentidentityprotocol/aip-go)
wraps *any* MCP server and handles Layer 2 enforcement externally.
This demo bundles both layers into a single Python process to keep
the example self-contained and easy to follow.

See **[implementation.md](implementation.md)** for a step-by-step guide to connecting
aip-go to this playground's `mcp_server_plain.py` from Cursor, Claude Desktop, or the
command line.
