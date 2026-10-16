"""Offline tests for energy_lakehouse.smard (HTTP is faked, no network)."""

import json
from pathlib import Path

import pytest
import requests

from energy_lakehouse import smard
from energy_lakehouse.smard_config import SERIES, SmardSeries

FIXTURE = Path(__file__).parent / "fixtures" / "smard_chunk_410_DE_hour.json"
LOAD = SmardSeries(410, "total_load", "DE", "MWh")
PRICE = SmardSeries(4169, "day_ahead_price", "DE-LU", "EUR/MWh")


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.content)


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


def index_response(timestamps):
    return FakeResponse(json.dumps({"timestamps": timestamps}).encode())


def test_config_has_expected_series():
    assert [s.filter_id for s in SERIES] == [410, 4169, 4068, 4067, 1225, 1223, 4069, 4071]
    assert {s.name: s.region for s in SERIES}["day_ahead_price"] == "DE-LU"


def test_index_url():
    assert smard.index_url(PRICE, "hour") == (
        "https://www.smard.de/app/chart_data/4169/DE-LU/index_hour.json"
    )


def test_chunk_url():
    assert smard.chunk_url(LOAD, "hour", 1759701600000) == (
        "https://www.smard.de/app/chart_data/410/DE/410_DE_hour_1759701600000.json"
    )


def test_output_path():
    assert smard.output_path("/Volumes/workspace/energy_dev/landing/", LOAD, "hour", 123) == (
        "/Volumes/workspace/energy_dev/landing/smard/filter=410/resolution=hour/123.json"
    )


def test_select_timestamps_returns_latest_sorted():
    assert smard.select_timestamps([30, 10, 50, 20, 40], 3) == [30, 40, 50]


def test_select_timestamps_more_than_available():
    assert smard.select_timestamps([2, 1], 8) == [1, 2]


def test_select_timestamps_rejects_non_positive():
    with pytest.raises(ValueError):
        smard.select_timestamps([1, 2], 0)


def test_fetch_index():
    session = FakeSession({smard.index_url(LOAD, "hour"): [index_response([1, 2, 3])]})
    assert smard.fetch_index(session, LOAD, "hour", sleep=no_sleep) == [1, 2, 3]


def test_extract_writes_raw_bytes_and_is_idempotent(tmp_path):
    raw = FIXTURE.read_bytes()
    session = FakeSession(
        {
            smard.index_url(LOAD, "hour"): [index_response([100, 200, 300])],
            smard.chunk_url(LOAD, "hour", 200): [FakeResponse(raw)],
            smard.chunk_url(LOAD, "hour", 300): [FakeResponse(raw)],
        }
    )
    base = str(tmp_path)

    first = smard.extract(base, [LOAD], "hour", 2, session=session, sleep=no_sleep)
    assert first == {"written": 2, "skipped": 0, "failed": 0}
    written = Path(smard.output_path(base, LOAD, "hour", 300))
    assert written.read_bytes() == raw

    session.calls.clear()
    second = smard.extract(base, [LOAD], "hour", 2, session=session, sleep=no_sleep)
    assert second == {"written": 0, "skipped": 2, "failed": 0}
    assert session.calls == [smard.index_url(LOAD, "hour")]  # no chunk re-downloads


def test_extract_overwrite_redownloads(tmp_path):
    session = FakeSession(
        {
            smard.index_url(LOAD, "hour"): [index_response([100])],
            smard.chunk_url(LOAD, "hour", 100): [FakeResponse(b'{"series":[]}')],
        }
    )
    path = Path(smard.output_path(str(tmp_path), LOAD, "hour", 100))
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old")

    summary = smard.extract(
        str(tmp_path), [LOAD], "hour", 1, overwrite=True, session=session, sleep=no_sleep
    )
    assert summary == {"written": 1, "skipped": 0, "failed": 0}
    assert path.read_bytes() == b'{"series":[]}'


def test_get_retries_then_succeeds():
    url = smard.chunk_url(LOAD, "hour", 100)
    session = FakeSession(
        {url: [requests.ConnectionError("boom"), FakeResponse(b"x", 503), FakeResponse(b"ok")]}
    )
    sleeps = []
    resp = smard._get(session, url, sleep=sleeps.append)
    assert resp.content == b"ok"
    assert len(session.calls) == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff


def test_extract_counts_failure_after_retries_exhausted(tmp_path):
    session = FakeSession(
        {
            smard.index_url(LOAD, "hour"): [index_response([100, 200])],
            smard.chunk_url(LOAD, "hour", 100): [FakeResponse(b"", 500)],
            smard.chunk_url(LOAD, "hour", 200): [FakeResponse(b"{}")],
        }
    )
    summary = smard.extract(str(tmp_path), [LOAD], "hour", 2, session=session, sleep=no_sleep)
    assert summary == {"written": 1, "skipped": 0, "failed": 1}
    assert session.calls.count(smard.chunk_url(LOAD, "hour", 100)) == smard.MAX_ATTEMPTS
    assert not Path(smard.output_path(str(tmp_path), LOAD, "hour", 100)).exists()


def test_extract_index_failure_is_counted_and_other_series_continue(tmp_path):
    session = FakeSession(
        {
            smard.index_url(PRICE, "hour"): [FakeResponse(b"", 404)],
            smard.index_url(LOAD, "hour"): [index_response([100])],
            smard.chunk_url(LOAD, "hour", 100): [FakeResponse(b"{}")],
        }
    )
    summary = smard.extract(
        str(tmp_path), [PRICE, LOAD], "hour", 1, session=session, sleep=no_sleep
    )
    assert summary == {"written": 1, "skipped": 0, "failed": 1}
