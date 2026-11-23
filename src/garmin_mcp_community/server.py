"""FastMCP server: Streamable HTTP, stateless, Cloud Run ready.

Auth model (SPEC §5.1): the server holds no sessions and no health data.
Every tool call must carry the client's Garmin token as
``Authorization: Bearer <token>``; a fresh garminconnect client is created
for that request and discarded afterwards. If Garmin refreshed the token
during the request, the new token is returned in the ``X-Garmin-Token``
response header (see RefreshedTokenMiddleware) so the client can replace
its stored copy. Tokens and health data are never logged.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from . import auth as auth_mod
from .errors import GarminMcpError
from .service import GarminService

mcp = FastMCP(
    name="garmin-mcp-community",
    instructions=(
        "Read-only access to the caller's own Garmin Connect health and "
        "training data. Every call requires the caller's Garmin token in "
        "the Authorization: Bearer header. All dates are interpreted in "
        "the user's Garmin profile timezone, and every response states "
        "its date, timezone and units. Data is for health-management "
        "reference, not medical diagnosis."
    ),
)


async def _execute(call: Callable[[GarminService], dict[str, Any]]) -> dict[str, Any]:
    """Authenticate from the request header and run one tool, statelessly."""
    headers = get_http_headers(include_all=True)
    try:
        token = auth_mod.extract_bearer_token(headers)
    except GarminMcpError as exc:
        raise ToolError(exc.render()) from exc

    def work() -> tuple[dict[str, Any], str | None]:
        service = GarminService(token)
        result = call(service)
        return result, service.refreshed_token()

    try:
        result, refreshed = await asyncio.to_thread(work)
    except GarminMcpError as exc:
        raise ToolError(exc.render()) from exc
    auth_mod.note_refreshed_token(token, refreshed)
    return result


# ---------------------------------------------------------------------------
# v1 read-only tools (SPEC §5.5) — no write tools exist in this server.


@mcp.tool
async def get_user_profile() -> dict[str, Any]:
    """Return the Garmin user's display name and profile timezone."""
    return await _execute(lambda svc: svc.get_user_profile())


@mcp.tool
async def get_daily_summary(date: str) -> dict[str, Any]:
    """Daily summary for YYYY-MM-DD: steps, calories, activity time,
    intensity minutes and resting heart rate."""
    return await _execute(lambda svc: svc.get_daily_summary(date))


@mcp.tool
async def get_sleep(date: str) -> dict[str, Any]:
    """Sleep for YYYY-MM-DD: duration, Garmin sleep score and stage breakdown."""
    return await _execute(lambda svc: svc.get_sleep(date))


@mcp.tool
async def get_stress(date: str) -> dict[str, Any]:
    """Stress for YYYY-MM-DD: average/max level (0-100) and a sampled series."""
    return await _execute(lambda svc: svc.get_stress(date))


@mcp.tool
async def get_body_battery(date: str) -> dict[str, Any]:
    """Body Battery for YYYY-MM-DD: start/end, morning peak, high/low, series."""
    return await _execute(lambda svc: svc.get_body_battery(date))


@mcp.tool
async def get_heart_rate(date: str) -> dict[str, Any]:
    """Heart rate for YYYY-MM-DD: resting/min/max (bpm) and a sampled series."""
    return await _execute(lambda svc: svc.get_heart_rate(date))


@mcp.tool
async def get_activities(start: str, end: str) -> dict[str, Any]:
    """Activities between YYYY-MM-DD and YYYY-MM-DD (inclusive, max 100)."""
    return await _execute(lambda svc: svc.get_activities(start, end))


@mcp.tool
async def get_activity_detail(activity_id: str) -> dict[str, Any]:
    """Detail for one activity by its Garmin activity id."""
    return await _execute(lambda svc: svc.get_activity_detail(activity_id))


@mcp.tool
async def get_trends(days: int, end_date: str | None = None) -> dict[str, Any]:
    """Trends over the last N days (1-90): steps, resting heart rate,
    stress, sleep and Body Battery averages with direction of change."""
    return await _execute(lambda svc: svc.get_trends(days, end_date))


@mcp.tool
async def get_recovery_status(date: str) -> dict[str, Any]:
    """Composite recovery summary for YYYY-MM-DD from sleep, Body Battery,
    stress and resting heart rate vs its 7-day baseline. Heuristic only,
    not a medical assessment."""
    return await _execute(lambda svc: svc.get_recovery_status(date))


# ---------------------------------------------------------------------------
# /auth stub and health check


_AUTH_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>garmin-mcp-community — auth</title></head>
<body>
<h1>Garmin authentication (STUB — not yet implemented)</h1>
<p><strong>Status:</strong> placeholder. The interactive Garmin login is
implemented in a later pass, after a proof-of-concept with a real account.</p>
<h2>Intended flow</h2>
<ol>
  <li>You open this page and sign in to Garmin Connect yourself
      (including MFA, if enabled on your account).</li>
  <li>On success, a Garmin session token is issued <em>once</em> and shown
      to you / handed to your AI client. <strong>This server keeps
      nothing</strong>: no token, no session, no health data is stored
      or logged server-side.</li>
  <li>Your AI client stores the token and sends it on every MCP request
      as <code>Authorization: Bearer &lt;token&gt;</code>. The server uses
      it only within that single request and discards it.</li>
  <li>If the token has expired, the server refreshes it during a request
      and returns the new token in the <code>X-Garmin-Token</code>
      response header; if refresh fails, re-authenticate here.</li>
</ol>
<p>This is an unofficial, community-maintained project and is not
affiliated with Garmin.</p>
</body>
</html>
"""


@mcp.custom_route("/auth", methods=["GET"])
async def auth_page(request: Request) -> HTMLResponse:
    """STUB: explains the intended token issuance flow (SPEC §5.1)."""
    return HTMLResponse(_AUTH_HTML)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# X-Garmin-Token middleware


class RefreshedTokenMiddleware:
    """Emit X-Garmin-Token when a request refreshed the caller's token.

    NOTE (limitation): this works because FastMCP executes tools within
    the HTTP request's context, and the per-request holder dict is shared
    by reference through the contextvar. If a future FastMCP version runs
    tools in a fully isolated context, the header silently stops being
    emitted and clients fall back to re-authenticating via /auth on 401.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        holder: dict[str, str] = {}
        ctx = auth_mod.refreshed_token_holder.set(holder)

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start" and holder.get("token"):
                headers = MutableHeaders(scope=message)
                headers["X-Garmin-Token"] = holder["token"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            auth_mod.refreshed_token_holder.reset(ctx)


def build_app() -> Any:
    """Build the ASGI app: FastMCP Streamable HTTP + token middleware."""
    app = mcp.http_app(transport="streamable-http", stateless_http=True)
    return RefreshedTokenMiddleware(app)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(build_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
