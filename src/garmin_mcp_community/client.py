"""Construction of a per-request garminconnect client.

A brand-new ``Garmin`` client is created for every MCP request, logged in
with the token supplied in that request's Authorization header, and then
discarded. No client, token or health data is cached across requests
(SPEC §3.1/§3.2), and nothing here logs the token.
"""

from __future__ import annotations

from typing import Any

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
