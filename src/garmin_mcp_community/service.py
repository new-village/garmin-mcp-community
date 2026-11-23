"""Tool logic: one GarminService per request, backed by one Garmin client.

The service holds no state between requests. A factory creates the
(request-scoped) Garmin client from the caller's token; every method maps
garminconnect failures to the spec status codes and raises NoDataError
(404) when Garmin simply has nothing for the requested day.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_cls
from datetime import timedelta
from typing import Any

from . import shaping
from .client import RequestClient, create_request_client, map_garmin_error
from .errors import InvalidRequestError, NoDataError

MAX_TREND_DAYS = 90
MAX_ACTIVITIES = 100

ClientFactory = Callable[[str], RequestClient]


def _parse_date(value: str) -> date_cls:
    try:
        return date_cls.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise InvalidRequestError(
            f"Invalid date '{value}'. Use ISO format YYYY-MM-DD."
        ) from exc


class GarminService:
    """Read-only Garmin operations for a single authenticated request."""

    def __init__(
        self,
        token: str,
        client_factory: ClientFactory = create_request_client,
    ) -> None:
        self._request_client = client_factory(token)
        self._client: Any = self._request_client.garmin
        self._timezone: str | None = None

    # -- internals ---------------------------------------------------------

    def refreshed_token(self) -> str | None:
        return self._request_client.refreshed_token()

    def timezone(self) -> str:
        """The user's profile timezone; dates are interpreted in it."""
        if self._timezone is None:
            tz: str | None = None
            getter = getattr(self._client, "get_user_timezone", None)
            if callable(getter):
                try:
                    tz = getter()
                except Exception:
                    tz = None
            self._timezone = tz if isinstance(tz, str) and tz else "UTC"
        return self._timezone

    def _call(self, method: str, *args: Any) -> Any:
        fn = getattr(self._client, method)
        try:
            return fn(*args)
        except Exception as exc:
            raise map_garmin_error(exc) from exc

    # -- tools -------------------------------------------------------------

    def get_user_profile(self) -> dict[str, Any]:
        raw = self._call("get_user_profile")
        return shaping.shape_profile(raw, self.timezone())

    def get_daily_summary(self, date: str) -> dict[str, Any]:
        _parse_date(date)
        raw = self._call("get_stats", date)
        shaped = shaping.shape_daily_summary(raw, date, self.timezone())
        if shaped is None:
            raise NoDataError(f"No daily summary data for {date}.")
        return shaped

    def get_sleep(self, date: str) -> dict[str, Any]:
        _parse_date(date)
        raw = self._call("get_sleep_data", date)
        shaped = shaping.shape_sleep(raw, date, self.timezone())
        if shaped is None:
            raise NoDataError(f"No sleep data for {date}.")
        return shaped

    def get_stress(self, date: str) -> dict[str, Any]:
        _parse_date(date)
        raw = self._call("get_stress_data", date)
        shaped = shaping.shape_stress(raw, date, self.timezone())
        if shaped is None:
            raise NoDataError(f"No stress data for {date}.")
        return shaped

    def get_body_battery(self, date: str) -> dict[str, Any]:
        _parse_date(date)
        raw = self._call("get_body_battery", date, date)
        shaped = shaping.shape_body_battery(raw, date, self.timezone())
        if shaped is None:
            raise NoDataError(f"No Body Battery data for {date}.")
        return shaped

    def get_heart_rate(self, date: str) -> dict[str, Any]:
        _parse_date(date)
        raw = self._call("get_heart_rates", date)
        shaped = shaping.shape_heart_rate(raw, date, self.timezone())
        if shaped is None:
            raise NoDataError(f"No heart rate data for {date}.")
        return shaped

    def get_activities(self, start: str, end: str) -> dict[str, Any]:
        start_d, end_d = _parse_date(start), _parse_date(end)
        if end_d < start_d:
            raise InvalidRequestError("end date must not be before start date.")
        raw = self._call("get_activities_by_date", start, end)
        return shaping.shape_activity_list(
            raw, start, end, self.timezone(), MAX_ACTIVITIES
        )

    def get_activity_detail(self, activity_id: str) -> dict[str, Any]:
        if not activity_id or not str(activity_id).strip():
            raise InvalidRequestError("activity_id must not be empty.")
        summary = self._call("get_activity", str(activity_id))
        detail: Any = None
        try:
            detail = self._call("get_activity_details", str(activity_id))
        except NoDataError:
            detail = None
        shaped = shaping.shape_activity_detail(summary, detail, self.timezone())
        if shaped is None:
            raise NoDataError(f"No activity found for id {activity_id}.")
        return shaped

    def get_trends(
        self, days: int, end_date: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise InvalidRequestError("days must be a positive integer.")
        if days > MAX_TREND_DAYS:
            raise InvalidRequestError(
                f"days must be at most {MAX_TREND_DAYS} (got {days})."
            )
        end_d = _parse_date(end_date) if end_date else date_cls.today()
        start_d = end_d - timedelta(days=days - 1)
        daily: list[dict[str, Any]] = []
        for offset in range(days):
            day = start_d + timedelta(days=offset)
            day_str = day.isoformat()
            stats = self._call("get_stats", day_str)
            sleep_raw = self._call("get_sleep_data", day_str)
            stats = stats if isinstance(stats, dict) else {}
            dto = shaping.sleep_dto(sleep_raw)
            score = None
            if isinstance(dto.get("sleepScores"), dict):
                score = (dto["sleepScores"].get("overall") or {}).get("value")
            daily.append(
                {
                    "date": day_str,
                    "steps": stats.get("totalSteps"),
                    "resting_heart_rate": stats.get("restingHeartRate"),
                    "average_stress": stats.get("averageStressLevel"),
                    "sleep_minutes": shaping._minutes(dto.get("sleepTimeSeconds")),
                    "sleep_score": score,
                    "body_battery_high": stats.get("bodyBatteryHighestValue"),
                }
            )
        return shaping.shape_trends(
            daily, start_d.isoformat(), end_d.isoformat(), self.timezone()
        )

    def get_recovery_status(self, date: str) -> dict[str, Any]:
        day = _parse_date(date)
        stats_raw = self._call("get_stats", date)
        stats = stats_raw if isinstance(stats_raw, dict) else {}
        sleep_raw = self._call("get_sleep_data", date)
        dto = shaping.sleep_dto(sleep_raw)
        sleep_score = None
        if isinstance(dto.get("sleepScores"), dict):
            sleep_score = (dto["sleepScores"].get("overall") or {}).get("value")
        bb_raw = self._call("get_body_battery", date, date)
        bb = shaping.shape_body_battery(bb_raw, date, self.timezone())
        morning = None
        if bb is not None:
            morning = bb.get("morning_peak")
            if morning is None:
                morning = bb.get("highest")

        # Resting-HR baseline: mean of the previous 7 days (best effort —
        # days without data are skipped, failures degrade to no baseline).
        baseline_values: list[float] = []
        for offset in range(1, 8):
            prev = (day - timedelta(days=offset)).isoformat()
            try:
                prev_stats = self._call("get_stats", prev)
            except Exception:
                continue
            if isinstance(prev_stats, dict) and prev_stats.get(
                "restingHeartRate"
            ) is not None:
                baseline_values.append(float(prev_stats["restingHeartRate"]))
        baseline = (
            sum(baseline_values) / len(baseline_values)
            if baseline_values
            else None
        )

        if not stats and not dto and bb is None:
            raise NoDataError(f"No recovery data for {date}.")
        return shaping.shape_recovery(
            date,
            self.timezone(),
            sleep_score=sleep_score,
            sleep_minutes=shaping._minutes(dto.get("sleepTimeSeconds")),
            body_battery_morning=morning,
            average_stress=stats.get("averageStressLevel"),
            resting_heart_rate=stats.get("restingHeartRate"),
            resting_hr_baseline=baseline,
        )
