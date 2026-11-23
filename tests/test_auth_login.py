"""/auth tests: credential-login unit tests and HTTP route tests.

garminconnect is fully faked — no network, no real credentials.
"""

from __future__ import annotations

import base64
import json

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)
from starlette.testclient import TestClient

from garmin_mcp_community import auth as auth_mod
from garmin_mcp_community import server
from garmin_mcp_community.client import login_with_credentials
from garmin_mcp_community.errors import (
    AuthError,
    MfaRequiredError,
    RateLimitError,
)

TOKEN_JSON = '{"di_token": "fake", "di_refresh_token": "fake", "di_client_id": "fake"}'
EMAIL = "user@example.com"
PASSWORD = "s3cret-test-password"


# ---------------------------------------------------------------------------
# login_with_credentials (unit)


class _FakeInnerClient:
    def dumps(self) -> str:
        return TOKEN_JSON


class _FakeGarminOk:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.client = _FakeInnerClient()

    def login(self):
        return None, None


class _FakeGarminMfa:
    def __init__(self, **kwargs) -> None:
        self.client = _FakeInnerClient()

    def login(self):
        return "needs_mfa", {"mfa": "client-state"}


class _FakeGarminAuthError:
    def __init__(self, **kwargs) -> None:
        self.client = _FakeInnerClient()

    def login(self):
        raise GarminConnectAuthenticationError("bad credentials")


class _FakeGarminRateLimited:
    def __init__(self, **kwargs) -> None:
        self.client = _FakeInnerClient()

    def login(self):
        raise GarminConnectTooManyRequestsError("slow down")


def test_login_success_returns_base64_token_accepted_by_clients():
    token = login_with_credentials(EMAIL, PASSWORD, garmin_factory=_FakeGarminOk)
    # Decodes back to the garth JSON and passes the client-side validation.
    assert base64.b64decode(token).decode("utf-8") == TOKEN_JSON
    assert json.loads(auth_mod.normalize_token(token))["di_token"] == "fake"


def test_login_passes_credentials_and_return_on_mfa():
    seen = {}

    class Recorder(_FakeGarminOk):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            seen.update(kwargs)

    login_with_credentials(EMAIL, PASSWORD, garmin_factory=Recorder)
    assert seen["email"] == EMAIL
    assert seen["password"] == PASSWORD
    assert seen["return_on_mfa"] is True


def test_login_mfa_required_raises_dedicated_error():
    with pytest.raises(MfaRequiredError) as excinfo:
        login_with_credentials(EMAIL, PASSWORD, garmin_factory=_FakeGarminMfa)
    assert "MFA" in excinfo.value.message


def test_login_bad_credentials_map_to_auth_error():
    with pytest.raises(AuthError):
        login_with_credentials(EMAIL, PASSWORD, garmin_factory=_FakeGarminAuthError)


def test_login_rate_limit_maps_to_rate_limit_error():
    with pytest.raises(RateLimitError):
        login_with_credentials(
            EMAIL, PASSWORD, garmin_factory=_FakeGarminRateLimited
        )


# ---------------------------------------------------------------------------
# /auth routes (HTTP, login function mocked)


@pytest.fixture
def http_client():
    with TestClient(server.build_app()) as client:
        yield client


def test_get_auth_serves_login_form(http_client):
    resp = http_client.get("/auth")
    assert resp.status_code == 200
    assert "<form" in resp.text
    assert 'name="email"' in resp.text
    assert 'name="password"' in resp.text
    assert 'type="password"' in resp.text
    # The page must state the non-storage guarantee up front.
    assert "never stored and never logged" in resp.text


def test_post_auth_success_shows_token_once(http_client, monkeypatch):
    monkeypatch.setattr(
        server, "login_with_credentials", lambda email, password: "issued-token-123"
    )
    resp = http_client.post(
        "/auth", data={"email": EMAIL, "password": PASSWORD}
    )
    assert resp.status_code == 200
    assert "issued-token-123" in resp.text
    assert "only this once" in resp.text
    assert PASSWORD not in resp.text


def test_post_auth_bad_credentials_generic_401(http_client, monkeypatch):
    def fail(email, password):
        raise AuthError("Garmin rejected the token (authentication failed).")

    monkeypatch.setattr(server, "login_with_credentials", fail)
    resp = http_client.post(
        "/auth", data={"email": EMAIL, "password": PASSWORD}
    )
    assert resp.status_code == 401
    assert "Sign-in failed" in resp.text
    assert PASSWORD not in resp.text
    assert EMAIL not in resp.text


def test_post_auth_mfa_account_gets_explicit_message(http_client, monkeypatch):
    def mfa(email, password):
        raise MfaRequiredError(
            "This Garmin account requires MFA (two-factor authentication), "
            "which /auth does not support yet."
        )

    monkeypatch.setattr(server, "login_with_credentials", mfa)
    resp = http_client.post(
        "/auth", data={"email": EMAIL, "password": PASSWORD}
    )
    assert resp.status_code == 200
    assert "MFA" in resp.text
    assert "not support" in resp.text
    assert PASSWORD not in resp.text


def test_post_auth_rate_limited_is_429(http_client, monkeypatch):
    def limited(email, password):
        raise RateLimitError("Garmin Connect rate limit reached. Retry after a pause.")

    monkeypatch.setattr(server, "login_with_credentials", limited)
    resp = http_client.post(
        "/auth", data={"email": EMAIL, "password": PASSWORD}
    )
    assert resp.status_code == 429
    assert PASSWORD not in resp.text


def test_post_auth_missing_fields_is_400(http_client):
    resp = http_client.post("/auth", data={"email": "", "password": ""})
    assert resp.status_code == 400
    assert "required" in resp.text
