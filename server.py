"""Cordalux #muse-bridge MCP server.

One codebase, one deployment per bot (claude / grok / chatgpt). Each instance
holds ONLY its own bot's Slack token and exposes bridge tools with the room
protocol baked in:

- All activity stays in the configured channel (default #muse-bridge).
- Replies go in threads and stay short (enforced: 500 chars max).
- @Jon escalation is a first-class tool, not a suggestion.

Env vars:
  SLACK_BOT_TOKEN   Bot User OAuth Token for this instance's Slack app.
                    If unset, tools report "not configured" instead of failing.
  SLACK_CHANNEL_ID  Channel to operate in (default C0C3V0CT4RX = #muse-bridge).
  BOT_NAME          claude | grok | chatgpt (logging / prompt context only).
  MCP_BEARER        Shared secret clients must present as a Bearer token.
  PORT              Listen port (default 8000).
"""

import json
import os
import urllib.request
import urllib.parse

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "C0C3V0CT4RX")
BOT_NAME = os.environ.get("BOT_NAME", "unknown")
MCP_BEARER = os.environ.get("MCP_BEARER", "")
MAX_REPLY_CHARS = 500


def _allowed_hosts() -> list[str]:
    """Hosts the MCP SDK's DNS-rebinding protection will accept.
    The SDK auto-allowlists only localhost, which 421s every request to
    our real Render hostname. Each instance allowlists its own hostname
    (plus an ALLOWED_HOSTS env override and local dev names)."""
    hosts: set[str] = set()
    for h in os.environ.get("ALLOWED_HOSTS", "").split(","):
        h = h.strip()
        if h:
            hosts.add(h)
    if BOT_NAME and BOT_NAME != "unknown":
        hosts.add(f"slack-bridge-{BOT_NAME}.onrender.com")
        hosts.add(f"slack-bridge-{BOT_NAME}.onrender.com:*")
    hosts.update(["127.0.0.1:*", "localhost:*", "[::1]:*"])
    return sorted(hosts)


mcp = FastMCP(
    "cordalux-slack-bridge",
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(),
        allowed_origins=[],
    ),
)


def _slack_token() -> str | None:
    return os.environ.get("SLACK_BOT_TOKEN") or None


def _slack_api(method: str, params: dict) -> dict:
    token = _slack_token()
    if not token:
        return {"ok": False, "error": "not_configured",
                "detail": "This bridge instance has no Slack token yet."}
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"https://slack.com/api/{method}", data=data,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


@mcp.tool()
def read_channel(limit: int = 20) -> str:
    """Read the most recent top-level messages in the bridge channel.
    Use this to catch up on what happened while you were away."""
    if limit < 1 or limit > 50:
        return "limit must be 1-50"
    out = _slack_api("conversations.history",
                     {"channel": SLACK_CHANNEL_ID, "limit": str(limit)})
    if not out.get("ok"):
        return f"Slack error: {out.get('error', out)}"
    lines = []
    for m in out.get("messages", []):
        ts = m.get("ts", "")
        user = m.get("user", m.get("username", "?"))
        text = m.get("text", "")
        replies = m.get("reply_count", 0)
        flag = f" [{replies} replies]" if replies else ""
        lines.append(f"{ts} <{user}>{flag}: {text}")
    return "\n".join(lines) or "(no messages)"


@mcp.tool()
def read_thread(thread_ts: str, limit: int = 20) -> str:
    """Read every reply in one thread of the bridge channel.
    thread_ts is the timestamp shown at the start of a read_channel line."""
    out = _slack_api("conversations.replies",
                     {"channel": SLACK_CHANNEL_ID, "ts": thread_ts,
                      "limit": str(limit)})
    if not out.get("ok"):
        return f"Slack error: {out.get('error', out)}"
    lines = []
    for m in out.get("messages", []):
        ts = m.get("ts", "")
        user = m.get("user", m.get("username", "?"))
        text = m.get("text", "")
        lines.append(f"{ts} <{user}>: {text}")
    return "\n".join(lines) or "(no replies)"


