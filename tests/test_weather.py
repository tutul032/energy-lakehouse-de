"""Offline tests for energy_lakehouse.weather (HTTP is faked, no network)."""

import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from energy_lakehouse import http_utils, weather
from energy_lakehouse.weather_config import LOCATIONS, WeatherLocation

FIXTURE = Path(__file__).parent / "fixtures" / "openmeteo_hamburg.json"
HAMBURG = WeatherLocation("hamburg", "Hamburg", 53.55, 9.99, "onshore_wind")
MUNICH = WeatherLocation("munich", "Munich", 48.14, 11.58, "solar")
RUN_DATE = date(2026, 10, 19)


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


def test_build_url_params():
    url = weather.build_url(HAMBURG, 63)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == weather.BASE_URL
    assert parse_qs(parts.query) == {
        "latitude": ["53.55"],
        "longitude": ["9.99"],
        "hourly": ["temperature_2m,wind_speed_10m,wind_speed_100m,shortwave_radiation,cloud_cover"],
        "past_days": ["63"],
        "forecast_days": ["1"],
        "timezone": ["GMT"],
        "timeformat": ["unixtime"],
        "wind_speed_unit": ["ms"],
    }
    assert "%2C" not in url  # commas kept readable


@pytest.mark.parametrize("past_days", [-1, 93])
def test_build_url_rejects_out_of_range_past_days(past_days):
    with pytest.raises(ValueError):
        weather.build_url(HAMBURG, past_days)


def test_output_path():
    assert weather.output_path("/Volumes/workspace/energy_dev/landing/", HAMBURG, RUN_DATE) == (
        "/Volumes/workspace/energy_dev/landing/weather/location=hamburg/run_date=2026-10-19.json"
    )


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
        base, [HAMBURG, MUNICH], 63, run_date=RUN_DATE, session=session, sleep=no_sleep
    )
    assert first == {"written": 2, "skipped": 0, "failed": 0}
    written = Path(weather.output_path(base, HAMBURG, RUN_DATE))
    assert written.read_bytes() == raw
    assert json.loads(written.read_bytes())["hourly"]["time"][0] == 1760832000

    session.calls.clear()
    second = weather.extract(
        base, [HAMBURG, MUNICH], 63, run_date=RUN_DATE, session=session, sleep=no_sleep
    )
    assert second == {"written": 0, "skipped": 2, "failed": 0}
    assert session.calls == []


def test_extract_overwrite_redownloads(tmp_path):
    session = FakeSession({weather.build_url(HAMBURG, 63): [FakeResponse(b'{"hourly":{}}')]})
    path = Path(weather.output_path(str(tmp_path), HAMBURG, RUN_DATE))
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old")

    summary = weather.extract(
        str(tmp_path), [HAMBURG], 63, RUN_DATE, overwrite=True, session=session, sleep=no_sleep
    )
    assert summary == {"written": 1, "skipped": 0, "failed": 0}
    assert path.read_bytes() == b'{"hourly":{}}'


def test_extract_defaults_run_date_to_today_utc(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, "today_utc", lambda: RUN_DATE)
    session = FakeSession({weather.build_url(HAMBURG, 1): [FakeResponse(b"{}")]})
    weather.extract(str(tmp_path), [HAMBURG], 1, session=session, sleep=no_sleep)
    assert Path(weather.output_path(str(tmp_path), HAMBURG, RUN_DATE)).exists()


def test_extract_retries_then_succeeds(tmp_path):
    url = weather.build_url(HAMBURG, 63)
    session = FakeSession(
        {url: [requests.ConnectionError("boom"), FakeResponse(b"", 503), FakeResponse(b"{}")]}
    )
    sleeps = []
    summary = weather.extract(
        str(tmp_path), [HAMBURG], 63, RUN_DATE, session=session, sleep=sleeps.append
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
        str(tmp_path), [HAMBURG, MUNICH], 63, RUN_DATE, session=session, sleep=no_sleep
    )
    assert summary == {"written": 1, "skipped": 0, "failed": 1}
    assert session.calls.count(weather.build_url(HAMBURG, 63)) == http_utils.MAX_ATTEMPTS
    assert not Path(weather.output_path(str(tmp_path), HAMBURG, RUN_DATE)).exists()


def test_extract_rejects_out_of_range_past_days(tmp_path):
    with pytest.raises(ValueError):
        weather.extract(str(tmp_path), [HAMBURG], 93, RUN_DATE, session=FakeSession({}))
