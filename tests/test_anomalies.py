"""Offline tests for energy_lakehouse.anomalies (synthetic data, no Spark)."""

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from energy_lakehouse import anomalies as an
from energy_lakehouse.transforms import GOLD_DQ_ANOMALIES_COLUMNS, LOCAL_TZ

NOW = pd.Timestamp("2026-10-20 05:17", tz="UTC")


def weekly_series(entity="total_load", weeks=9, end=NOW, level=0.02, phase=0.0) -> pd.DataFrame:
    """Hourly load-like series: daily + weekend pattern (local time), weekly level +-``level``."""
    hours = pd.date_range(end=end.floor("h") - pd.Timedelta(hours=1), periods=weeks * 168, freq="h")
    local = hours.tz_convert(LOCAL_TZ)
    hour = local.hour.to_numpy()
    base = 55000 + 10000 * np.sin(2 * np.pi * (hour - 6) / 24) - 6000 * (local.day_of_week >= 5)
    week = np.arange(len(hours)) // 168
    factor = 1 + level * np.sin(2.4 * week + phase)
    return pd.DataFrame({"entity": entity, "ts_utc": hours, "value": base * factor})


def score_from(df: pd.DataFrame) -> pd.Timestamp:
    return df["ts_utc"].max() - pd.Timedelta(days=an.SCORE_DAYS) + pd.Timedelta(hours=1)


def assert_columns(df: pd.DataFrame):
    assert tuple(df.columns) == an.ANOMALY_COLUMNS
    assert set(df["severity"]) <= {an.CRITICAL, an.WARNING}
    assert (df["score"] >= 0).all()


# --- (a) seasonal robust z ----------------------------------------------------------------------


def test_seasonal_z_flags_clear_spike_critical():
    df = weekly_series()
    last = df.index[-1]
    df.loc[last, "value"] *= 3
    rows = an.seasonal_robust_z(df, "value", score_from=score_from(df))
    assert_columns(rows)
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["severity"] == an.CRITICAL
    assert row["entity"] == "total_load"
    assert row["ts_utc"] == df.loc[last, "ts_utc"]
    assert row["lower"] < row["expected"] < row["upper"] < row["value"]
    assert "robust z +" in row["reason"]


def test_seasonal_z_moderate_deviation_is_warning():
    df = weekly_series()
    last = df.index[-1]
    history = df["value"].to_numpy()[last - 168 * 8 : last : 168]
    median = np.median(history)
    mad = np.median(np.abs(history - median))
    df.loc[last, "value"] = median + 6 * mad / an.MAD_TO_Z  # z = 6: warning, not critical
    rows = an.seasonal_robust_z(df, "value", score_from=score_from(df))
    assert list(rows["severity"]) == [an.WARNING]
    assert rows.iloc[0]["score"] == pytest.approx(6.0)


def test_seasonal_z_normal_weekly_pattern_gives_no_rows():
    df = pd.concat(
        [weekly_series(), weekly_series("solar", level=0.05, phase=1.0)], ignore_index=True
    )
    rows = an.seasonal_robust_z(df, "value", score_from=score_from(df))
    assert rows.empty
    assert tuple(rows.columns) == an.ANOMALY_COLUMNS


def test_seasonal_z_skips_mad_zero():
    df = weekly_series()
    df["value"] = 1000.0  # constant history: MAD == 0
    df.loc[df.index[-1], "value"] = 99999.0
    assert an.seasonal_robust_z(df, "value", score_from=score_from(df)).empty


def test_seasonal_z_skips_short_history():
    df = weekly_series(weeks=4)  # 3 weeks of history before the scored week (< 5)
    df.loc[df.index[-1], "value"] *= 3
    assert an.seasonal_robust_z(df, "value", score_from=score_from(df)).empty


def test_seasonal_z_excludes_current_point_from_history():
    df = weekly_series()
    df.loc[df.index[-1], "value"] *= 3
    rows = an.seasonal_robust_z(df, "value", score_from=score_from(df))
    # Median of the 8 previous weeks, not including the spike itself.
    assert rows.iloc[0]["expected"] < 80000


