"""
AIP Sample Application — FastAPI Web Server

Two distinct authentication paths, correctly separated:

  Browser (human)
  ───────────────
  Login stores a plain user session (user_id + role).
  Browser routes read the session directly — no AAT involved.
  The AAT is an agent identity token; humans do not have one.

  API (agent / MCP server)
  ────────────────────────
  Every /api/* route requires  Authorization: Bearer <aat>.
  The AAT is validated via AIP Layer 2 enforcement (validate_aat,
  check_capability, check_role) before any data is returned.
  No session cookie is read or written for API routes.

Static assets and templates are served from:
  static/    CSS and other static files
  templates/ Jinja2 HTML templates (base.html + one per page)

Run:
    source .venv/bin/activate
    uvicorn webapp:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import uuid
from typing import Optional

from fastapi import FastAPI, Form, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import AuthError, check_capability, check_role, issue_aat, validate_aat
from data import USERS, authenticate_user, get_all_emails, get_emails_for_user

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="AIP Email Demo", docs_url=None, redoc_url=None)

SESSION_SECRET = os.environ.get("AIP_SESSION_SECRET", "dev-session-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=3600)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

AUDIT_LOG_PATH = "audit.jsonl"

# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _audit(event: str, actor: str, action: str, outcome: str, detail: str = "", transport: str = "http") -> None:
    record = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "event": event,
        "actor": actor,
        "action": action,
        "outcome": outcome,
        "detail": detail,
        "transport": transport,
    }
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Human session helpers
#
# The session stores only {user_id, role} — a plain server-side record of
# who logged in.  No AAT is issued, stored, or read here.
# ---------------------------------------------------------------------------


def _session_user(request: Request) -> Optional[dict]:
    """Return the USERS record for the logged-in human, or None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return USERS.get(user_id)


# ---------------------------------------------------------------------------
# Browser routes — human authentication via session
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root(request: Request):
    if _session_user(request):
        return RedirectResponse("/inbox", status_code=status.HTTP_302_FOUND)
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)


# --- Login ---

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, error: str = ""):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "user": None, "error": error},
    )


@app.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    user = authenticate_user(username, password)
    if not user:
        return RedirectResponse(
            "/login?error=Invalid+username+or+password",
            status_code=status.HTTP_302_FOUND,
        )

    # Store only the minimal user identity in the server-side session.
    # No AAT is issued — this is a human, not an agent.
    request.session["user_id"] = user["id"]
    request.session["role"] = user["role"]

    _audit("login", actor=user["id"], action="login", outcome="allow",
           detail=f"role={user['role']}")
    return RedirectResponse("/inbox", status_code=status.HTTP_302_FOUND)


# --- Logout ---

@app.get("/logout")
async def logout(request: Request):
    user_id = request.session.get("user_id", "<unknown>")
    _audit("logout", actor=user_id, action="logout", outcome="allow")
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)


# --- Inbox (user view) ---

@app.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request):
    user = _session_user(request)
    if not user:
        return RedirectResponse("/login?error=Session+expired",
                                status_code=status.HTTP_302_FOUND)

    emails = get_emails_for_user(user["email"])
    _audit("page_view", actor=user["id"], action="inbox", outcome="allow",
           detail=f"emails={len(emails)}")

    return templates.TemplateResponse(
        "inbox.html",
        {
            "request": request,
            "user": user,
            "emails": emails,
        },
    )


# --- Admin: all emails ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_view(request: Request):
    user = _session_user(request)
    if not user:
        return RedirectResponse("/login?error=Session+expired",
                                status_code=status.HTTP_302_FOUND)

    if user["role"] != "admin":
        _audit("page_view", actor=user["id"], action="admin", outcome="deny",
               detail="insufficient role")
        return RedirectResponse("/login?error=Access+denied:+admin+role+required",
                                status_code=status.HTTP_302_FOUND)

    emails = get_all_emails()
    _audit("page_view", actor=user["id"], action="admin", outcome="allow",
           detail=f"emails={len(emails)}")

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": user,
            "emails": emails,
        },
    )


# --- Audit log ---

