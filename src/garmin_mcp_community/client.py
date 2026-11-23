"""Construction of a per-request garminconnect client.

A brand-new ``Garmin`` client is created for every MCP request, logged in
with the token supplied in that request's Authorization header, and then
discarded. No client, token or health data is cached across requests
(SPEC §3.1/§3.2), and nothing here logs the token.
"""

from __future__ import annotations

import base64
from typing import Any, Callable

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from .errors import (
    AuthError,
    GarminMcpError,
    MfaRequiredError,
    NoDataError,
    RateLimitError,
    UpstreamError,
    UpstreamUnavailableError,
)


def map_garmin_error(exc: Exception) -> GarminMcpError:
    """Translate garminconnect exceptions to spec status codes (§5.5)."""
    if isinstance(exc, GarminMcpError):
        return exc
    if isinstance(exc, GarminConnectAuthenticationError):
        return AuthError(
            "Garmin rejected the token (authentication failed). "
            "Re-authenticate via /auth to obtain a fresh token."
        )
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return RateLimitError(
            "Garmin Connect rate limit reached. Retry after a pause."
        )
    if isinstance(exc, GarminConnectNotFoundError):
        return NoDataError("The requested resource was not found in Garmin Connect.")
    if isinstance(exc, GarminConnectConnectionError):
        return UpstreamUnavailableError(
            "Could not connect to Garmin Connect. Try again later."
        )
    return UpstreamError(f"Garmin Connect request failed: {type(exc).__name__}")


class RequestClient:
    """Thin wrapper around a logged-in garminconnect client for one request."""

    def __init__(self, garmin: Any, original_token: str) -> None:
        self._garmin = garmin
        self._original_token = original_token

    @property
    def garmin(self) -> Any:
        return self._garmin

    def refreshed_token(self) -> str | None:
        """Return the session token if Garmin refreshed it during this request."""
        try:
            dumped = self._garmin.client.dumps()
        except Exception:
            return None
        if isinstance(dumped, str) and dumped != self._original_token:
            return dumped
        return None


def create_request_client(token: str) -> RequestClient:
    """Log in to Garmin with a client-held token. Used once per request."""
    garmin = Garmin()
    try:
        garmin.login(tokenstore=token)
    except Exception as exc:  # garminconnect raises several auth flavours
        raise map_garmin_error(exc) from exc
    return RequestClient(garmin, token)


def login_with_credentials(
    email: str,
    password: str,
    garmin_factory: Callable[..., Any] = Garmin,
) -> str:
    """One-shot credential login for the /auth page (SPEC §5.1).

    Performs a full Garmin login with the supplied credentials, in this
    call only, and returns the issued token as base64-encoded garth JSON
    — one of the forms accepted from clients (see
    ``auth.normalize_token``). The credentials are used solely for this
    login attempt: they are never stored, cached or logged here, and the
    garminconnect client is discarded when this function returns.

    TODO(MFA): two-step MFA completion is not implemented yet. With
    ``return_on_mfa=True`` garminconnect reports ``"needs_mfa"`` instead
    of prompting inline. Supporting it later means a second step that
    resumes the flow with the returned client_state plus the user's MFA
    code, while still holding no state server-side between the steps
    (the client would carry the client_state). For now, accounts that
    require MFA raise :class:`MfaRequiredError`.
    """
    garmin = garmin_factory(email=email, password=password, return_on_mfa=True)
    try:
        status, _client_state = garmin.login()
    except Exception as exc:  # garminconnect raises several auth flavours
        raise map_garmin_error(exc) from exc
    if status == "needs_mfa":
        raise MfaRequiredError(
            "This Garmin account requires MFA (two-factor authentication), "
            "which /auth does not support yet."
        )
    try:
        token_json = garmin.client.dumps()
    except Exception as exc:
        raise UpstreamError(
            "Garmin login succeeded but the session token could not be read."
        ) from exc
    if not isinstance(token_json, str) or not token_json:
        raise UpstreamError(
            "Garmin login succeeded but no session token was issued."
        )
    return base64.b64encode(token_json.encode("utf-8")).decode("ascii")