def test_inject_test_anomaly_marks_copy_and_is_flagged():
    df = weekly_series()
    injected = an.inject_test_anomaly(df, NOW)
    assert (injected["entity"] == "total_load").sum() == len(df)
    test = injected[injected["entity"] == "TEST:total_load"]
    assert len(test) == len(df)
    latest = df["ts_utc"].max()
    original = df.loc[df["ts_utc"] == latest, "value"].iloc[0]
    assert test.loc[test["ts_utc"] == latest, "value"].iloc[0] == pytest.approx(original * 0.5)
    rows = an.seasonal_robust_z(injected, "value", score_from=score_from(df))
    assert list(rows["entity"]) == ["TEST:total_load"]
    assert list(rows["severity"]) == [an.CRITICAL]
    assert an.critical_failures(rows) == []


def test_inject_test_anomaly_uses_complete_hour_only():
    df = weekly_series(end=NOW + pd.Timedelta(hours=2))  # contains an hour not yet complete
    injected = an.inject_test_anomaly(df, NOW)
    test = injected[injected["entity"] == "TEST:total_load"].set_index("ts_utc")["value"]
    real = df.set_index("ts_utc")["value"]
    changed = test.index[test.to_numpy() != real.to_numpy()]
    assert list(changed) == [NOW.floor("h") - pd.Timedelta(hours=1)]


def test_inject_test_anomaly_without_data_raises():
    with pytest.raises(ValueError, match="no complete hour"):
        an.inject_test_anomaly(weekly_series("solar"), NOW)


# --- (b) isolation forest -----------------------------------------------------------------------


@pytest.fixture
def sklearn_available():
    """Skip locally where scikit-learn cannot load (Smart App Control); never skip in CI."""
    try:
        import sklearn.ensemble  # noqa: F401
    except ImportError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"scikit-learn not loadable here: {exc}")


def gold_year(days=372, seed=7) -> pd.DataFrame:
    """Hourly gold-like frame: load, solar, wind and prices driven by hour, sun and wind speed."""
    hours = pd.date_range(end=NOW.floor("h") - pd.Timedelta(hours=1), periods=days * 24, freq="h")
    local = hours.tz_convert(LOCAL_TZ)
    hour = local.hour.to_numpy()
    rng = np.random.default_rng(seed)
    n = len(hours)
    sun = np.clip(np.sin(2 * np.pi * (hour - 6) / 24), 0, None)
    radiation = 600 * sun * rng.uniform(0.5, 1.0, n)
    wind_speed = rng.uniform(2, 14, n)
    load = 55000 + 10000 * np.sin(2 * np.pi * (hour - 6) / 24) + rng.normal(0, 1000, n)
    wind = 2000 * wind_speed + rng.normal(0, 1000, n)
    solar = 50 * radiation + rng.uniform(0, 100, n)
    residual = load - wind - solar
    return pd.DataFrame(
        {
            "hour_utc": hours,
            "hour_local": local.tz_localize(None),
            "total_load_mwh": load,
            "price_eur_mwh": 40 + residual / 500 + rng.normal(0, 5, n),
            "solar_mwh": solar,
            "wind_onshore_mwh": wind,
            "wind_offshore_mwh": 400 * wind_speed + rng.normal(0, 300, n),
            "lignite_mwh": 5000 + 0.15 * residual,
            "natural_gas_mwh": 3000 + 0.1 * residual,
            "wind_speed_100m_onshore_avg_ms": wind_speed,
            "wind_speed_100m_offshore_ms": wind_speed * 1.2 + rng.uniform(0, 1, n),
            "shortwave_radiation_solar_avg_wm2": radiation,
        }
    )


