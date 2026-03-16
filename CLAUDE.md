# Claude Instructions — AIP Playground

## Documentation rule

**Always update `README.md` and `tutorials.md` when you change code.**

- New route or endpoint → add it to the README route table and tutorials
- Changed URL path → update every occurrence in README, tutorials, and code comments
- New tool or auth path → update the architecture diagram and authentication paths section
- Removed or renamed anything → remove stale references immediately

Do not leave documentation that contradicts the code. If a section no longer matches, rewrite it.

## Project conventions

- Python 3.9+ compatible — use `from __future__ import annotations` and `typing` imports, not `list[str]` / `dict | None` syntax
- Zero external dependencies for `auth.py`, `data.py`, `mcp_server.py`, and `main.py` — stdlib only
- FastAPI + Jinja2 + itsdangerous for `webapp.py` only
- Audit log (`audit.jsonl`) is append-only — never truncate or delete in code
- Human browser session stores `{user_id, role}` only — `issue_aat()` is never called for humans
- MCP SSE endpoint is `/aip-playground-mcp` (not `/mcp`) to avoid collisions
