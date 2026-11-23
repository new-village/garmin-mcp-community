"""MCP Streamable HTTP integration tests through TestClient (all mocked).

These tests were added to isolate the live-PoC failure in which every
tools/call returned::

    [401] Authorization header must be 'Bearer <garmin-token>'.

That exact wording is produced by ``auth.extract_bearer_token`` only when
an Authorization header *is* present but its scheme is not ``bearer``
(or the credential after it is empty). A missing header produces a
different message, and a well-formed ``Bearer`` header with a garbage
credential produces yet another one ("not valid Garmin token data").

The tests below drive the real ASGI stack (RefreshedTokenMiddleware +
FastMCP Streamable HTTP, stateless) exactly as a client does —
initialize -> notifications/initialized -> tools/call — with
``GarminService`` monkeypatched out, proving locally that:

* with ``Authorization: Bearer <base64-token>`` the header *does* reach
  ``server._execute`` via ``get_http_headers(include_all=True)`` and the
  tool succeeds; and
* sending the same token *without* the ``Bearer `` prefix reproduces
  the exact live 401 wording.

So the code path is sound when the header arrives as sent; the live
failure means the header *as seen by the server* differed from what the
client intended (scheme not ``bearer`` / empty credential) — e.g. the
prefix missing or not separated by a space on the tools/call requests,
or the header being rewritten in transit — not a token-content problem
and not the header being absent entirely.

Note on RefreshedTokenMiddleware timing (verified locally while writing
these tests): on the Streamable HTTP (SSE) transport, FastMCP sends
``http.response.start`` *before* the tool executes, so by the time a
refreshed token is noted the response headers have already been sent
and ``X-Garmin-Token`` cannot be attached to that response. The
middleware header logic (including ``Cache-Control: no-store``) is
therefore tested directly against the middleware below, with a dummy
ASGI app that notes a refreshed token before ``http.response.start`` —
the ordering the middleware was written for.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from starlette.testclient import TestClient

from garmin_mcp_community import auth as auth_mod
from garmin_mcp_community import server

from conftest import TOKEN

TOKEN_B64 = base64.b64encode(TOKEN.encode()).decode()
REFRESHED_JSON = (
    '{"di_token": "refreshed", "di_refresh_token": "refreshed", '
    '"di_client_id": "fake"}'
)
BEARER_401_TEXT = (
    "[401] Authorization header must be 'Bearer <garmin-token>'. "
    "Re-authenticate via /auth if you do not have a token."
)

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


class _FakeService:
    """Stand-in for GarminService: records the token it was built with."""

    seen_tokens: list[str] = []
    refreshed_value: str | None = None

    def __init__(self, token: str) -> None:
        type(self).seen_tokens.append(token)
        self._token = token

    def get_user_profile(self) -> dict:
        return {"display_name": "test-user", "timezone": "Asia/Tokyo"}

    def refreshed_token(self) -> str | None:
        return type(self).refreshed_value


@pytest.fixture
def fake_service(monkeypatch):
    _FakeService.seen_tokens = []
    _FakeService.refreshed_value = None
    monkeypatch.setattr(server, "GarminService", _FakeService)
    return _FakeService


@pytest.fixture
def mcp_client(fake_service):
    with TestClient(server.build_app()) as client:
        yield client


def _parse_sse_json(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"no SSE data line in response: {text!r}")


def _run_mcp_flow(client: TestClient, authorization: str) -> dict:
    """initialize -> notifications/initialized -> tools/call get_user_profile.

    Returns the parsed JSON-RPC payload of the tools/call response.
    """
    headers = {**_MCP_HEADERS, "Authorization": authorization}

    resp = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        },
    )
    assert resp.status_code == 200
    session_id = resp.headers.get("mcp-session-id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    resp = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert resp.status_code in (200, 202)

    resp = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_user_profile", "arguments": {}},
        },
    )
    assert resp.status_code == 200
    return _parse_sse_json(resp.text)


# ---------------------------------------------------------------------------
# 401 isolation: the Authorization header through the real HTTP stack


def test_tools_call_succeeds_with_bearer_base64_token(mcp_client, fake_service):
    payload = _run_mcp_flow(mcp_client, f"Bearer {TOKEN_B64}")
    result = payload["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["display_name"] == "test-user"
    # The service was built from the *normalised* (base64-decoded) token,
    # proving Authorization reached _execute via get_http_headers.
    assert fake_service.seen_tokens == [TOKEN]


def test_tools_call_without_bearer_prefix_is_exact_live_401(
    mcp_client, fake_service
):
    # Same token, sent bare (no "Bearer " prefix): the scheme check in
    # extract_bearer_token fails before the token is ever looked at.
    payload = _run_mcp_flow(mcp_client, TOKEN_B64)
    result = payload["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == BEARER_401_TEXT
    # Auth failed before any Garmin service was constructed.
    assert fake_service.seen_tokens == []


# ---------------------------------------------------------------------------
# RefreshedTokenMiddleware: X-Garmin-Token + Cache-Control: no-store


def _run_middleware(app) -> list[dict]:
    """Drive RefreshedTokenMiddleware around a dummy ASGI app, capturing sends."""
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    asyncio.run(server.RefreshedTokenMiddleware(app)(scope, receive, send))
    return sent


def _response_start(sent: list[dict]) -> dict:
    return next(m for m in sent if m["type"] == "http.response.start")


def _headers_dict(message: dict) -> dict[str, str]:
    return {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in message.get("headers", [])
    }


def test_middleware_refreshed_token_sets_x_garmin_token_and_no_store():
    async def dummy_app(scope, receive, send):
        # Simulate a tool call that refreshed the token *before* the
        # response starts (the ordering the middleware is written for).
        auth_mod.note_refreshed_token(TOKEN, REFRESHED_JSON)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    headers = _headers_dict(_response_start(_run_middleware(dummy_app)))
    assert headers.get("x-garmin-token") == REFRESHED_JSON
    assert headers.get("cache-control") == "no-store"


def test_middleware_without_refresh_adds_neither_header():
    async def dummy_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    headers = _headers_dict(_response_start(_run_middleware(dummy_app)))
    assert "x-garmin-token" not in headers
    assert headers.get("cache-control") != "no-store"