def test_add_hour_features_cyclic():
    df = pd.DataFrame({"hour_local": pd.to_datetime(["2026-10-20 00:00", "2026-10-20 06:00"])})
    out = an.add_hour_features(df)
    assert out["hour_sin"].tolist() == pytest.approx([0.0, 1.0])
    assert out["hour_cos"].tolist() == pytest.approx([1.0, 0.0], abs=1e-12)


@pytest.mark.parametrize("seed", [7, 8, 9])
def test_isolation_forest_flags_impossible_combination(sklearn_available, seed):
    gold = gold_year(seed=seed)
    start = gold["hour_utc"].max() - pd.Timedelta(days=an.SCORE_DAYS) + pd.Timedelta(hours=1)
    night = gold.index[(gold["hour_utc"] >= start) & (gold["hour_local"].dt.hour == 2)][2]
    # Daytime solar output at 02:00 local with zero radiation: impossible.
    gold.loc[night, ["solar_mwh", "shortwave_radiation_solar_avg_wm2"]] = [30000.0, 0.0]
    rows = an.multivariate_isolation_forest(gold, score_from=start)
    assert_columns(rows)
    assert set(rows["severity"]) == {an.WARNING}
    assert len(rows) < 0.05 * 168  # contamination 0.5 %: a few of the 168 hours, not many
    flagged = rows.set_index("ts_utc").loc[gold.loc[night, "hour_utc"]]
    assert flagged["reason"].startswith("unusual combination: solar_mwh z=+")
    assert flagged["reason"].count("z=") == an.IF_TOP_FEATURES
    assert flagged["value"] > flagged["upper"]


def test_hourly_robust_z_uses_same_hour_median():
    x_train = np.array([[0.0], [1.0], [2.0], [100.0], [110.0], [120.0]])
    hours_train = np.array([2, 2, 2, 12, 12, 12])
    z = an.hourly_robust_z(x_train, hours_train, np.array([[100.0], [110.0]]), np.array([2, 12]))
    assert z[0, 0] == pytest.approx(an.MAD_TO_Z * 99)  # night: median 1, MAD 1
    assert z[1, 0] == pytest.approx(0.0)  # noon: at the noon median


def test_hourly_robust_z_falls_back_when_mad_zero():
    x_train = np.array([[0.0], [0.0], [0.0], [10.0], [20.0], [30.0]])
    hours_train = np.array([2, 2, 2, 12, 12, 12])
    z = an.hourly_robust_z(x_train, hours_train, np.array([[5.0]]), np.array([2]))
    overall_sigma = np.median(np.abs(x_train - np.median(x_train))) / an.MAD_TO_Z
    assert z[0, 0] == pytest.approx(5.0 / overall_sigma)


def test_isolation_forest_needs_training_rows():
    gold = gold_year(days=10)
    with pytest.raises(ValueError, match="training hours"):
        an.multivariate_isolation_forest(gold)


def test_isolation_forest_drops_rows_with_missing_features():
    gold = gold_year(days=40)
    gold.loc[gold.index[-24:], "shortwave_radiation_solar_avg_wm2"] = np.nan
    rows = an.multivariate_isolation_forest(gold, score_from=gold["hour_utc"].iloc[-24])
    assert rows.empty


# --- (c) completeness ---------------------------------------------------------------------------


def hours_of_day(day: date) -> pd.DatetimeIndex:
    start = an.local_day_start(day)
    return pd.date_range(start, periods=an.expected_hours(day), freq="h")


def day_frame(entity: str, day: date, drop: int = 0) -> pd.DataFrame:
    hours = hours_of_day(day)
    hours = hours[: len(hours) - drop]
    return pd.DataFrame({"entity": entity, "ts_utc": hours, "value": 1.0})


def test_expected_hours_dst():
    assert an.expected_hours(date(2026, 3, 29)) == 23
    assert an.expected_hours(date(2026, 10, 25)) == 25
    assert an.expected_hours(date(2026, 10, 20)) == 24


