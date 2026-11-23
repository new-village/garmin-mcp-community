"""Pure shaping of raw Garmin Connect payloads into MCP tool responses.

Every response carries the date it describes, the timezone the date is
interpreted in, and explicit units (SPEC §3.4/§5.5), so an AI client never
has to guess what a number means. These functions are pure: no I/O, no
client, fully unit-testable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# small helpers


def _get(data: Any, *keys: str) -> Any:
    """Nested dict lookup returning None when any level is missing."""
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _round(value: float | int | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _minutes(seconds: float | int | None) -> float | None:
    if seconds is None:
        return None
    return round(float(seconds) / 60.0, 1)


def envelope(date: str, timezone: str, units: dict[str, str]) -> dict[str, Any]:
    return {"date": date, "timezone": timezone, "units": units}


def downsample_series(
    values: list[Any] | None, timezone: str, max_points: int = 96
) -> list[dict[str, Any]]:
    """Shape a ``[[epoch_ms, value, ...], ...]`` array into timestamped points.

    Downsamples evenly to at most ``max_points`` entries so responses stay
    small enough for an AI client to reason over.
    """
    if not values:
        return []
    points: list[dict[str, Any]] = []
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    step = max(1, len(values) // max_points)
    for entry in values[::step]:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        ts_ms, value = entry[0], entry[1]
        point: dict[str, Any] = {"timestamp_ms": ts_ms, "value": value}
        if isinstance(ts_ms, (int, float)):
            point["time_local"] = (
                datetime.fromtimestamp(ts_ms / 1000, tz=tz).isoformat()
            )
        points.append(point)
    return points


# ---------------------------------------------------------------------------
# profile


def shape_profile(raw: Any, timezone: str | None) -> dict[str, Any]:
    """Shape get_user_profile() output (user settings payload)."""
    user_data = _get(raw, "userData") or {}
    display_name = (
        (raw or {}).get("displayName")
        or user_data.get("displayName")
        or _get(raw, "socialProfile", "userName")
    )
    full_name = (
        (raw or {}).get("fullName")
        or user_data.get("firstName")
    )
    return {
        "display_name": display_name,
        "full_name": full_name,
        "timezone": timezone,
        "units": {
            "note": "All other tools state their units per field; dates in "
            "this profile's timezone are the basis for every date argument."
        },
    }


# ---------------------------------------------------------------------------
# daily summary (from get_stats)


DAILY_UNITS = {
    "steps": "steps",
    "distance": "meters (km also provided)",
    "calories": "kcal",
    "activity_time": "minutes (seconds also provided)",
    "intensity_minutes": "minutes",
    "resting_heart_rate": "bpm",
}


def shape_daily_summary(raw: Any, date: str, timezone: str) -> dict[str, Any] | None:
    """Shape get_stats() output; None when Garmin has no stats for the day."""
    if not isinstance(raw, dict) or not raw:
        return None
    distance_m = raw.get("totalDistanceMeters")
    result = envelope(date, timezone, dict(DAILY_UNITS))
    result.update(
        {
            "steps": raw.get("totalSteps"),
            "distance": {
                "meters": distance_m,
                "km": _round(distance_m / 1000.0, 2)
                if distance_m is not None
                else None,
            },
            "calories": {
                "total": raw.get("totalKilocalories"),
                "active": raw.get("activeKilocalories"),
                "resting": raw.get("restingKilocalories"),
            },
            "activity_time": {
                "highly_active_minutes": _minutes(raw.get("highlyActiveSeconds")),
                "active_minutes": _minutes(raw.get("activeSeconds")),
                "sedentary_minutes": _minutes(raw.get("sedentarySeconds")),
                "highly_active_seconds": raw.get("highlyActiveSeconds"),
                "active_seconds": raw.get("activeSeconds"),
                "sedentary_seconds": raw.get("sedentarySeconds"),
            },
            "intensity_minutes": {
                "moderate": raw.get("moderateIntensityMinutes"),
                "vigorous": raw.get("vigorousIntensityMinutes"),
            },
            "floors_ascended": raw.get("floorsAscended"),
            "resting_heart_rate": raw.get("restingHeartRate"),
        }
    )
    return result


# ---------------------------------------------------------------------------
# sleep (from get_sleep_data)


def sleep_dto(raw: Any) -> dict[str, Any]:
    dto = _get(raw, "dailySleepDTO")
    return dto if isinstance(dto, dict) else {}


def shape_sleep(raw: Any, date: str, timezone: str) -> dict[str, Any] | None:
    dto = sleep_dto(raw)
    if not dto or dto.get("sleepTimeSeconds") is None:
        return None
    score = _get(dto, "sleepScores", "overall", "value")
    result = envelope(
        date,
        timezone,
        {
            "durations": "minutes (seconds also provided)",
            "sleep_score": "points (0-100, Garmin sleep score)",
        },
    )
    result.update(
        {
            "sleep_time_minutes": _minutes(dto.get("sleepTimeSeconds")),
            "sleep_time_seconds": dto.get("sleepTimeSeconds"),
            "nap_time_minutes": _minutes(dto.get("napTimeSeconds")),
            "stages_minutes": {
                "deep": _minutes(dto.get("deepSleepSeconds")),
                "light": _minutes(dto.get("lightSleepSeconds")),
                "rem": _minutes(dto.get("remSleepSeconds")),
                "awake": _minutes(dto.get("awakeSleepSeconds")),
            },
            "sleep_score": score,
            "sleep_start_local": dto.get("sleepStartTimestampLocal"),
            "sleep_end_local": dto.get("sleepEndTimestampLocal"),
            "average_spo2_percent": dto.get("averageSpO2Value"),
            "average_respiration_per_min": dto.get("avgSleepRespirationValue"),
        }
    )
    return result


# ---------------------------------------------------------------------------
# stress (from get_stress_data)


def shape_stress(raw: Any, date: str, timezone: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    if raw.get("avgStressLevel") is None and not raw.get("stressValuesArray"):
        return None
    result = envelope(
        date,
        timezone,
        {
            "stress_level": "Garmin stress level (0-100)",
            "durations": "minutes",
            "series": "stress level sampled over the day",
        },
    )
    result.update(
        {
            "average_stress_level": raw.get("avgStressLevel"),
            "max_stress_level": raw.get("maxStressLevel"),
            "rest_stress_minutes": _minutes(raw.get("restStressDuration")),
            "activity_stress_minutes": _minutes(
                raw.get("activityStressDuration")
            ),
            "total_stress_minutes": _minutes(raw.get("totalStressDuration")),
            "series": downsample_series(raw.get("stressValuesArray"), timezone),
        }
    )
    return result


# ---------------------------------------------------------------------------
# body battery (from get_body_battery)


def shape_body_battery(
    raw: Any, date: str, timezone: str
) -> dict[str, Any] | None:
    if not isinstance(raw, list) or not raw:
        return None
    entry = next(
        (e for e in raw if isinstance(e, dict) and e.get("date") == date),
        raw[0] if isinstance(raw[0], dict) else None,
    )
    if not entry:
        return None
    values = entry.get("bodyBatteryValuesArray") or []
    levels = [v[1] for v in values if isinstance(v, (list, tuple)) and len(v) >= 2]
    if not levels:
        return None
    # Morning peak: highest level among readings before local noon, the
    # closest available proxy for "how charged you woke up".
    morning_peak: int | None = None
    try:
        tz = ZoneInfo(timezone)
        morning = [
            v[1]
            for v in values
            if isinstance(v, (list, tuple))
            and len(v) >= 2
            and isinstance(v[0], (int, float))
            and datetime.fromtimestamp(v[0] / 1000, tz=tz).hour < 12
        ]
        morning_peak = max(morning) if morning else None
    except Exception:
        morning_peak = None
    result = envelope(
        date,
        timezone,
        {
            "body_battery": "Body Battery level (0-100)",
            "charged_drained": "Body Battery points",
            "series": "Body Battery level sampled over the day",
        },
    )
    result.update(
        {
            "start_of_day": levels[0],
            "end_of_day": levels[-1],
            "morning_peak": morning_peak,
            "highest": max(levels),
            "lowest": min(levels),
            "charged": entry.get("charged"),
            "drained": entry.get("drained"),
            "series": downsample_series(values, timezone),
        }
    )
    return result


# ---------------------------------------------------------------------------
# heart rate (from get_heart_rates)


def shape_heart_rate(raw: Any, date: str, timezone: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    if raw.get("restingHeartRate") is None and not raw.get("heartRateValues"):
        return None
    result = envelope(
        date, timezone, {"heart_rate": "bpm", "series": "heart rate (bpm)"}
    )
    result.update(
        {
            "resting_heart_rate": raw.get("restingHeartRate"),
            "min_heart_rate": raw.get("minHeartRate"),
            "max_heart_rate": raw.get("maxHeartRate"),
            "series": downsample_series(raw.get("heartRateValues"), timezone),
        }
    )
    return result


# ---------------------------------------------------------------------------
# activities


ACTIVITY_UNITS = {
    "distance": "meters (km also provided)",
    "duration": "minutes (seconds also provided)",
    "heart_rate": "bpm",
    "calories": "kcal",
    "training_effect": "Garmin training effect (0.0-5.0)",
}


def shape_activity(raw: dict[str, Any]) -> dict[str, Any]:
    distance_m = raw.get("distance")
    type_info = raw.get("activityType") or {}
    return {
        "activity_id": raw.get("activityId"),
        "name": raw.get("activityName"),
        "type": type_info.get("typeKey") if isinstance(type_info, dict) else None,
        "start_time_local": raw.get("startTimeLocal"),
        "distance": {
            "meters": distance_m,
            "km": _round(distance_m / 1000.0, 2)
            if distance_m is not None
            else None,
        },
        "duration_minutes": _minutes(raw.get("duration")),
        "duration_seconds": raw.get("duration"),
        "elapsed_duration_minutes": _minutes(raw.get("elapsedDuration")),
        "average_heart_rate": raw.get("averageHR"),
        "max_heart_rate": raw.get("maxHR"),
        "calories": raw.get("calories"),
        "aerobic_training_effect": raw.get("aerobicTrainingEffect"),
        "anaerobic_training_effect": raw.get("anaerobicTrainingEffect"),
    }


def shape_activity_list(
    raw: Any, start: str, end: str, timezone: str, limit: int
) -> dict[str, Any]:
    items = raw if isinstance(raw, list) else []
    shaped = [shape_activity(a) for a in items[:limit] if isinstance(a, dict)]
    result: dict[str, Any] = {
        "start_date": start,
        "end_date": end,
        "timezone": timezone,
        "units": dict(ACTIVITY_UNITS),
        "count": len(shaped),
        "activities": shaped,
    }
    return result


def shape_activity_detail(
    summary_raw: Any, detail_raw: Any, timezone: str
) -> dict[str, Any] | None:
    if not isinstance(summary_raw, dict) or not summary_raw:
        if not isinstance(detail_raw, dict) or not detail_raw:
            return None
        summary_raw = {}
    shaped = shape_activity(summary_raw)
    date = (shaped.get("start_time_local") or "")[:10] or None
    shaped["date"] = date
    shaped["timezone"] = timezone
    shaped["units"] = dict(ACTIVITY_UNITS)
    if isinstance(detail_raw, dict):
        summary_dto = detail_raw.get("summaryDTO") or {}
        if isinstance(summary_dto, dict) and summary_dto:
            shaped["detail_summary"] = {
                "average_speed_mps": summary_dto.get("averageSpeed"),
                "max_speed_mps": summary_dto.get("maxSpeed"),
                "elevation_gain_meters": summary_dto.get("totalElevationGain"),
                "elevation_loss_meters": summary_dto.get("totalElevationLoss"),
                "average_cadence": summary_dto.get("averageRunCadence")
                or summary_dto.get("averageBikeCadence"),
            }
        metrics = detail_raw.get("activityDetailMetrics") or []
        shaped["detail_metric_points"] = len(metrics)
    return shaped


# ---------------------------------------------------------------------------
# trends


def _trend_for(values: list[float]) -> dict[str, Any]:
    """Average/min/max plus a simple first-half vs second-half direction."""
    if not values:
        return {"average": None, "min": None, "max": None,
                "direction": "unknown", "change_first_to_second_half": None}
    avg = sum(values) / len(values)
    half = len(values) // 2
    if half == 0:
        direction, change = "unknown", None
    else:
        first = sum(values[:half]) / half
        second = sum(values[half:]) / (len(values) - half)
        change = round(second - first, 2)
        if abs(change) <= max(0.02 * abs(first), 1e-9):
            direction = "flat"
        elif change > 0:
            direction = "increasing"
        else:
            direction = "decreasing"
    return {
        "average": round(avg, 2),
        "min": min(values),
        "max": max(values),
        "direction": direction,
        "change_first_to_second_half": change,
        "days_with_data": len(values),
    }


def shape_trends(
    daily: list[dict[str, Any]],
    start: str,
    end: str,
    timezone: str,
) -> dict[str, Any]:
    """``daily`` rows: {date, steps, resting_heart_rate, average_stress,
    sleep_minutes, sleep_score, body_battery_high} with None for gaps."""
    metrics = {
        "steps": "steps",
        "resting_heart_rate": "bpm",
        "average_stress": "Garmin stress level (0-100)",
        "sleep_minutes": "minutes",
        "sleep_score": "points (0-100)",
        "body_battery_high": "Body Battery level (0-100)",
    }
    trends: dict[str, Any] = {}
    for key in metrics:
        values = [float(r[key]) for r in daily if r.get(key) is not None]
        trends[key] = _trend_for(values)
    return {
        "start_date": start,
        "end_date": end,
        "timezone": timezone,
        "units": metrics,
        "days_requested": len(daily),
        "daily": daily,
        "trends": trends,
    }


# ---------------------------------------------------------------------------
# recovery status (composite heuristic — not a medical assessment)


def _level(points: float | None) -> str:
    if points is None:
        return "unknown"
    if points >= 1.5:
        return "good"
    if points >= 0.75:
        return "fair"
    return "poor"


def shape_recovery(
    date: str,
    timezone: str,
    *,
    sleep_score: float | None,
    sleep_minutes: float | None,
    body_battery_morning: int | None,
    average_stress: float | None,
    resting_heart_rate: float | None,
    resting_hr_baseline: float | None,
) -> dict[str, Any]:
    """Combine sleep / Body Battery / stress / resting HR into one summary.

    Scoring is a transparent heuristic (good=2, fair=1, poor=0 per
    component); it is a training-readiness aid, not a medical assessment.
    """
    components: dict[str, Any] = {}
    points: list[float] = []

    # Sleep: prefer Garmin's own score, fall back to duration.
    if sleep_score is not None:
        p = 2.0 if sleep_score >= 80 else 1.0 if sleep_score >= 60 else 0.0
        components["sleep"] = {
            "level": _level(p), "garmin_sleep_score": sleep_score,
            "sleep_minutes": sleep_minutes, "basis": "garmin_sleep_score",
        }
        points.append(p)
    elif sleep_minutes is not None:
        p = 2.0 if sleep_minutes >= 420 else 1.0 if sleep_minutes >= 360 else 0.0
        components["sleep"] = {
            "level": _level(p), "garmin_sleep_score": None,
            "sleep_minutes": sleep_minutes, "basis": "sleep_duration",
        }
        points.append(p)
    else:
        components["sleep"] = {"level": "unknown", "garmin_sleep_score": None,
                               "sleep_minutes": None, "basis": None}

    if body_battery_morning is not None:
        p = 2.0 if body_battery_morning >= 75 else 1.0 if body_battery_morning >= 50 else 0.0
        components["body_battery"] = {
            "level": _level(p), "morning_peak": body_battery_morning,
        }
        points.append(p)
    else:
        components["body_battery"] = {"level": "unknown", "morning_peak": None}

    if average_stress is not None:
        p = 2.0 if average_stress <= 25 else 1.0 if average_stress <= 50 else 0.0
        components["stress"] = {
            "level": _level(p), "average_stress_level": average_stress,
        }
        points.append(p)
    else:
        components["stress"] = {"level": "unknown", "average_stress_level": None}

    if resting_heart_rate is not None:
        if resting_hr_baseline is not None:
            delta = float(resting_heart_rate) - float(resting_hr_baseline)
            p = 2.0 if delta <= 1 else 1.0 if delta <= 3 else 0.0
            components["resting_heart_rate"] = {
                "level": _level(p), "value": resting_heart_rate,
                "baseline_7d_average": _round(resting_hr_baseline),
                "delta_vs_baseline": _round(delta),
            }
        else:
            p = None
            components["resting_heart_rate"] = {
                "level": "unknown", "value": resting_heart_rate,
                "baseline_7d_average": None, "delta_vs_baseline": None,
            }
        if p is not None:
            points.append(p)
    else:
        components["resting_heart_rate"] = {
            "level": "unknown", "value": None,
            "baseline_7d_average": None, "delta_vs_baseline": None,
        }

    overall_points = sum(points) / len(points) if points else None
    overall = _level(overall_points)
    result = envelope(
        date,
        timezone,
        {
            "sleep_score": "points (0-100)",
            "sleep_minutes": "minutes",
            "body_battery": "Body Battery level (0-100)",
            "average_stress": "Garmin stress level (0-100)",
            "resting_heart_rate": "bpm",
        },
    )
    result.update(
        {
            "overall": overall,
            "components": components,
            "note": "Heuristic summary of Garmin metrics for training "
            "readiness. This is not a medical assessment.",
        }
    )
    return result
