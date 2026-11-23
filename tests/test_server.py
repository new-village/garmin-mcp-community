"""Server-level tests: the missing/invalid token path and tool inventory."""

import asyncio

import pytest
from fastmcp.exceptions import ToolError

from garmin_mcp_community import server


def _run(coro):
    return asyncio.run(coro)


def test_missing_token_is_401_tool_error(monkeypatch):
    monkeypatch.setattr(
        server, "get_http_headers", lambda include_all=False: {}
    )
    with pytest.raises(ToolError) as excinfo:
        _run(server._execute(lambda svc: svc.get_user_profile()))
    assert "[401]" in str(excinfo.value)


def test_invalid_token_is_401_tool_error(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_http_headers",
        lambda include_all=False: {"authorization": "Bearer garbage"},
    )
    with pytest.raises(ToolError) as excinfo:
        _run(server._execute(lambda svc: svc.get_user_profile()))
    assert "[401]" in str(excinfo.value)


def test_tool_inventory_is_exactly_v1_read_only():
    tools = _run(server.mcp.list_tools())
    names = sorted(t.name for t in tools)
    assert names == sorted(
        [
            "get_user_profile",
            "get_daily_summary",
            "get_sleep",
            "get_stress",
            "get_body_battery",
            "get_heart_rate",
            "get_activities",
            "get_activity_detail",
            "get_trends",
            "get_recovery_status",
        ]
    )