def test_completeness_dst_days_complete_give_no_rows():
    df = pd.concat(
        [day_frame("total_load", date(2026, 3, 29)), day_frame("total_load", date(2026, 10, 25))]
    )
    assert an.completeness(df, today=date(2026, 3, 30), days=1).empty
    assert an.completeness(df, today=date(2026, 10, 26), days=1).empty


def test_completeness_dst_day_expects_23_and_25_hours():
    df = pd.concat(
        [
            day_frame("total_load", date(2026, 3, 29), drop=2),
            day_frame("total_load", date(2026, 10, 25), drop=2),
        ]
    )
    spring = an.completeness(df, today=date(2026, 3, 30), days=1).iloc[0]
    autumn = an.completeness(df, today=date(2026, 10, 26), days=1).iloc[0]
    assert (spring["value"], spring["expected"]) == (21, 23)  # 8.7 % missing
    assert (autumn["value"], autumn["expected"]) == (23, 25)  # 8.0 % missing
    assert spring["severity"] == autumn["severity"] == an.WARNING
    assert spring["ts_utc"] == pd.Timestamp("2026-03-28 23:00", tz="UTC")


@pytest.mark.parametrize(
    ("missing", "severity"),
    [(0, None), (1, None), (2, an.WARNING), (4, an.WARNING), (5, an.CRITICAL), (24, an.CRITICAL)],
)
def test_completeness_thresholds(missing, severity):
    day = date(2026, 10, 19)
    df = day_frame("solar", day, drop=missing)
    rows = an.completeness(df, today=day + timedelta(days=1), days=1, entities=["solar"])
    if severity is None:
        assert rows.empty
    else:
        assert list(rows["severity"]) == [severity]
        assert rows.iloc[0]["score"] == pytest.approx(100 * missing / 24)


def test_completeness_null_values_and_absent_series_count_as_missing():
    day = date(2026, 10, 19)
    df = day_frame("solar", day)
    df.loc[df.index[:3], "value"] = np.nan
    rows = an.completeness(df, today=date(2026, 10, 20), days=1, entities=["solar", "lignite"])
    assert dict(zip(rows["entity"], rows["value"], strict=True)) == {"solar": 21, "lignite": 0}
    assert dict(zip(rows["entity"], rows["severity"], strict=True)) == {
        "solar": an.WARNING,
        "lignite": an.CRITICAL,
    }


def test_completeness_covers_complete_days_before_today_only():
    today = date(2026, 10, 20)
    df = pd.concat([day_frame("solar", today - timedelta(days=k)) for k in range(1, 8)])
    assert an.completeness(df, today=today).empty  # today itself (incomplete) is not checked
    rows = an.completeness(df, today=today + timedelta(days=1))
    assert [d.date() for d in rows["ts_utc"].dt.tz_convert(LOCAL_TZ)] == [today]


# --- (d) physics rules --------------------------------------------------------------------------


def physics_frame(solar, radiation) -> pd.DataFrame:
    hours = pd.date_range("2026-10-19 00:00", periods=len(solar), freq="h", tz="UTC")
    return pd.DataFrame(
        {"hour_utc": hours, "solar_mwh": solar, "shortwave_radiation_solar_avg_wm2": radiation}
    )


def test_solar_at_night_is_critical():
    rows = an.physics_rules(physics_frame([500.0, 0.0, 0.0], [0.0, 0.0, 0.0]))
    assert_columns(rows)
    assert list(rows["severity"]) == [an.CRITICAL]
    assert rows.iloc[0]["ts_utc"] == pd.Timestamp("2026-10-19 00:00", tz="UTC")
    assert rows.iloc[0]["reason"].startswith("solar output at night")


def test_solar_rule_allows_dawn_small_output_and_unknown_next_hour():
    # Hour 0: dawn (next-hour radiation 40), hour 1: small output, hour 2: next hour unknown.
    rows = an.physics_rules(physics_frame([500.0, 50.0, 500.0], [0.0, 40.0, 0.0]))
    assert rows.empty


# --- (e) forecast drift -------------------------------------------------------------------------


