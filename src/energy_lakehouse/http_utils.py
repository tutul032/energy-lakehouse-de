"""Shared HTTP + file helpers for the extractors (requests + plain file I/O, no Spark)."""

import logging
import os
import time
from collections.abc import Callable

import requests

REQUEST_TIMEOUT_S = 30
MAX_ATTEMPTS = 3
BACKOFF_S = 1.0

log = logging.getLogger(__name__)


def get_with_retry(
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


def write_bytes(path: str, data: bytes) -> None:
    """Write ``data`` to ``path``, creating parent directories."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