@app.get("/audit", response_class=HTMLResponse)
async def audit_view(request: Request):
    user = _session_user(request)
    if not user:
        return RedirectResponse("/login?error=Session+expired",
                                status_code=status.HTTP_302_FOUND)

    if user["role"] != "admin":
        return RedirectResponse("/login?error=Access+denied:+admin+role+required",
                                status_code=status.HTTP_302_FOUND)

    records: list = []
    try:
        with open(AUDIT_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except FileNotFoundError:
        pass

    return templates.TemplateResponse(
        "audit.html",
        {
            "request": request,
            "user": user,
            "records": list(reversed(records)),
        },
    )


# ---------------------------------------------------------------------------
# JSON API routes — agent / MCP server authentication via AAT Bearer token
#
# No session cookie is read here.  The caller must present a valid AAT in
# the Authorization header.  AIP Layer 2 enforcement (validate_aat,
# check_capability, check_role) gates every request.
#
# Authorization: Bearer <aat>
# ---------------------------------------------------------------------------


def _bearer_claims(request: Request) -> Optional[dict]:
    """Extract and validate the AAT from an Authorization: Bearer header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    aat = auth[len("Bearer "):]
    try:
        return validate_aat(aat)
    except AuthError:
        return None


@app.get("/api/emails/mine")
async def api_list_my_emails(request: Request):
    """
    Return the agent's own emails as JSON.

    Requires:  Authorization: Bearer <aat>  with capability read:own_emails
    """
    claims = _bearer_claims(request)
    if not claims:
        return JSONResponse(
            {"error": "missing or invalid AAT"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        check_capability(claims, "read:own_emails")
    except AuthError as exc:
        _audit("api_call", actor=claims.get("agent_id", "?"),
               action="api:list_my_emails", outcome="deny", detail=str(exc))
        return JSONResponse({"error": str(exc)}, status_code=status.HTTP_403_FORBIDDEN)

    user_id = claims.get("sub")
    user = USERS.get(user_id)
    if not user:
        return JSONResponse({"error": "unknown user in token"},
                            status_code=status.HTTP_403_FORBIDDEN)

    emails = get_emails_for_user(user["email"])
    _audit("api_call", actor=claims.get("agent_id", "?"),
           action="api:list_my_emails", outcome="allow", detail=f"user={user_id}")
    return JSONResponse({"emails": emails, "count": len(emails)})


@app.get("/api/emails/all")
async def api_list_all_emails(request: Request):
    """
    Return all emails in the system as JSON.

    Requires:  Authorization: Bearer <aat>  with capability read:all_emails and role admin
    """
    claims = _bearer_claims(request)
    if not claims:
        return JSONResponse(
            {"error": "missing or invalid AAT"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        check_capability(claims, "read:all_emails")
        check_role(claims, "admin")
    except AuthError as exc:
        _audit("api_call", actor=claims.get("agent_id", "?"),
               action="api:list_all_emails", outcome="deny", detail=str(exc))
        return JSONResponse({"error": str(exc)}, status_code=status.HTTP_403_FORBIDDEN)

    emails = get_all_emails()
    _audit("api_call", actor=claims.get("agent_id", "?"),
           action="api:list_all_emails", outcome="allow", detail="role=admin")
    return JSONResponse({"emails": emails, "count": len(emails)})


# ---------------------------------------------------------------------------
# MCP over HTTP — SSE transport
#
# Exposes the same AIP-enforced tools as mcp_server.py but over HTTP/SSE
# so Claude Desktop, Cursor, and other MCP clients can connect via URL
# instead of spawning a subprocess.
#
# Connection flow:
#   1. Client opens GET /mcp  → receives SSE stream
#   2. Server sends: event: endpoint / data: /mcp/messages?sessionId=<id>
#   3. Client POSTs JSON-RPC messages to /mcp/messages?sessionId=<id>
#   4. Server pushes JSON-RPC responses back through the SSE stream
#
# Tools:
#   authenticate      — AIP Layer 1: validates credentials, issues a signed AAT
#   list_my_emails    — AIP Layer 2: validates AAT, enforces read:own_emails
#   list_all_emails   — AIP Layer 2: validates AAT, enforces read:all_emails + admin
#
# Config (Claude Desktop / Cursor):
#   "aip-email": { "url": "http://localhost:8000/aip-playground-mcp" }
# ---------------------------------------------------------------------------

_mcp_sessions: dict = {}  # session_id -> asyncio.Queue

MCP_TOOLS = [
    {
        "name": "authenticate",
        "description": (
            "Authenticate a user and receive an Agent Authentication Token (AAT). "
            "Call this first — the token is required by all other tools. "
            "AIP Layer 1: issues a signed JWT scoped to the user's role and capabilities. "
            "Available users: alice (user), bob (user), admin (admin)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Username"},
                "password": {"type": "string", "description": "Password"},
                "agent_name": {"type": "string", "description": "Label for this agent session (optional)"},
            },
            "required": ["username", "password"],
        },
    },
    {
        "name": "list_my_emails",
        "description": (
            "List emails for the authenticated user. "
            "AIP Layer 2: validates the AAT and enforces read:own_emails capability. "
            "Returns only emails addressed to the token subject."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "aat": {"type": "string", "description": "AAT returned by the authenticate tool"},
            },
            "required": ["aat"],
        },
    },
    {
        "name": "list_all_emails",
        "description": (
            "List every email in the system. Admin role required. "
            "AIP Layer 2: validates the AAT and enforces read:all_emails + admin role."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "aat": {"type": "string", "description": "Admin AAT returned by the authenticate tool"},
            },
            "required": ["aat"],
        },
    },
]


def _mcp_tool_call(tool_name: str, args: dict) -> dict:
    """Dispatch an MCP tool call with full AIP enforcement."""

    if tool_name == "authenticate":
        username = args.get("username", "")
        password = args.get("password", "")
        agent_name = args.get("agent_name", "claude-agent")
        user = authenticate_user(username, password)
        if not user:
            raise ValueError("invalid username or password")
        caps = ["read:own_emails"]
        if user["role"] == "admin":
            caps.append("read:all_emails")
        # AIP Layer 1: issue a signed AAT for the agent
        aat = issue_aat(agent_name, user["id"], user["role"], caps)
        _audit("tool_call", agent_name, "authenticate", "allow",
               f"user={username} role={user['role']}", transport="mcp-http")
        return {
            "aat": aat,
            "role": user["role"],
            "user": user["name"],
            "capabilities": caps,
            "message": (
                f"Authenticated as {user['name']} ({user['role']}). "
                "Pass the 'aat' value to list_my_emails or list_all_emails."
            ),
        }

    if tool_name == "list_my_emails":
        aat = args.get("aat", "")
        # AIP Layer 2: validate token and enforce capability
        claims = validate_aat(aat)
        check_capability(claims, "read:own_emails")
        user_id = claims.get("sub")
        user = USERS.get(user_id, {})
        emails = get_emails_for_user(user.get("email", ""))
        _audit("tool_call", claims.get("agent_id", "?"), "list_my_emails", "allow",
               f"user={user_id}", transport="mcp-http")
        return {"emails": emails, "count": len(emails)}

    if tool_name == "list_all_emails":
        aat = args.get("aat", "")
        # AIP Layer 2: validate token, enforce capability and role
        claims = validate_aat(aat)
        check_capability(claims, "read:all_emails")
        check_role(claims, "admin")
        emails = get_all_emails()
        _audit("tool_call", claims.get("agent_id", "?"), "list_all_emails", "allow",
               "role=admin", transport="mcp-http")
        return {"emails": emails, "count": len(emails)}

    raise ValueError(f"unknown tool '{tool_name}'")


def _mcp_dispatch(body: dict) -> Optional[dict]:
    """JSON-RPC 2.0 dispatcher for MCP requests."""
    rid = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aip-email-mcp", "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return None  # notification — no response needed

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": MCP_TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        try:
            result = _mcp_tool_call(tool_name, tool_args)
            return {
                "jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
            }
        except AuthError as exc:
            _audit("tool_call", "?", tool_name or "?", "deny", str(exc), transport="mcp-http")
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32001, "message": f"AIP policy denied: {exc}"}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": str(exc)}}

    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


@app.get("/aip-playground-mcp")
async def mcp_sse(request: Request):
    """
    SSE endpoint — MCP clients open a persistent connection here.
    The server immediately sends the POST endpoint URL, then streams
    JSON-RPC responses as the client sends requests.
    """
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _mcp_sessions[session_id] = queue

    async def stream():
        # Tell the client where to POST messages for this session
        yield f"event: endpoint\ndata: /aip-playground-mcp/messages?sessionId={session_id}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=2.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keepalive comment
        finally:
            _mcp_sessions.pop(session_id, None)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/aip-playground-mcp/messages")
async def mcp_messages(request: Request, sessionId: str):
    """Receive JSON-RPC messages from the MCP client and push responses to the SSE stream."""
    session_queue = _mcp_sessions.get(sessionId)
    if not session_queue:
        return JSONResponse({"error": "session not found"}, status_code=404)
    body = await request.json()
    response = _mcp_dispatch(body)
    if response is not None:
        await session_queue.put(response)
    return Response(status_code=202)
