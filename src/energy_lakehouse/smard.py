"""Download raw SMARD chart_data chunks to a landing directory.

Pure Python (requests + plain file I/O, no Spark), so the same code runs locally and against
``/Volumes/...`` paths on Databricks serverless.
"""

import logging
import os
import time
from collections.abc import Callable, Iterable

import requests

from energy_lakehouse.http_utils import get_with_retry as _get
from energy_lakehouse.http_utils import write_bytes as _write_bytes
from energy_lakehouse.smard_config import SmardSeries

BASE_URL = "https://www.smard.de/app/chart_data"
PAUSE_S = 0.2
DEFAULT_REFRESH_LATEST = 2

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


def timestamps_to_refresh(timestamps: Iterable[int], refresh_latest: int) -> set[int]:
    """The ``refresh_latest`` newest chunk timestamps, which are always re-downloaded.

    SMARD publishes the current week's chunk incrementally (and occasionally revises the previous
    one), so a file written earlier can be missing late-published hours.
    """
    if refresh_latest < 0:
        raise ValueError("refresh_latest must be >= 0")
    if refresh_latest == 0:
        return set()
    return set(sorted(set(timestamps))[-refresh_latest:])


def fetch_index(
    session: requests.Session,
    series: SmardSeries,
    resolution: str,
    sleep: Callable[[float], None] = time.sleep,
) -> list[int]:
    """Return all chunk timestamps (epoch ms) available for ``series``."""
    resp = _get(session, index_url(series, resolution), sleep=sleep)
    return [int(ts) for ts in resp.json()["timestamps"]]


def extract(
    base_dir: str,
    series_list: Iterable[SmardSeries],
    resolution: str,
    n_latest: int,
    overwrite: bool = False,
    refresh_latest: int = DEFAULT_REFRESH_LATEST,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Download the latest ``n_latest`` chunks per series into ``base_dir``.

    Raw JSON is written byte-for-byte. The ``refresh_latest`` newest chunks per series are
    always re-downloaded so late-published hours get filled; older existing files are skipped
    unless ``overwrite`` is set, so re-runs stay idempotent for them. Failures are logged and
    counted, not raised. Returns ``{"written": n, "skipped": n, "failed": n}``.
    """
    if refresh_latest < 0:
        raise ValueError("refresh_latest must be >= 0")
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

        refresh = timestamps_to_refresh(timestamps, refresh_latest)
        for ts in timestamps:
            path = output_path(base_dir, series, resolution, ts)
            if not overwrite and ts not in refresh and os.path.exists(path):
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
