# Implementation Notes — v1 (2026-11-23)

First implementation pass against the approved spec (`docs/SPEC.md` v1).
Local files only; nothing has been pushed to GitHub yet.

## Implemented

- **Project**: Python package `garmin_mcp_community` under `src/`,
  FastMCP 4.x + garminconnect 0.3.x, `pyproject.toml` with a
  `garmin-mcp-community` console entry point.
- **Tools (exactly the v1 read-only set, no write tools)**:
  `get_user_profile`, `get_daily_summary`, `get_sleep`, `get_stress`,
  `get_body_battery`, `get_heart_rate`, `get_activities`,
  `get_activity_detail`, `get_trends` (1–90 days, cap enforced),
  `get_recovery_status`. Verified live via an MCP client against the
  running HTTP server: tool list matches exactly.
- **Stateless auth (SPEC §5.1)**: a brand-new garminconnect client is
  created per request from the `Authorization: Bearer <token>` header
  (raw garth JSON or base64-encoded JSON both accepted), used only for
  that request, then discarded. No token or health data is stored,
  cached across requests, or logged anywhere in the code.
- **Token refresh header**: if Garmin's client session token changed
  during a request, the new token is emitted as `X-Garmin-Token` via an
  ASGI middleware + per-request contextvar holder (see limitation below).
- **Error mapping (SPEC §5.5)**: errors render as `[<status>] <message>` —
  401 re-auth (missing/invalid/rejected token), 404 no data for the
  day/activity, 429 Garmin rate limit, 502 generic Garmin failure,
  503 connection failure, 400 invalid arguments (bad date, trends > 90).
- **Response shaping**: pure functions in `shaping.py`. Every response
  includes date, the user's profile timezone (via garminconnect's
  `get_user_timezone()`), and explicit units. Series data is downsampled
  to ≤96 points with local timestamps. Trends compare first-half vs
  second-half means for direction. Recovery status is a transparent
  heuristic composite (sleep score/duration, Body Battery morning peak,
  average stress, resting HR vs 7-day baseline) and is labelled
  "not a medical assessment" in the response.
- **Transport**: Streamable HTTP (`stateless_http=True`), served by
  uvicorn on `0.0.0.0:$PORT` (Cloud Run convention, default 8080).
- **Dockerfile**: `python:3.12-slim`, `pip install .`, runs the console
  entry point. (Not build-tested locally — no Docker in the dev
  environment; the same `pip install .` path was verified in a venv.)
- **GitHub Actions** (`.github/workflows/deploy.yml`): on push to `main`,
  keyless Workload Identity Federation auth + `gcloud run deploy
  --source .`. Project `kuro-intelligence` and region `asia-northeast1`
  are filled in; WIF provider and deployer service account are marked
  `TODO_...` placeholders pending GCP-side setup.
- **Tests**: 26 pytest tests, all passing, using a mocked garminconnect
  client (no network, no credentials): shaping of summary/sleep/stress/
  Body Battery/heart rate/activities, trends averages + direction + cap,
  recovery composite incl. baseline, error mapping (401/404/429/400),
  token extraction/normalisation, refreshed-token holder, server-level
  missing/invalid token → `[401]` ToolError, and exact tool inventory.
  Also smoke-tested over real HTTP: `/healthz` OK, `/auth` page served,
  unauthenticated tool call returns the `[401]` error.

## Stubbed

- **`/auth`**: placeholder page only. It explains the intended flow
  (user logs in to Garmin incl. MFA → token issued once to the client →
  server keeps nothing) but performs no login and issues no token.
  The real interactive flow lands with the real-account PoC.
- **Token refresh header**: implemented, but depends on FastMCP running
  tools inside the HTTP request context (true for FastMCP 4.2.4 as
  tested). If that ever changes, the header silently stops being
  emitted and clients fall back to re-authenticating on 401. Noted in
  a comment on `RefreshedTokenMiddleware` in `server.py`.

## Remaining for the real-account PoC (blocked, separate step)

- Interactive Garmin login with the user's real account (incl. MFA
  state, still unconfirmed) to issue the first real token and verify
  the garth JSON format against garminconnect 0.3.x's `dumps()`.
- Verify real payload shapes: `get_user_profile()` field layout and
  `get_stats()` keys vary by account/device; shaping is defensive
  (missing keys → null) but should be checked against real data,
  especially sleep DTO, Body Battery arrays and activity summaries.
- GCP-side setup: enable APIs in `kuro-intelligence`, create the WIF
  pool/provider + deployer service account, fill the two `TODO_`
  values in the workflow, first Cloud Run deploy.
- Decide the hosted instance's public URL / client onboarding text
  once `/auth` is real.
