"""Offline tests for energy_lakehouse.weather (HTTP is faked, no network)."""

import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from energy_lakehouse import http_utils, weather
from energy_lakehouse.weather_config import DEFAULT_PAST_DAYS, LOCATIONS, WeatherLocation

FIXTURE = Path(__file__).parent / "fixtures" / "openmeteo_hamburg.json"
HAMBURG = WeatherLocation("hamburg", "Hamburg", 53.55, 9.99, "onshore_wind")
MUNICH = WeatherLocation("munich", "Munich", 48.14, 11.58, "solar")
RUN_TS = datetime(2026, 10, 19, 5, 17, tzinfo=UTC)


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves queued responses per URL; an Exception in the queue is raised instead."""

    def __init__(self, routes: dict[str, list]):
        self.routes = {url: list(queue) for url, queue in routes.items()}
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        item = self.routes[url].pop(0) if len(self.routes[url]) > 1 else self.routes[url][0]
        if isinstance(item, Exception):
            raise item
        return item


def no_sleep(_seconds):
    pass


def test_config_has_expected_locations():
    assert [loc.location_id for loc in LOCATIONS] == [
        "hamburg",
        "north_sea",
        "berlin",
        "essen",
        "frankfurt",
        "munich",
        "stuttgart",
    ]
    assert {loc.location_id: loc.role for loc in LOCATIONS}["north_sea"] == "offshore_wind"
    assert len({loc.location_id for loc in LOCATIONS}) == len(LOCATIONS)


HOURLY = ["temperature_2m,wind_speed_10m,wind_speed_100m,shortwave_radiation,cloud_cover"]
START = date(2026, 8, 1)
END = date(2026, 10, 15)


def test_build_url_params():
    url = weather.build_url(HAMBURG, 63)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == weather.BASE_URL
    assert parse_qs(parts.query) == {
        "latitude": ["53.55"],
        "longitude": ["9.99"],
        "hourly": HOURLY,
        "past_days": ["63"],
        "forecast_days": ["3"],
        "timezone": ["GMT"],
        "timeformat": ["unixtime"],
        "wind_speed_unit": ["ms"],
    }
    assert "%2C" not in url  # commas kept readable


def test_build_url_daily_defaults_cover_local_day_ahead():
    query = parse_qs(urlsplit(weather.build_url(HAMBURG, DEFAULT_PAST_DAYS)).query)
    assert query["past_days"] == ["7"]
    assert query["forecast_days"] == ["3"]  # today + 2: full Berlin D+1 at any run time/DST


@pytest.mark.parametrize("past_days", [-1, 93])
def test_build_url_rejects_out_of_range_past_days(past_days):
    with pytest.raises(ValueError):
        weather.build_url(HAMBURG, past_days)


@pytest.mark.parametrize("forecast_days", [0, 17])
def test_build_url_rejects_out_of_range_forecast_days(forecast_days):
    with pytest.raises(ValueError):
        weather.build_url(HAMBURG, 7, forecast_days)


def test_build_archive_url_params():
    url = weather.build_archive_url(HAMBURG, START, END)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == weather.ARCHIVE_URL
    assert weather.ARCHIVE_URL == "https://archive-api.open-meteo.com/v1/archive"
    assert parse_qs(parts.query) == {
        "latitude": ["53.55"],
        "longitude": ["9.99"],
        "hourly": HOURLY,
        "start_date": ["2026-08-01"],
        "end_date": ["2026-10-15"],
        "timezone": ["GMT"],
        "timeformat": ["unixtime"],
        "wind_speed_unit": ["ms"],
    }
    assert "%2C" not in url


def test_build_archive_url_single_day_and_rejects_reversed_range():
    assert "start_date=2026-08-01&end_date=2026-08-01" in weather.build_archive_url(
        HAMBURG, START, START
    )
    with pytest.raises(ValueError):
        weather.build_archive_url(HAMBURG, END, START)


def test_archive_output_path():
    assert weather.archive_output_path("data/landing/", HAMBURG, START, END) == (
        "data/landing/weather/location=hamburg/run_date=archive_2026-08-01_2026-10-15.json"
    )


def test_output_path_uses_utc_run_timestamp():
    assert weather.output_path("/Volumes/workspace/energy_dev/landing/", HAMBURG, RUN_TS) == (
        "/Volumes/workspace/energy_dev/landing/weather/location=hamburg/run_ts=20261019T0517Z.json"
    )


def test_format_run_ts_converts_to_utc_and_treats_naive_as_utc():
    berlin_summer = timezone(timedelta(hours=2))
    assert weather.format_run_ts(datetime(2026, 10, 19, 7, 17, tzinfo=berlin_summer)) == (
        "20261019T0517Z"
    )
    assert weather.format_run_ts(datetime(2026, 10, 19, 5, 17, 59)) == "20261019T0517Z"


def test_now_utc_is_aware_and_truncated_to_minute():
    now = weather.now_utc()
    assert now.tzinfo == UTC
    assert (now.second, now.microsecond) == (0, 0)


def test_fetch_returns_raw_bytes():
    raw = FIXTURE.read_bytes()
    session = FakeSession({weather.build_url(HAMBURG, 5): [FakeResponse(raw)]})
    assert weather.fetch(session, HAMBURG, 5, sleep=no_sleep) == raw


def test_extract_writes_raw_bytes_and_is_idempotent(tmp_path):
    raw = FIXTURE.read_bytes()
    session = FakeSession(
        {
            weather.build_url(HAMBURG, 63): [FakeResponse(raw)],
            weather.build_url(MUNICH, 63): [FakeResponse(raw)],
        }
    )
    base = str(tmp_path)

    first = weather.extract(
        base, [HAMBURG, MUNICH], 63, run_ts=RUN_TS, session=session, sleep=no_sleep
    )
    assert first == {"written": 2, "skipped": 0, "failed": 0}
    written = Path(weather.output_path(base, HAMBURG, RUN_TS))
    assert written.read_bytes() == raw
    assert json.loads(written.read_bytes())["hourly"]["time"][0] == 1760832000

    session.calls.clear()
    second = weather.extract(
        base, [HAMBURG, MUNICH], 63, run_ts=RUN_TS, session=session, sleep=no_sleep
    )
    assert second == {"written": 0, "skipped": 2, "failed": 0}
    assert session.calls == []


def test_extract_later_run_same_day_writes_new_file(tmp_path):
    session = FakeSession({weather.build_url(HAMBURG, 63): [FakeResponse(b'{"hourly":{}}')]})
    base = str(tmp_path)
    first = Path(weather.output_path(base, HAMBURG, RUN_TS))
    first.parent.mkdir(parents=True)
    first.write_bytes(b"old")

    later = RUN_TS + timedelta(hours=6)
    summary = weather.extract(base, [HAMBURG], 63, later, session=session, sleep=no_sleep)
    assert summary == {"written": 1, "skipped": 0, "failed": 0}
    assert first.read_bytes() == b"old"  # never overwritten
    assert Path(weather.output_path(base, HAMBURG, later)).read_bytes() == b'{"hourly":{}}'


def test_extract_has_no_overwrite_option(tmp_path):
    with pytest.raises(TypeError):
        weather.extract(str(tmp_path), [HAMBURG], 63, RUN_TS, overwrite=True)


def test_extract_defaults_run_ts_to_now_utc(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, "now_utc", lambda: RUN_TS)
    session = FakeSession({weather.build_url(HAMBURG, 1): [FakeResponse(b"{}")]})
    weather.extract(str(tmp_path), [HAMBURG], 1, session=session, sleep=no_sleep)
    assert Path(weather.output_path(str(tmp_path), HAMBURG, RUN_TS)).exists()


def test_extract_retries_then_succeeds(tmp_path):
    url = weather.build_url(HAMBURG, 63)
    session = FakeSession(
        {url: [requests.ConnectionError("boom"), FakeResponse(b"", 503), FakeResponse(b"{}")]}
    )
    sleeps = []
    summary = weather.extract(
        str(tmp_path), [HAMBURG], 63, RUN_TS, session=session, sleep=sleeps.append
    )
    assert summary == {"written": 1, "skipped": 0, "failed": 0}
    assert len(session.calls) == 3
    assert sleeps == [1.0, 2.0, weather.PAUSE_S]  # exponential backoff, then polite pause


def test_extract_counts_failure_after_retries_and_continues(tmp_path):
    session = FakeSession(
        {
            weather.build_url(HAMBURG, 63): [FakeResponse(b"", 500)],
            weather.build_url(MUNICH, 63): [FakeResponse(b"{}")],
        }
    )
    summary = weather.extract(
        str(tmp_path), [HAMBURG, MUNICH], 63, RUN_TS, session=session, sleep=no_sleep
    )
    assert summary == {"written": 1, "skipped": 0, "failed": 1}
    assert session.calls.count(weather.build_url(HAMBURG, 63)) == http_utils.MAX_ATTEMPTS
    assert not Path(weather.output_path(str(tmp_path), HAMBURG, RUN_TS)).exists()


def test_extract_rejects_out_of_range_past_days(tmp_path):
    with pytest.raises(ValueError):
        weather.extract(str(tmp_path), [HAMBURG], 93, RUN_TS, session=FakeSession({}))


def test_extract_archive_writes_one_file_per_location_and_is_idempotent(tmp_path):
    raw = FIXTURE.read_bytes()
    session = FakeSession(
        {
            weather.build_archive_url(HAMBURG, START, END): [FakeResponse(raw)],
            weather.build_archive_url(MUNICH, START, END): [FakeResponse(b"", 500)],
        }
    )
    base = str(tmp_path)
    first = weather.extract_archive(
        base, [HAMBURG, MUNICH], START, END, session=session, sleep=no_sleep
    )
    assert first == {"written": 1, "skipped": 0, "failed": 1}
    assert Path(weather.archive_output_path(base, HAMBURG, START, END)).read_bytes() == raw

    session.calls.clear()
    second = weather.extract_archive(base, [HAMBURG], START, END, session=session, sleep=no_sleep)
    assert second == {"written": 0, "skipped": 1, "failed": 0}
    assert session.calls == []


def test_extract_archive_rejects_reversed_range(tmp_path):
    with pytest.raises(ValueError):
        weather.extract_archive(str(tmp_path), [HAMBURG], END, START, session=FakeSession({}))
