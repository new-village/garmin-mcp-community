import base64
import json

import pytest

from garmin_mcp_community import auth
from garmin_mcp_community.errors import AuthError

from conftest import TOKEN


def test_missing_header_is_401():
    with pytest.raises(AuthError) as excinfo:
        auth.extract_bearer_token({})
    assert excinfo.value.status == 401


def test_non_bearer_scheme_is_401():
    with pytest.raises(AuthError) as excinfo:
        auth.extract_bearer_token({"authorization": "Basic abc"})
    assert excinfo.value.status == 401


def test_garbage_token_is_401():
    with pytest.raises(AuthError) as excinfo:
        auth.extract_bearer_token({"authorization": "Bearer not-a-token"})
    assert excinfo.value.status == 401


def test_raw_json_token_accepted():
    token = auth.extract_bearer_token(
        {"Authorization": f"Bearer {TOKEN}"}
    )
    assert json.loads(token)["di_token"] == "fake"


def test_base64_json_token_accepted():
    encoded = base64.b64encode(TOKEN.encode()).decode()
    token = auth.extract_bearer_token({"authorization": f"Bearer {encoded}"})
    assert json.loads(token)["di_token"] == "fake"


def test_refreshed_token_holder():
    holder: dict[str, str] = {}
    ctx = auth.refreshed_token_holder.set(holder)
    try:
        auth.note_refreshed_token(TOKEN, '{"di_token": "new"}')
        assert holder["token"] == '{"di_token": "new"}'
        # Unchanged token is not noted.
        holder.clear()
        auth.note_refreshed_token(TOKEN, TOKEN)
        assert holder == {}
    finally:
        auth.refreshed_token_holder.reset(ctx)