def accuracy(days, mae, baseline, mape=None, target="total_load_mwh") -> pd.DataFrame:
    start = date(2026, 10, 10)
    return pd.DataFrame(
        {
            "target": target,
            "issue_date": [start + timedelta(days=d) for d in days],
            "n_hours": 24,
            "mae": mae,
            "mape": mape if mape is not None else [np.nan] * len(days),
            "baseline_mae": baseline,
            "baseline_mape": np.nan,
        }
    )


def test_forecast_drift_three_bad_days_warn():
    rows = an.forecast_drift(accuracy([0, 1, 2], [900, 900, 900], [800, 800, 800], [3, 3, 3]))
    assert_columns(rows)
    assert list(rows["check_name"]) == [an.DRIFT_MAE]
    assert list(rows["severity"]) == [an.WARNING]
    assert rows.iloc[0]["ts_utc"] == an.local_day_start(date(2026, 10, 12))


def test_forecast_drift_two_bad_days_or_gap_no_rows():
    assert an.forecast_drift(accuracy([0, 1], [900, 900], [800, 800])).empty
    assert an.forecast_drift(accuracy([0, 1, 3], [900] * 3, [800] * 3)).empty  # day 2 missing
    assert an.forecast_drift(accuracy([0, 1, 2], [900, 700, 900], [800] * 3)).empty


def test_forecast_drift_mape_two_days_critical():
    rows = an.forecast_drift(accuracy([0, 1], [500, 500], [800, 800], [7.0, 6.5]))
    assert list(rows["check_name"]) == [an.DRIFT_MAPE]
    assert list(rows["severity"]) == [an.CRITICAL]
    assert rows.iloc[0]["upper"] == 6.0


def test_forecast_drift_since_keeps_streaks_reports_recent_only():
    df = accuracy([0, 1, 2, 3, 4], [900] * 5, [800] * 5)
    rows = an.forecast_drift(df, since=date(2026, 10, 13))
    assert [t.date() for t in rows["ts_utc"].dt.tz_convert(LOCAL_TZ)] == [
        date(2026, 10, 13),
        date(2026, 10, 14),
    ]
    assert rows.iloc[0]["reason"].startswith("model MAE above seasonal naive baseline on 4")


def test_forecast_drift_price_without_mape_and_empty_input():
    price = accuracy([0, 1, 2], [9, 9, 9], [10, 10, 10], target="price_eur_mwh")
    assert an.forecast_drift(price).empty
    assert an.forecast_drift(price.iloc[0:0]).empty
    assert an.forecast_drift(None).empty


# --- results, summary, SQL ----------------------------------------------------------------------


def sample_rows() -> pd.DataFrame:
    ts = pd.Timestamp("2026-10-19 03:00", tz="UTC")
    return an.combine(
        [
            an.physics_rules(physics_frame([500.0, 0.0, 0.0], [0.0, 0.0, 0.0])),
            an._frame(
                [
                    {
                        "check_name": an.SEASONAL_Z,
                        "entity": "TEST:total_load",
                        "ts_utc": ts,
                        "value": 1.0,
                        "expected": 2.0,
                        "lower": 1.5,
                        "upper": 2.5,
                        "score": 9.0,
                        "severity": an.CRITICAL,
                        "reason": "test",
                    },
                    {
                        "check_name": an.SEASONAL_Z,
                        "entity": "solar",
                        "ts_utc": ts,
                        "value": 1.0,
                        "expected": np.nan,
                        "lower": np.nan,
                        "upper": np.nan,
                        "score": 5.0,
                        "severity": an.WARNING,
                        "reason": "x",
                    },
                ]
            ),
        ]
    )


def test_combine_dedupes_on_key_keeping_most_severe():
    rows = sample_rows()
    dup = rows.iloc[[0]].assign(severity=an.WARNING, score=0.1)
    combined = an.combine([dup, rows])
    assert len(combined) == len(rows)
    assert combined.iloc[0]["severity"] == an.CRITICAL
    assert an.combine([]).empty


