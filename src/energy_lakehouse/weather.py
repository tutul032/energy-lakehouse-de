"""Download raw Open-Meteo hourly weather per location to a landing directory.

Pure Python (requests + plain file I/O, no Spark), so the same code runs locally and against
``/Volumes/...`` paths on Databricks serverless. One file per location and run date; each file
covers ``past_days`` back plus today, so consecutive runs overlap and silver deduplicates later.
"""

import logging
import os
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from urllib.parse import urlencode

import requests

from energy_lakehouse.http_utils import get_with_retry, write_bytes
from energy_lakehouse.weather_config import HOURLY_VARIABLES, MAX_PAST_DAYS, WeatherLocation

BASE_URL = "https://api.open-meteo.com/v1/forecast"
PAUSE_S = 0.2

log = logging.getLogger(__name__)


def build_url(location: WeatherLocation, past_days: int) -> str:
    """Forecast API URL for hourly variables in UTC, unix-second timestamps and m/s wind."""
    if not 0 <= past_days <= MAX_PAST_DAYS:
        raise ValueError(f"past_days must be between 0 and {MAX_PAST_DAYS}")
    params = {
        "latitude": location.lat,
        "longitude": location.lon,
        "hourly": ",".join(HOURLY_VARIABLES),
        "past_days": past_days,
        "forecast_days": 1,
        "timezone": "GMT",
        "timeformat": "unixtime",
        "wind_speed_unit": "ms",
    }
    return f"{BASE_URL}?{urlencode(params, safe=',')}"


def fetch(
    session: requests.Session,
    location: WeatherLocation,
    past_days: int,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """Raw response body (JSON bytes, unparsed) for one location."""
    return get_with_retry(session, build_url(location, past_days), sleep=sleep).content


def output_path(base_dir: str, location: WeatherLocation, run_date: date) -> str:
    """Landing path for one raw response, Hive-style partitioned by location and run date."""
    base = base_dir.rstrip("/\\")
    return f"{base}/weather/location={location.location_id}/run_date={run_date.isoformat()}.json"


def today_utc() -> date:
    return datetime.now(UTC).date()


def extract(
    base_dir: str,
    locations: Iterable[WeatherLocation],
    past_days: int,
    run_date: date | None = None,
    overwrite: bool = False,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Download one response per location for ``run_date`` (default: today, UTC).

    Raw JSON is written byte-for-byte. Existing files are skipped unless ``overwrite`` is set,
    so re-runs on the same day are idempotent. Failures are logged and counted, not raised.
    Returns ``{"written": n, "skipped": n, "failed": n}``.
    """
    if not 0 <= past_days <= MAX_PAST_DAYS:
        raise ValueError(f"past_days must be between 0 and {MAX_PAST_DAYS}")
    run_date = run_date or today_utc()
    session = session or requests.Session()
    summary = {"written": 0, "skipped": 0, "failed": 0}

    for location in locations:
        path = output_path(base_dir, location, run_date)
        if not overwrite and os.path.exists(path):
            log.info("SKIPPED %s", path)
            summary["skipped"] += 1
            continue
        try:
            body = fetch(session, location, past_days, sleep)
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
