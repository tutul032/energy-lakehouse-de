"""Download raw Open-Meteo hourly weather per location to a landing directory.

Pure Python (requests + plain file I/O, no Spark), so the same code runs locally and against
``/Volumes/...`` paths on Databricks serverless. Two modes:

- daily (``extract``): forecast API, one file per location and UTC run timestamp
  (``run_ts=<yyyymmddThhmmZ>.json``, never overwritten) covering ``past_days`` back plus
  ``forecast_days`` (today + 2 days, so the full local D+1 is covered at any run time/DST).
  Consecutive runs overlap (silver dedupes); the newest file holds the forecast at issue time.
- archive (``extract_archive``): historical API for a fixed date range, one file per location
  named ``run_date=archive_<start>_<end>.json``; used once to backfill history.
"""

import logging
import os
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from urllib.parse import urlencode

import requests

from energy_lakehouse.http_utils import get_with_retry, write_bytes
from energy_lakehouse.weather_config import (
    DEFAULT_FORECAST_DAYS,
    HOURLY_VARIABLES,
    MAX_FORECAST_DAYS,
    MAX_PAST_DAYS,
    RUN_TS_FORMAT,
    WeatherLocation,
)

BASE_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PAUSE_S = 0.2
# Both APIs: UTC, unix-second timestamps, wind in m/s.
_FORMAT_PARAMS = {"timezone": "GMT", "timeformat": "unixtime", "wind_speed_unit": "ms"}

log = logging.getLogger(__name__)


def _common_params(location: WeatherLocation) -> dict[str, object]:
    """Location and hourly variables, shared by the forecast and archive URLs."""
    return {
        "latitude": location.lat,
        "longitude": location.lon,
        "hourly": ",".join(HOURLY_VARIABLES),
    }


def _check_days(past_days: int, forecast_days: int) -> None:
    if not 0 <= past_days <= MAX_PAST_DAYS:
        raise ValueError(f"past_days must be between 0 and {MAX_PAST_DAYS}")
    if not 1 <= forecast_days <= MAX_FORECAST_DAYS:
        raise ValueError(f"forecast_days must be between 1 and {MAX_FORECAST_DAYS}")


def build_url(
    location: WeatherLocation, past_days: int, forecast_days: int = DEFAULT_FORECAST_DAYS
) -> str:
    """Forecast API URL: ``past_days`` back plus ``forecast_days`` (today counts as one)."""
    _check_days(past_days, forecast_days)
    params = {
        **_common_params(location),
        "past_days": past_days,
        "forecast_days": forecast_days,
        **_FORMAT_PARAMS,
    }
    return f"{BASE_URL}?{urlencode(params, safe=',')}"


def build_archive_url(location: WeatherLocation, start: date, end: date) -> str:
    """Historical (archive) API URL for the inclusive UTC date range ``start``..``end``."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    params = {
        **_common_params(location),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        **_FORMAT_PARAMS,
    }
    return f"{ARCHIVE_URL}?{urlencode(params, safe=',')}"


def format_run_ts(run_ts: datetime) -> str:
    """UTC run timestamp as used in file names, e.g. ``20261022T0517Z`` (naive = UTC)."""
    if run_ts.tzinfo is not None:
        run_ts = run_ts.astimezone(UTC)
    return run_ts.strftime(RUN_TS_FORMAT)


def output_path(base_dir: str, location: WeatherLocation, run_ts: datetime) -> str:
    """Landing path for one daily response: ``location=<id>/run_ts=<yyyymmddThhmmZ>.json``."""
    base = base_dir.rstrip("/\\")
    return f"{base}/weather/location={location.location_id}/run_ts={format_run_ts(run_ts)}.json"


def archive_output_path(base_dir: str, location: WeatherLocation, start: date, end: date) -> str:
    """Landing path for one archive response: ``run_date=archive_<start>_<end>.json``."""
    base = base_dir.rstrip("/\\")
    return (
        f"{base}/weather/location={location.location_id}/"
        f"run_date=archive_{start.isoformat()}_{end.isoformat()}.json"
    )


def now_utc() -> datetime:
    """Current UTC time, truncated to the minute (the file name resolution)."""
    return datetime.now(UTC).replace(second=0, microsecond=0)


def _download_all(
    jobs: Iterable[tuple[str, str]],
    overwrite: bool,
    session: requests.Session,
    sleep: Callable[[float], None],
) -> dict[str, int]:
    """Download each ``(path, url)`` byte-for-byte; skip existing files unless ``overwrite``.

    Failures are logged and counted, not raised.
    """
    summary = {"written": 0, "skipped": 0, "failed": 0}
    for path, url in jobs:
        if not overwrite and os.path.exists(path):
            log.info("SKIPPED %s", path)
            summary["skipped"] += 1
            continue
        try:
            body = get_with_retry(session, url, sleep=sleep).content
            write_bytes(path, body)
        except (requests.RequestException, OSError) as exc:
            log.error("FAILED  %s: %s", path, exc)
            summary["failed"] += 1
        else:
            log.info("WROTE   %s (%d bytes)", path, len(body))
            summary["written"] += 1
        sleep(PAUSE_S)

    log.info("summary: %s", summary)
    return summary


def extract(
    base_dir: str,
    locations: Iterable[WeatherLocation],
    past_days: int,
    run_ts: datetime | None = None,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
    forecast_days: int = DEFAULT_FORECAST_DAYS,
) -> dict[str, int]:
    """Daily mode: one forecast response per location for ``run_ts`` (default: now, UTC).

    Raw JSON is written byte-for-byte. Every run gets a new file name, so a later run on the same
    day fetches a fresh forecast; existing files are never overwritten (a second run within the
    same minute is skipped). Returns ``{"written", "skipped", "failed"}``.
    """
    _check_days(past_days, forecast_days)
    run_ts = run_ts or now_utc()
    jobs = [
        (output_path(base_dir, loc, run_ts), build_url(loc, past_days, forecast_days))
        for loc in locations
    ]
    return _download_all(jobs, False, session or requests.Session(), sleep)


def extract_archive(
    base_dir: str,
    locations: Iterable[WeatherLocation],
    start: date,
    end: date,
    overwrite: bool = False,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Archive mode: one historical response per location for ``start``..``end`` (inclusive).

    Same skip/overwrite and failure handling as ``extract``.
    """
    jobs = [
        (archive_output_path(base_dir, loc, start, end), build_archive_url(loc, start, end))
        for loc in locations
    ]
    return _download_all(jobs, overwrite, session or requests.Session(), sleep)
