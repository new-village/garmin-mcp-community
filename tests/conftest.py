"""Shared fakes: a garminconnect client stub — no network, no credentials."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from garmin_mcp_community.client import RequestClient
from garmin_mcp_community.service import GarminService

TZ = ZoneInfo("Asia/Tokyo")
TOKEN = '{"di_token": "fake", "di_refresh_token": "fake", "di_client_id": "fake"}'
MAIN_DATE = "2026-11-22"


def _ts_ms(day: str, hour: int) -> int:
    dt = datetime.fromisoformat(day).replace(hour=hour, tzinfo=TZ)
    return int(dt.timestamp() * 1000)


class _InnerClient:
    """Stands in for garminconnect's inner client (token dumps())."""

    def dumps(self) -> str:
        return TOKEN


class FakeGarmin:
    """Duck-typed replacement for garminconnect.Garmin with canned data."""

    def __init__(self) -> None:
        self.client = _InnerClient()

    # -- profile -----------------------------------------------------------
    def get_user_timezone(self) -> str:
        return "Asia/Tokyo"

    def get_user_profile(self) -> dict[str, Any]:
        return {"displayName": "test-user", "fullName": "Test User"}

    # -- daily stats ---------------------------------------------------------
    def get_stats(self, cdate: str) -> dict[str, Any]:
        # Deterministic pseudo-data for any date in Nov 2026; empty otherwise.
        if not cdate.startswith("2026-11-"):
            return {}
        day = int(cdate[-2:])
        return {
            "totalSteps": 8000 + day * 100,
            "totalDistanceMeters": 6000 + day * 50,
            "totalKilocalories": 2300,
            "activeKilocalories": 550,
            "restingKilocalories": 1750,
            "highlyActiveSeconds": 1800,
            "activeSeconds": 3600,
            "sedentarySeconds": 28800,
            "moderateIntensityMinutes": 25,
            "vigorousIntensityMinutes": 10,
            "floorsAscended": 8,
            "restingHeartRate": 52,
            "averageStressLevel": 28,
            "maxStressLevel": 71,
            "bodyBatteryHighestValue": 88,
            "bodyBatteryLowestValue": 22,
        }

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        if cdate != MAIN_DATE and not cdate.startswith("2026-11-"):
            return {}
        return {
            "dailySleepDTO": {
                "calendarDate": cdate,
                "sleepTimeSeconds": 7 * 3600,
                "napTimeSeconds": 0,
                "deepSleepSeconds": 3600,
                "lightSleepSeconds": 14400,
                "remSleepSeconds": 5400,
                "awakeSleepSeconds": 1800,
                "sleepScores": {"overall": {"value": 85}},
                "sleepStartTimestampLocal": f"{cdate}T00:30:00",
                "sleepEndTimestampLocal": f"{cdate}T07:30:00",
                "averageSpO2Value": 97.0,
            }
        }

    def get_stress_data(self, cdate: str) -> dict[str, Any]:
        if not cdate.startswith("2026-11-"):
            return {}
        return {
            "calendarDate": cdate,
            "avgStressLevel": 28,
            "maxStressLevel": 71,
            "restStressDuration": 20000,
            "activityStressDuration": 5000,
            "totalStressDuration": 25000,
            "stressValuesArray": [
                [_ts_ms(cdate, 8), 20],
                [_ts_ms(cdate, 12), 45],
                [_ts_ms(cdate, 18), 30],
            ],
        }

    def get_body_battery(
        self, startdate: str, enddate: str | None = None
    ) -> list[dict[str, Any]]:
        if not startdate.startswith("2026-11-"):
            return []
        return [
            {
                "date": startdate,
                "charged": 66,
                "drained": 55,
                "bodyBatteryValuesArray": [
                    [_ts_ms(startdate, 6), 88, "charged", 1],
                    [_ts_ms(startdate, 12), 60, "drained", 1],
                    [_ts_ms(startdate, 22), 33, "drained", 1],
                ],
            }
        ]

    def get_heart_rates(self, cdate: str) -> dict[str, Any]:
        if not cdate.startswith("2026-11-"):
            return {}
        return {
            "calendarDate": cdate,
            "restingHeartRate": 52,
            "minHeartRate": 48,
            "maxHeartRate": 132,
            "heartRateValues": [
                [_ts_ms(cdate, 8), 60],
                [_ts_ms(cdate, 12), 95],
            ],
        }

    # -- activities ----------------------------------------------------------
    def get_activities_by_date(
        self, startdate: str, enddate: str | None = None, **_: Any
    ) -> list[dict[str, Any]]:
        return [
            {
                "activityId": 12345,
                "activityName": "Morning Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": f"{startdate}T07:00:00",
                "distance": 10000.0,
                "duration": 3000.0,
                "elapsedDuration": 3100.0,
                "averageHR": 145.0,
                "maxHR": 168.0,
                "calories": 620.0,
                "aerobicTrainingEffect": 3.2,
                "anaerobicTrainingEffect": 1.1,
            }
        ]

    def get_activity(self, activity_id: str) -> dict[str, Any]:
        return self.get_activities_by_date(MAIN_DATE)[0]

    def get_activity_details(
        self, activity_id: str, **_: Any
    ) -> dict[str, Any]:
        return {
            "summaryDTO": {
                "averageSpeed": 3.33,
                "maxSpeed": 4.5,
                "totalElevationGain": 85.0,
                "totalElevationLoss": 80.0,
                "averageRunCadence": 176.0,
            },
            "activityDetailMetrics": [{"metrics": [1, 2]}, {"metrics": [3, 4]}],
        }


def fake_factory(token: str) -> RequestClient:
    return RequestClient(FakeGarmin(), token)


@pytest.fixture
def service() -> GarminService:
    return GarminService(TOKEN, client_factory=fake_factory)


@pytest.fixture
def main_date() -> str:
    return MAIN_DATE
