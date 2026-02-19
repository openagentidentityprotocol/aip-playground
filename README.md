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
| **Transport-agnosticism** — AAT works over MCP stdio *and* HTTP Bearer token | `auth.py` |
| **MCP stdio transport** — agent interface speaks JSON-RPC 2.0 | `mcp_server.py` |
| **Separate auth paths** — human browser session vs. agent Bearer token are distinct | `webapp.py` |

## Architecture

```
                        auth.py + data.py
                       (shared, unchanged)
                        ┌──────┴──────┐
                        │             │
              mcp_server.py       webapp.py
           (AIP Layer 2 for    ┌── Browser routes ──────────────────┐
            MCP stdio agents)  │  plain session {user_id, role}     │
                        │      │  no AAT — humans are not agents     │
                   main.py     ├── JSON API routes (/api/*) ─────────┤
                (CLI demo)     │  Authorization: Bearer <aat>        │
                               │  AIP Layer 2 enforcement            │
                               └─────────────────────────────────────┘
```

**Key insight:** `auth.py` is transport-agnostic. `validate_aat()` /
`check_capability()` / `check_role()` are called by `mcp_server.py` (AI agents
over stdio) and by `webapp.py`'s `/api/*` routes (agents/MCP server over HTTP).
Human browser sessions use a plain `{user_id, role}` record — no AAT involved.

## Files

```
.
├── auth.py          AIP Layer 1: AAT issuance & Layer 2: validation helpers
├── data.py          In-memory email store and user registry
├── mcp_server.py    AIP-aware proxy wrapped MCP server (stdio JSON-RPC 2.0 transport)
├── main.py          CLI demo — runs all four scenarios
├── webapp.py        FastAPI web UI — browser demo
├── requirements.txt Web UI dependencies
└── audit.jsonl      Created at runtime; one JSON line per tool call
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

## Web UI pages

| Route | Access | Description |
|---|---|---|
| `/login` | Public | Login form |
| `/inbox` | Any authenticated user | Own emails only (AIP data isolation) |
| `/admin` | Admin role only | All emails across all users |
| `/audit` | Admin role only | Live audit log — shows both web and CLI/MCP events |

## Authentication paths

`webapp.py` has two completely separate authentication paths that must not be confused:

### Browser (human) — session cookie
1. **Login** (`POST /login`) → `authenticate_user()` validates credentials → stores `{user_id, role}` in a signed Starlette session cookie.  No AAT is issued — humans are not agents.
2. **Every browser route** (`/inbox`, `/admin`, `/audit`) → reads `session["user_id"]`, looks up the user record, checks `user["role"]` directly → allow or redirect to `/login` with an error.
3. **Every decision** → written to `audit.jsonl` with `actor = user_id`.

### API (agent / MCP server) — Bearer token
1. **Every `/api/*` route** → reads `Authorization: Bearer <aat>` header → `validate_aat(aat)` (Layer 2) → `check_capability()` / `check_role()` → allow or `401`/`403`.
2. **Every decision** → written to `audit.jsonl` with `actor = agent_id` from the AAT claims, tagged `"transport": "mcp"` (when called by `mcp_server.py`) or `"http"` (when called directly).

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
{"ts":"2025-01-14T12:00:00Z","event":"login","actor":"alice","action":"login","outcome":"allow","detail":"role=user","transport":"http"}
{"ts":"2025-01-14T12:00:01Z","event":"page_view","actor":"alice","action":"inbox","outcome":"allow","detail":"emails=2","transport":"http"}
{"ts":"2025-01-14T12:00:02Z","event":"page_view","actor":"alice","action":"admin","outcome":"deny","detail":"insufficient role","transport":"http"}
{"ts":"2025-01-14T12:00:03Z","event":"tool_call","actor":"email-assistant-v1","action":"list_my_emails","outcome":"allow","detail":"user=alice","transport":"mcp"}
{"ts":"2025-01-14T12:00:04Z","event":"api_call","actor":"email-assistant-v1","action":"api:list_my_emails","outcome":"allow","detail":"user=alice","transport":"http"}
```

- `actor` is the **user ID** for browser events and the **agent ID** for API/MCP events.
- `transport: "http"` covers both browser page views and direct API calls; `transport: "mcp"` is the MCP server path.

TODO: Handl the point when agents are doing browser based actions.

## MCP server → web server: AAT vs session

This is a critical distinction — the browser session and the agent AAT are **completely separate**.

### Humans do not have AATs. Agents do not have sessions.

| | Browser (human) | Agent / MCP server |
|---|---|---|
| Identity carrier | Starlette session cookie `{user_id, role}` | `Authorization: Bearer <aat>` |
| Token issued by | Nothing — session stores plain user record | `issue_aat()` in the agent |
| Validated by | `_session_user()` — looks up user in `USERS` dict | `validate_aat()` — verifies HMAC signature |
| Route prefix | `/`, `/inbox`, `/admin`, `/audit` | `/api/*` |

`issue_aat()` is only ever called by agents obtaining their own identity token. `webapp.py` never calls `issue_aat()` — human login just stores the authenticated user's ID in the session.

```
Browser    → encrypted session cookie  → webapp.py _session_user()  → user dict
MCP agent  → aat= tool argument        → mcp_server.py validate_aat() → claims
API client → Authorization: Bearer     → webapp.py _bearer_claims()  → validate_aat() → claims
```

### The MCP server calls the web server

`mcp_server.py` no longer imports `data.py` directly. It forwards the agent's AAT to the web server's JSON API as a Bearer token, and the web server returns the data.

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

The Go reference proxy at
[`implementations/go-proxy`](https://github.com/openagentidentityprotocol/agentidentityprotocol/tree/main/implementations/go-proxy)
wraps *any* MCP server and handles Layer 2 enforcement externally.
This demo bundles both layers into a single Python process to keep
the example self-contained and easy to follow.
