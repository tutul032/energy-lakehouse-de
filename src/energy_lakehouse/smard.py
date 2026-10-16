"""Download raw SMARD chart_data chunks to a landing directory.

Pure Python (requests + plain file I/O, no Spark), so the same code runs locally and against
``/Volumes/...`` paths on Databricks serverless.
"""

import logging
import os
import time
from collections.abc import Callable, Iterable

import requests

from energy_lakehouse.smard_config import SmardSeries

BASE_URL = "https://www.smard.de/app/chart_data"
REQUEST_TIMEOUT_S = 30
PAUSE_S = 0.2
MAX_ATTEMPTS = 3
BACKOFF_S = 1.0

log = logging.getLogger(__name__)


def index_url(series: SmardSeries, resolution: str) -> str:
    """URL of the index listing one timestamp (ms) per weekly chunk."""
    return f"{BASE_URL}/{series.filter_id}/{series.region}/index_{resolution}.json"


def chunk_url(series: SmardSeries, resolution: str, ts: int) -> str:
    """URL of one weekly chunk starting at ``ts`` (epoch ms)."""
    fid, region = series.filter_id, series.region
    return f"{BASE_URL}/{fid}/{region}/{fid}_{region}_{resolution}_{ts}.json"


def output_path(base_dir: str, series: SmardSeries, resolution: str, ts: int) -> str:
    """Landing path for one raw chunk, Hive-style partitioned by filter and resolution."""
    base = base_dir.rstrip("/\\")
    return f"{base}/smard/filter={series.filter_id}/resolution={resolution}/{ts}.json"


def select_timestamps(timestamps: Iterable[int], n_latest: int) -> list[int]:
    """Return the ``n_latest`` most recent chunk timestamps, ascending."""
    if n_latest < 1:
        raise ValueError("n_latest must be >= 1")
    return sorted(set(timestamps))[-n_latest:]


def _get(
    session: requests.Session,
    url: str,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_s: float = BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
) -> requests.Response:
    """GET with up to ``max_attempts`` tries and exponential backoff (1 s, 2 s, ...)."""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT_S)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == max_attempts:
                raise
            delay = backoff_s * 2 ** (attempt - 1)
            log.warning("attempt %d/%d failed for %s: %s", attempt, max_attempts, url, exc)
            sleep(delay)
    raise AssertionError("unreachable")


def fetch_index(
    session: requests.Session,
    series: SmardSeries,
    resolution: str,
    sleep: Callable[[float], None] = time.sleep,
) -> list[int]:
    """Return all chunk timestamps (epoch ms) available for ``series``."""
    resp = _get(session, index_url(series, resolution), sleep=sleep)
    return [int(ts) for ts in resp.json()["timestamps"]]


def _write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def extract(
    base_dir: str,
    series_list: Iterable[SmardSeries],
    resolution: str,
    n_latest: int,
    overwrite: bool = False,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Download the latest ``n_latest`` chunks per series into ``base_dir``.

    Raw JSON is written byte-for-byte. Existing files are skipped unless ``overwrite`` is set,
    so re-runs are idempotent. Failures are logged and counted, not raised.
    Returns ``{"written": n, "skipped": n, "failed": n}``.
    """
    session = session or requests.Session()
    summary = {"written": 0, "skipped": 0, "failed": 0}

    for series in series_list:
        try:
            timestamps = select_timestamps(
                fetch_index(session, series, resolution, sleep), n_latest
            )
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.error("FAILED  index %s (%s): %s", series.name, series.filter_id, exc)
            summary["failed"] += 1
            continue
        sleep(PAUSE_S)

        for ts in timestamps:
            path = output_path(base_dir, series, resolution, ts)
            if not overwrite and os.path.exists(path):
                log.info("SKIPPED %s", path)
                summary["skipped"] += 1
                continue
            try:
                resp = _get(session, chunk_url(series, resolution, ts), sleep=sleep)
                _write_bytes(path, resp.content)
            except (requests.RequestException, OSError) as exc:
                log.error("FAILED  %s: %s", path, exc)
                summary["failed"] += 1
            else:
                log.info("WROTE   %s (%d bytes)", path, len(resp.content))
                summary["written"] += 1
            sleep(PAUSE_S)

    log.info("summary: %s", summary)
    return summary
