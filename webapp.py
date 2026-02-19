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

import datetime
import json
import os
from typing import Optional

from fastapi import FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import AuthError, check_capability, check_role, validate_aat
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


def _audit(event: str, actor: str, action: str, outcome: str, detail: str = "") -> None:
    record = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "event": event,
        "actor": actor,
        "action": action,
        "outcome": outcome,
        "detail": detail,
        "transport": "http",
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
