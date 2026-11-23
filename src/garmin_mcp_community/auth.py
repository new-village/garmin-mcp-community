"""Per-request Garmin token handling (SPEC §5.1).

The server is stateless: it never stores tokens or sessions. The client
holds the Garmin (garth-format) token and sends it on every MCP request as
``Authorization: Bearer <token>``. The token is the JSON string produced by
the garminconnect client's ``dumps()``; because raw JSON is awkward in a
header, base64-encoded JSON is also accepted and normalised here.

Nothing in this module logs the token.
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import json
from collections.abc import Mapping

from .errors import AuthError

# Mutable per-request holder for a refreshed token. The ASGI middleware in
# server.py creates the dict, sets it into this contextvar, and after the
# response reads it back to emit the ``X-Garmin-Token`` response header.
# A dict is used (rather than a plain value) so mutations made inside the
# request task are visible to the middleware even across copied contexts.
refreshed_token_holder: contextvars.ContextVar[dict[str, str] | None] = (
    contextvars.ContextVar("refreshed_token_holder", default=None)
)


def extract_bearer_token(headers: Mapping[str, str]) -> str:
    """Return the normalised Garmin token from request headers.

    Raises AuthError (401) when the header is missing or the token is not
    recognisable garth token JSON.
    """
    raw: str | None = None
    for key, value in headers.items():
        if key.lower() == "authorization":
            raw = value
            break
    if not raw:
        raise AuthError(
            "Missing Authorization header. Authenticate via /auth and send "
            "the issued token as 'Authorization: Bearer <token>'."
        )
    scheme, _, credential = raw.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise AuthError(
            "Authorization header must be 'Bearer <garmin-token>'. "
            "Re-authenticate via /auth if you do not have a token."
        )
    return normalize_token(credential.strip())


def normalize_token(credential: str) -> str:
    """Validate a credential as garth token JSON and return the JSON string.

    Accepts either the raw JSON string or base64-encoded JSON.
    """
    candidates = [credential]
    try:
        decoded = base64.b64decode(credential, validate=True).decode("utf-8")
        candidates.append(decoded)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, (dict, list)):
            return candidate
    raise AuthError(
        "The supplied token is not valid Garmin token data. "
        "Re-authenticate via /auth to obtain a fresh token."
    )


def note_refreshed_token(original: str, refreshed: str | None) -> None:
    """Record a refreshed token for the X-Garmin-Token response header."""
    if refreshed and refreshed != original:
        holder = refreshed_token_holder.get()
        if holder is not None:
            holder["token"] = refreshed
