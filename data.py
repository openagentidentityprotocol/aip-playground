"""
Sample email data store (in-memory, no external dependencies).

In a real deployment this would be a database, but for the AIP demo
the data layer intentionally stays simple so the identity/policy
enforcement logic in mcp_server.py is easy to follow.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# --- Users ---
# Each user has: id, name, email, password (plaintext for demo only)
USERS: Dict[str, dict] = {
    "alice": {
        "id": "alice",
        "name": "Alice Nguyen",
        "email": "alice@example.com",
        "password": "alice123",
        "role": "user",
    },
    "bob": {
        "id": "bob",
        "name": "Bob Smith",
        "email": "bob@example.com",
        "password": "bob123",
        "role": "user",
    },
    "admin": {
        "id": "admin",
        "name": "Admin User",
        "email": "admin@example.com",
        "password": "admin123",
        "role": "admin",
    },
}


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return user record if credentials match, else None."""
    user = USERS.get(username)
    if user and user["password"] == password:
        return user
    return None


# --- Emails ---
EMAILS: List[dict] = [
    {
        "id": "e1",
        "to": "alice@example.com",
        "from": "boss@example.com",
        "subject": "Q1 Report",
        "body": "Please review the Q1 report attached.",
        "timestamp": "2025-01-10T09:00:00",
    },
    {
        "id": "e2",
        "to": "alice@example.com",
        "from": "hr@example.com",
        "subject": "Vacation Policy Update",
        "body": "New vacation policy takes effect next month.",
        "timestamp": "2025-01-12T14:30:00",
    },
    {
        "id": "e3",
        "to": "bob@example.com",
        "from": "boss@example.com",
        "subject": "Project Kickoff",
        "body": "We're kicking off the new project on Monday.",
        "timestamp": "2025-01-11T10:00:00",
    },
    {
        "id": "e4",
        "to": "bob@example.com",
        "from": "support@vendor.com",
        "subject": "Invoice #1042",
        "body": "Your invoice is ready for download.",
        "timestamp": "2025-01-13T08:15:00",
    },
    {
        "id": "e5",
        "to": "admin@example.com",
        "from": "system@example.com",
        "subject": "Security Alert",
        "body": "Unusual login detected from new IP address.",
        "timestamp": "2025-01-14T02:47:00",
    },
]


def get_emails_for_user(user_email: str) -> List[dict]:
    """Return only emails addressed to a specific user."""
    return [e for e in EMAILS if e["to"] == user_email]


def get_all_emails() -> List[dict]:
    """Return all emails (admin view)."""
    return list(EMAILS)