def test_summary_rows_lists_every_check_with_zero_counts():
    summary = an.summary_rows(sample_rows(), date(2026, 10, 20))
    pairs = {(r.check_name, r.severity): r.n for r in summary.itertuples()}
    assert len(pairs) == sum(len(s) for s in an.CHECK_SEVERITIES.values())
    assert pairs[(an.SOLAR_AT_NIGHT, an.CRITICAL)] == 1
    assert pairs[(an.SEASONAL_Z, an.CRITICAL)] == 1
    assert pairs[(an.SEASONAL_Z, an.WARNING)] == 1
    assert pairs[(an.ISOLATION_FOREST, an.WARNING)] == 0
    assert set(summary["detection_date"]) == {date(2026, 10, 20)}
    assert an.summary_rows(an.empty_anomalies(), date(2026, 10, 20))["n"].sum() == 0


def test_critical_failures_excludes_test_entities():
    failures = an.critical_failures(sample_rows())
    assert len(failures) == 1
    assert failures[0].startswith("physics_solar_at_night solar_mwh 2026-10-19 00:00 UTC: solar")


def test_to_spark_rows_naive_utc_and_nulls():
    detected_at = pd.Timestamp("2026-10-20 05:20", tz="UTC")
    rows = an.to_spark_rows(sample_rows(), detected_at)
    assert list(rows.columns) == [name for name, _, _ in GOLD_DQ_ANOMALIES_COLUMNS]
    assert rows["ts_utc"].iloc[0].tzinfo is None
    assert rows["detected_at"].iloc[0] == pd.Timestamp("2026-10-20 05:20")
    solar = rows[rows["entity"] == "solar"].iloc[0]
    assert solar["expected"] is None and solar["lower"] is None


def test_sql_snapshots():
    fqn = "`workspace`.`energy_dev`.`gold_dq_anomalies`"
    assert an.merge_anomalies_sql(fqn) == (
        f"MERGE INTO {fqn} AS t USING dq_anomaly_updates AS s "
        "ON t.check_name = s.check_name AND t.entity = s.entity AND t.ts_utc = s.ts_utc "
        "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *"
    )
    assert an.replace_summary_sql("s", date(2026, 10, 20)) == (
        "INSERT INTO s REPLACE WHERE detection_date = DATE '2026-10-20' "
        "SELECT * FROM dq_summary_updates"
    )
    assert "WHERE detection_date = DATE '2026-10-20'" in an.summary_for_day_sql(
        "s", date(2026, 10, 20)
    )
    top = an.top_anomalies_sql("a", date(2026, 10, 20))
    assert "from_utc_timestamp(detected_at, 'Europe/Berlin')) = DATE '2026-10-20'" in top
    assert top.endswith("LIMIT 10")
    assert an.LOCAL_TODAY_SQL == (
        "to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin'))"
    )


def test_ai_query_sql_escapes_literals():
    assert an.ai_query_sql("my-endpoint", "it's") == (
        "SELECT ai_query('my-endpoint', 'it\\'s') AS summary_text"
    )


def test_llm_prompt_contains_counts_and_top_anomalies():
    summary = [{"check_name": an.COMPLETENESS, "severity": an.WARNING, "n": 2}]
    top = [
        {
            "check_name": an.COMPLETENESS,
            "entity": "solar",
            "ts_utc": "2026-10-18 22:00:00",
            "severity": an.WARNING,
            "reason": "2 of 24 hours missing",
        }
    ]
    prompt = an.llm_prompt(date(2026, 10, 20), summary, top)
    assert "Detection date (Europe/Berlin): 2026-10-20" in prompt
    assert "- completeness / warning: 2" in prompt
    assert "- [warning] completeness solar at 2026-10-18 22:00:00 UTC: 2 of 24 hours" in prompt
    assert an.llm_prompt(date(2026, 10, 20), [], []).count("- none") == 2