@mcp.tool()
def reply_in_thread(thread_ts: str, text: str) -> str:
    """Post a reply inside an existing thread. THIS IS THE DEFAULT WAY TO
    SPEAK IN THE BRIDGE ROOM. Keep replies short (500 chars max, enforced).
    Continue follow-ups in the same thread. Never start a new top-level
    message to answer something that already has a thread."""
    if len(text) > MAX_REPLY_CHARS:
        return (f"REJECTED: reply is {len(text)} chars, max is "
                f"{MAX_REPLY_CHARS}. Shorten it and try again.")
    out = _slack_api("chat.postMessage",
                     {"channel": SLACK_CHANNEL_ID,
                      "thread_ts": thread_ts, "text": text})
    if not out.get("ok"):
        return f"Slack error: {out.get('error', out)}"
    return f"posted in thread {thread_ts} (ts {out.get('ts')})"


@mcp.tool()
def post_update(text: str) -> str:
    """Post a NEW top-level message in the bridge channel. Use sparingly:
    only for announcements that deserve their own thread (status changes,
    handoffs, decisions). Everything else belongs in reply_in_thread."""
    if len(text) > MAX_REPLY_CHARS:
        return (f"REJECTED: message is {len(text)} chars, max is "
                f"{MAX_REPLY_CHARS}. Shorten it and try again.")
    out = _slack_api("chat.postMessage",
                     {"channel": SLACK_CHANNEL_ID, "text": text})
    if not out.get("ok"):
        return f"Slack error: {out.get('error', out)}"
    return f"posted as new thread {out.get('ts')}"


@mcp.tool()
def ask_jon(question: str) -> str:
    """Ask Jon a question that needs HIS decision. Posts in the channel
    tagging @Jon with a clear yes/no question. Use ONLY when blocked on a
    decision that is genuinely his to make."""
    out = _slack_api("chat.postMessage",
                     {"channel": SLACK_CHANNEL_ID,
                      "text": f"<@U0BMBH2K3C5> {question}"})
    if not out.get("ok"):
        return f"Slack error: {out.get('error', out)}"
    return f"asked Jon (ts {out.get('ts')})"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if not MCP_BEARER:
            return Response("bridge auth not configured", status_code=503)
        token = _extract_token(request)
        if token != MCP_BEARER:
            # Log shape only, never values.
            print(f"auth denied: path={request.url.path} "
                  f"placement={_token_placement(request)} "
                  f"auth_header_present={'authorization' in request.headers}",
                  flush=True)
            return Response("unauthorized", status_code=401)
        return await call_next(request)


def _token_placement(request: Request) -> str:
    if request.headers.get("authorization"):
        return "authorization_header"
    for h in ("x-api-key", "x-mcp-key", "x-auth-token"):
        if request.headers.get(h):
            return h
    for q in ("token", "bearer", "api_key", "access_token"):
        if request.query_params.get(q):
            return f"query:{q}"
    return "none"


def _extract_token(request: Request) -> str:
    """Pull the bearer token from wherever the connector put it.
    Connectors vary: some send `Bearer <t>`, some the raw token, some a
    custom header, some a query param. Accept them all; the token value
    itself is still the secret."""
    auth = request.headers.get("authorization", "").strip()
    if auth:
        scheme, _, cred = auth.partition(" ")
        if cred and scheme.lower() == "bearer":
            return cred.strip().strip("'\"")
        # Raw token without scheme (some connector UIs do this).
        return auth.strip("'\"")
    for h in ("x-api-key", "x-mcp-key", "x-auth-token"):
        v = request.headers.get(h, "").strip()
        if v:
            return v.strip("'\"")
    for q in ("token", "bearer", "api_key", "access_token"):
        v = request.query_params.get(q, "").strip()
        if v:
            return v
    return ""


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "bot": BOT_NAME,
                         "slack_configured": bool(_slack_token()),
                         "channel": SLACK_CHANNEL_ID})


def build_app() -> Starlette:
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware)
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
