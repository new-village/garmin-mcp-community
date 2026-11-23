import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp_community.client import RequestClient, map_garmin_error
from garmin_mcp_community.errors import (
    AuthError,
    InvalidRequestError,
    NoDataError,
    RateLimitError,
)
from garmin_mcp_community.service import GarminService

from conftest import MAIN_DATE, TOKEN, FakeGarmin, fake_factory


def test_profile(service):
    profile = service.get_user_profile()
    assert profile["display_name"] == "test-user"
    assert profile["timezone"] == "Asia/Tokyo"


def test_daily_summary_shaping(service):
    summary = service.get_daily_summary(MAIN_DATE)
    assert summary["date"] == MAIN_DATE
    assert summary["timezone"] == "Asia/Tokyo"
    assert summary["units"]["calories"] == "kcal"
    assert summary["steps"] == 8000 + 22 * 100
    assert summary["distance"]["km"] == pytest.approx(7.1)
    assert summary["calories"]["total"] == 2300
    assert summary["activity_time"]["active_minutes"] == 60.0
    assert summary["intensity_minutes"] == {"moderate": 25, "vigorous": 10}
    assert summary["resting_heart_rate"] == 52


def test_sleep_shaping(service):
    sleep = service.get_sleep(MAIN_DATE)
    assert sleep["sleep_time_minutes"] == 420.0
    assert sleep["sleep_score"] == 85
    assert sleep["stages_minutes"]["deep"] == 60.0
    assert sleep["units"]["sleep_score"].startswith("points")


def test_stress_shaping(service):
    stress = service.get_stress(MAIN_DATE)
    assert stress["average_stress_level"] == 28
    assert stress["max_stress_level"] == 71
    assert len(stress["series"]) == 3
    assert stress["series"][0]["time_local"].startswith(MAIN_DATE)


def test_body_battery_shaping(service):
    bb = service.get_body_battery(MAIN_DATE)
    assert bb["start_of_day"] == 88
    assert bb["end_of_day"] == 33
    assert bb["morning_peak"] == 88
    assert bb["highest"] == 88 and bb["lowest"] == 33
    assert bb["charged"] == 66


def test_heart_rate_shaping(service):
    hr = service.get_heart_rate(MAIN_DATE)
    assert hr["resting_heart_rate"] == 52
    assert hr["max_heart_rate"] == 132
    assert hr["units"]["heart_rate"] == "bpm"


def test_no_data_is_404(service):
    with pytest.raises(NoDataError) as excinfo:
        service.get_daily_summary("2020-01-01")
    assert excinfo.value.status == 404
    with pytest.raises(NoDataError):
        service.get_sleep("2020-01-01")
    with pytest.raises(NoDataError):
        service.get_body_battery("2020-01-01")


def test_invalid_date_is_400(service):
    with pytest.raises(InvalidRequestError) as excinfo:
        service.get_daily_summary("22 Nov 2026")
    assert excinfo.value.status == 400


def test_activities(service):
    result = service.get_activities("2026-11-20", "2026-11-22")
    assert result["count"] == 1
    activity = result["activities"][0]
    assert activity["type"] == "running"
    assert activity["distance"]["km"] == 10.0
    assert activity["duration_minutes"] == 50.0
    assert activity["aerobic_training_effect"] == 3.2
    assert result["units"]["heart_rate"] == "bpm"


def test_activity_detail(service):
    detail = service.get_activity_detail("12345")
    assert detail["activity_id"] == 12345
    assert detail["detail_summary"]["elevation_gain_meters"] == 85.0
    assert detail["detail_metric_points"] == 2
    assert detail["timezone"] == "Asia/Tokyo"


def test_trends_averages_and_direction(service):
    result = service.get_trends(7, end_date=MAIN_DATE)
    assert result["start_date"] == "2026-11-16"
    assert result["end_date"] == MAIN_DATE
    assert result["days_requested"] == 7
    steps = result["trends"]["steps"]
    # Steps increase by 100/day in the fake, so direction must be increasing.
    assert steps["direction"] == "increasing"
    assert steps["days_with_data"] == 7
    assert steps["average"] == pytest.approx(8000 + 19 * 100)
    assert result["trends"]["sleep_minutes"]["average"] == 420.0
    assert result["units"]["steps"] == "steps"


def test_trends_over_cap_is_400(service):
    with pytest.raises(InvalidRequestError) as excinfo:
        service.get_trends(91, end_date=MAIN_DATE)
    assert excinfo.value.status == 400


def test_recovery_status(service):
    recovery = service.get_recovery_status(MAIN_DATE)
    assert recovery["date"] == MAIN_DATE
    assert recovery["timezone"] == "Asia/Tokyo"
    # Sleep score 85 (good), morning BB 88 (good), stress 28 (fair),
    # resting HR 52 vs baseline 52 (good) -> overall good.
    assert recovery["overall"] == "good"
    components = recovery["components"]
    assert components["sleep"]["level"] == "good"
    assert components["sleep"]["garmin_sleep_score"] == 85
    assert components["body_battery"]["morning_peak"] == 88
    assert components["stress"]["level"] == "fair"
    rhr = components["resting_heart_rate"]
    assert rhr["baseline_7d_average"] == 52.0
    assert rhr["delta_vs_baseline"] == 0.0
    assert "not a medical assessment" in recovery["note"]


def test_recovery_no_data_is_404(service):
    with pytest.raises(NoDataError):
        service.get_recovery_status("2020-01-01")


class _RaisingGarmin(FakeGarmin):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def get_stats(self, cdate: str):
        raise self._exc


def test_garmin_auth_error_maps_to_401():
    svc = GarminService(
        TOKEN,
        client_factory=lambda token: RequestClient(
            _RaisingGarmin(GarminConnectAuthenticationError("bad token")), token
        ),
    )
    with pytest.raises(AuthError) as excinfo:
        svc.get_daily_summary(MAIN_DATE)
    assert excinfo.value.status == 401


def test_garmin_rate_limit_maps_to_429():
    svc = GarminService(
        TOKEN,
        client_factory=lambda token: RequestClient(
            _RaisingGarmin(GarminConnectTooManyRequestsError("slow down")), token
        ),
    )
    with pytest.raises(RateLimitError) as excinfo:
        svc.get_daily_summary(MAIN_DATE)
    assert excinfo.value.status == 429


def test_map_garmin_error_passthrough():
    err = NoDataError("gone")
    assert map_garmin_error(err) is err
