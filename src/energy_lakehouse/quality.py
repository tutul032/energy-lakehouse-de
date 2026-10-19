"""Pure data-quality checks for the daily refresh (no Spark; the notebook supplies the numbers).

``03_quality_checks`` queries the tables, turns each result into a ``CheckResult`` with the
functions below, prints ``summary_rows()`` and calls ``raise_on_failures()``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# gold_energy_hourly must contain an hour at most this old (SMARD publishes with some delay).
MAX_GOLD_AGE = timedelta(hours=72)
# gold_energy_weather_hourly must have weather in at least one hour within this window.
WEATHER_WINDOW_DAYS = 7


class QualityCheckError(RuntimeError):
    """Raised when at least one quality check fails; fails the Databricks task."""


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check: ``name`` (what was checked), ``passed``, human-readable ``detail``."""

    name: str
    passed: bool
    detail: str


def _as_utc(ts: datetime) -> datetime:
    """Treat naive datetimes as UTC (the notebook sets the Spark session time zone to UTC)."""
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def freshness_check(
    table: str,
    max_ts: datetime | None,
    now: datetime,
    max_age: timedelta = MAX_GOLD_AGE,
) -> CheckResult:
    """Pass if ``max_ts`` is no older than ``max_age`` at ``now``; fail if missing or stale."""
    name = f"{table} freshness"
    if max_ts is None:
        return CheckResult(name, False, f"{table} has no rows (max timestamp is NULL)")
    age = _as_utc(now) - _as_utc(max_ts)
    hours = age.total_seconds() / 3600
    limit = max_age.total_seconds() / 3600
    detail = f"latest {_as_utc(max_ts):%Y-%m-%d %H:%M} UTC, age {hours:.1f} h (limit {limit:g} h)"
    return CheckResult(name, age <= max_age, detail)


def non_empty_check(table: str, row_count: int) -> CheckResult:
    """Pass if ``table`` has at least one row."""
    return CheckResult(f"{table} not empty", row_count > 0, f"{row_count} rows")


def recent_weather_check(
    table: str, row_count: int, window_days: int = WEATHER_WINDOW_DAYS
) -> CheckResult:
    """Pass if ``row_count`` (rows with any weather value in the window) is positive."""
    return CheckResult(
        f"{table} weather in last {window_days} d",
        row_count > 0,
        f"{row_count} rows with weather in the last {window_days} days",
    )


def recent_weather_rows_sql(
    table_fqn: str, weather_columns: Sequence[str], window_days: int = WEATHER_WINDOW_DAYS
) -> str:
    """SQL counting rows of ``table_fqn`` in the last ``window_days`` with any weather value."""
    if not weather_columns:
        raise ValueError("weather_columns must not be empty")
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    any_weather = " OR ".join(f"`{c}` IS NOT NULL" for c in weather_columns)
    return (
        f"SELECT count(*) AS n FROM {table_fqn} "
        f"WHERE hour_utc >= current_timestamp() - INTERVAL {int(window_days)} DAYS "
        f"AND ({any_weather})"
    )


def summary_rows(results: Iterable[CheckResult]) -> list[tuple[str, str, str]]:
    """``(check, status, detail)`` rows for printing; status is ``PASS`` or ``FAIL``."""
    return [(r.name, "PASS" if r.passed else "FAIL", r.detail) for r in results]


def raise_on_failures(results: Iterable[CheckResult]) -> None:
    """Raise ``QualityCheckError`` listing every failed check; do nothing if all passed."""
    failed = [r for r in results if not r.passed]
    if failed:
        lines = "\n".join(f"- {r.name}: {r.detail}" for r in failed)
        raise QualityCheckError(f"{len(failed)} quality check(s) failed:\n{lines}")
