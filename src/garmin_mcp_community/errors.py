"""Error types mapped to the status codes defined in docs/SPEC.md §5.5.

MCP has no HTTP status channel for tool results, so every error carries its
HTTP-style ``status`` and the tool layer renders it into the error message as
``[<status>] <message>`` so an AI client can decide what to do next
(401 = re-authenticate via /auth, 404 = no data, 429 = back off,
502/503 = Garmin-side failure).
"""

from __future__ import annotations


class GarminMcpError(Exception):
    """Base class for all errors surfaced to MCP clients."""

    status: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def render(self) -> str:
        return f"[{self.status}] {self.message}"


class AuthError(GarminMcpError):
    """Missing, malformed or expired token. Client must re-authenticate."""

    status = 401


class MfaRequiredError(GarminMcpError):
    """The Garmin account requires MFA, which /auth cannot complete yet."""

    status = 401


class NoDataError(GarminMcpError):
    """The requested day / activity has no data in Garmin Connect."""

    status = 404


class RateLimitError(GarminMcpError):
    """Garmin rate-limited the request."""

    status = 429


class UpstreamError(GarminMcpError):
    """Garmin Connect is unreachable or returned an unexpected failure."""

    status = 502


class UpstreamUnavailableError(UpstreamError):
    """Garmin Connect connection failure (network / service down)."""

    status = 503


class InvalidRequestError(GarminMcpError):
    """The tool arguments are invalid (e.g. trends range over the cap)."""

    status = 400
