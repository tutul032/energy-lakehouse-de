"""Offline tests for energy_lakehouse.quality (no Spark)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from energy_lakehouse import quality
from energy_lakehouse.quality import CheckResult, QualityCheckError

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_freshness_passes_within_limit():
    result = quality.freshness_check("gold", NOW - timedelta(hours=71), NOW)
    assert result.passed
    assert "age 71.0 h" in result.detail


def test_freshness_passes_exactly_at_limit():
    assert quality.freshness_check("gold", NOW - timedelta(hours=72), NOW).passed


def test_freshness_fails_when_stale():
    result = quality.freshness_check("gold", NOW - timedelta(hours=73), NOW)
    assert not result.passed
    assert "limit 72 h" in result.detail


def test_freshness_fails_when_no_rows():
    result = quality.freshness_check("gold", None, NOW)
    assert not result.passed
    assert "no rows" in result.detail


def test_freshness_treats_naive_as_utc():
    naive = datetime(2026, 10, 20, 10, 0)
    assert quality.freshness_check("gold", naive, NOW).detail.startswith("latest 2026-10-20 10:00")


def test_freshness_converts_other_time_zones():
    berlin_summer = timezone(timedelta(hours=2))
    ts = datetime(2026, 10, 17, 14, 0, tzinfo=berlin_summer)  # 12:00 UTC, exactly 72 h old
    assert quality.freshness_check("gold", ts, NOW).passed


def test_freshness_custom_max_age():
    result = quality.freshness_check("t", NOW - timedelta(hours=2), NOW, timedelta(hours=1))
    assert not result.passed


@pytest.mark.parametrize(("rows", "passed"), [(0, False), (1, True), (500, True)])
def test_non_empty_check(rows, passed):
    result = quality.non_empty_check("silver", rows)
    assert result.passed is passed
    assert result.detail == f"{rows} rows"


@pytest.mark.parametrize(("rows", "passed"), [(0, False), (24, True)])
def test_recent_weather_check(rows, passed):
    result = quality.recent_weather_check("gold_w", rows)
    assert result.passed is passed
    assert "last 7 days" in result.detail


def test_recent_weather_rows_sql():
    sql = quality.recent_weather_rows_sql("`c`.`s`.`t`", ["a", "b"], 3)
    assert sql == (
        "SELECT count(*) AS n FROM `c`.`s`.`t` "
        "WHERE hour_utc >= current_timestamp() - INTERVAL 3 DAYS "
        "AND (`a` IS NOT NULL OR `b` IS NOT NULL)"
    )


@pytest.mark.parametrize(("columns", "days"), [([], 7), (["a"], 0), (["a"], -1)])
def test_recent_weather_rows_sql_rejects_bad_input(columns, days):
    with pytest.raises(ValueError):
        quality.recent_weather_rows_sql("t", columns, days)


def test_summary_rows():
    results = [CheckResult("a", True, "ok"), CheckResult("b", False, "bad")]
    assert quality.summary_rows(results) == [("a", "PASS", "ok"), ("b", "FAIL", "bad")]


def test_raise_on_failures_passes_silently():
    quality.raise_on_failures([CheckResult("a", True, "ok")])


def test_raise_on_failures_lists_every_failure():
    results = [
        CheckResult("a", False, "stale"),
        CheckResult("b", True, "ok"),
        CheckResult("c", False, "empty"),
    ]
    with pytest.raises(QualityCheckError) as exc:
        quality.raise_on_failures(results)
    message = str(exc.value)
    assert message.startswith("2 quality check(s) failed:")
    assert "- a: stale" in message
    assert "- c: empty" in message
    assert "- b" not in message
