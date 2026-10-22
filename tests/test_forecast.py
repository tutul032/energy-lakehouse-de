"""Offline tests for energy_lakehouse.forecast (small synthetic data, no Spark, no MLflow)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from energy_lakehouse import forecast, transforms
from energy_lakehouse.forecast import LeakageError

TARGET = "total_load_mwh"
ISSUE_DAY = date(2026, 10, 20)
ISSUE = forecast.issue_time_for(ISSUE_DAY)  # 2026-10-20 05:17 UTC
SENTINEL = 1e9


def hourly(start: str, end: str, value=None) -> pd.DataFrame:
    """Synthetic gold_energy_weather_hourly: value = hours since start (unique, easy lag math)."""
    hours = pd.date_range(start, end, freq="h", tz="UTC", inclusive="left")
    n = np.arange(len(hours), dtype=float)
    df = pd.DataFrame({"hour_utc": hours, TARGET: n if value is None else value})
    df["price_eur_mwh"] = 50 + n % 24
    for i, column in enumerate(forecast.WEATHER_FEATURES):
        df[column] = 10.0 + i
    return df


HISTORY = hourly("2026-09-01", "2026-10-23")


def value_at(df: pd.DataFrame, ts) -> float:
    return float(df.loc[df["hour_utc"] == ts, TARGET].iloc[0])


# --- target hours / calendar ---------------------------------------------------------------------


def test_issue_time_and_target_hours_cover_local_next_day():
    assert ISSUE == pd.Timestamp("2026-10-20 05:17", tz="UTC")
    hours = forecast.target_hours(ISSUE)
    assert len(hours) == 24
    assert hours[0] == pd.Timestamp("2026-10-20 22:00", tz="UTC")  # 00:00 CEST on 21 Oct
    assert hours[-1] == pd.Timestamp("2026-10-21 21:00", tz="UTC")


@pytest.mark.parametrize(("day", "n_hours"), [(date(2026, 10, 24), 25), (date(2026, 3, 28), 23)])
def test_target_hours_dst_days(day, n_hours):
    assert len(forecast.target_hours(forecast.issue_time_for(day))) == n_hours


def test_holiday_flag_set_for_christmas_2026():
    hours = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-12-25 10:00", tz="Europe/Berlin"),
            pd.Timestamp("2026-12-23 10:00", tz="Europe/Berlin"),
        ]
    ).tz_convert("UTC")
    cal = forecast.calendar_features(hours)
    assert cal["is_holiday"].tolist() == [1, 0]
    assert cal.loc[0, ["hour_local", "dow", "month", "is_weekend"]].tolist() == [10, 4, 12, 0]
    assert (cal.dtypes == "int64").all()


def test_build_features_flags_every_hour_of_christmas_day():
    history = hourly("2026-11-01", "2026-12-25")
    feats = forecast.build_features(
        history, None, forecast.issue_time_for(date(2026, 12, 24)), TARGET
    )
    assert len(feats) == 24
    assert feats["is_holiday"].eq(1).all()
    assert feats["target_hour_local"].dt.date.eq(date(2026, 12, 25)).all()


# --- lags and leakage ----------------------------------------------------------------------------


def test_feature_lags_are_at_least_min_lag():
    assert forecast.MIN_LAG_HOURS == 48
    assert min(forecast.LAG_HOURS) >= forecast.MIN_LAG_HOURS
    assert forecast.LAG_FEATURES == ("lag_48h", "lag_72h", "lag_168h", "lag_336h")
    assert set(forecast.LAG_FEATURES) <= set(forecast.FEATURE_COLUMNS)
    with pytest.raises(LeakageError):
        forecast.build_features(HISTORY, None, ISSUE, TARGET, lags=(24, 48))
    with pytest.raises(LeakageError):
        forecast.seasonal_naive(HISTORY, TARGET, lag_hours=24)


def test_lag_values_match_history():
    feats = forecast.build_features(HISTORY, HISTORY, ISSUE, TARGET)
    for lag in forecast.LAG_HOURS:
        expected = [
            value_at(HISTORY, t - pd.Timedelta(hours=lag)) for t in feats["target_hour_utc"]
        ]
        assert feats[f"lag_{lag}h"].tolist() == expected


def test_no_feature_uses_data_after_issue_time():
    # Every hour that had not ended at issue time carries a sentinel; none may show up.
    history = HISTORY.copy()
    late = (history["hour_utc"] + pd.Timedelta(hours=1)) > ISSUE
    history.loc[late, TARGET] = SENTINEL
    feats = forecast.build_features(history, history, ISSUE, TARGET)
    numeric = feats[list(forecast.FEATURE_COLUMNS)].to_numpy(dtype=float)
    assert not (numeric >= SENTINEL).any()
    assert feats["lag_48h"].notna().all()  # still populated from available history
    used = forecast.available_history(history, ISSUE)
    assert (pd.to_datetime(used["hour_utc"]) + pd.Timedelta(hours=1)).max() <= ISSUE


def test_training_rows_have_no_leakage_and_true_targets():
    data = forecast.build_training_set(HISTORY, TARGET)
    assert len(data) > 0
    # Every feature row: issue time before all of its target hours and lag sources.
    assert (data["target_hour_utc"] > data["issue_time_utc"]).all()
    newest_source = data["target_hour_utc"] - pd.Timedelta(hours=forecast.MIN_LAG_HOURS)
    assert (newest_source + pd.Timedelta(hours=1) <= data["issue_time_utc"]).all()
    sample = data.iloc[len(data) // 2]
    assert sample["y"] == value_at(HISTORY, sample["target_hour_utc"])
    assert sample["lag_168h"] == sample["y"] - 168  # synthetic series: value = hour index


def test_weather_issued_after_issue_time_is_rejected():
    hours = forecast.target_hours(ISSUE)
    weather = pd.DataFrame({"hour_utc": hours, "issued_at": ISSUE + pd.Timedelta(hours=1)})
    for column in forecast.WEATHER_FEATURES:
        weather[column] = 1.0
    with pytest.raises(LeakageError):
        forecast.build_features(HISTORY, weather, ISSUE, TARGET)
    weather["issued_at"] = ISSUE - pd.Timedelta(minutes=5)
    feats = forecast.build_features(HISTORY, weather, ISSUE, TARGET)
    assert feats[list(forecast.WEATHER_FEATURES)].eq(1.0).all().all()


def test_missing_weather_gives_nan_not_error():
    feats = forecast.build_features(HISTORY, None, ISSUE, TARGET)
    assert feats[list(forecast.WEATHER_FEATURES)].isna().all().all()


def test_build_features_rejects_unknown_target():
    with pytest.raises(ValueError):
        forecast.build_features(HISTORY, None, ISSUE, "solar_mwh")


def test_build_features_accepts_naive_utc_timestamps():
    naive = HISTORY.assign(hour_utc=HISTORY["hour_utc"].dt.tz_localize(None))
    a = forecast.build_features(naive, None, ISSUE.tz_localize(None), TARGET)
    b = forecast.build_features(HISTORY, None, ISSUE, TARGET)
    pd.testing.assert_frame_equal(a, b)


# --- rolling same-hour mean ----------------------------------------------------------------------


def test_same_hour_mean_last_7_available_days_skipping_gaps():
    t = pd.Timestamp("2026-10-21 12:00", tz="UTC")
    days_back = range(2, 15)
    values = {t - pd.Timedelta(days=k): float(k) for k in days_back}
    for k in (3, 5):  # gaps: these days are missing
        del values[t - pd.Timedelta(days=k)]
    series = pd.Series(values).sort_index()
    result = forecast.same_hour_mean(series, pd.DatetimeIndex([t]))
    assert result[0] == pytest.approx(np.mean([2, 4, 6, 7, 8, 9, 10]))
    assert np.isnan(forecast.same_hour_mean(series.iloc[:0], pd.DatetimeIndex([t]))[0])


def test_rolling_feature_ignores_last_two_days():
    t = pd.Timestamp("2026-10-21 12:00", tz="UTC")
    series = pd.Series({t - pd.Timedelta(days=1): SENTINEL, t - pd.Timedelta(days=2): 5.0})
    assert forecast.same_hour_mean(series, pd.DatetimeIndex([t]))[0] == 5.0


# --- baseline, metrics, split --------------------------------------------------------------------


def test_seasonal_naive_is_value_one_week_earlier():
    hours = pd.DatetimeIndex([pd.Timestamp("2026-10-15 00:00", tz="UTC")])
    baseline = forecast.seasonal_naive(HISTORY, TARGET, hours=hours)
    assert baseline.iloc[0] == value_at(HISTORY, hours[0] - pd.Timedelta(hours=168))
    full = forecast.seasonal_naive(HISTORY, TARGET)
    assert full.iloc[:168].isna().all()
    assert (full.iloc[168:].to_numpy() == np.arange(len(HISTORY) - 168)).all()


def test_metrics():
    y = [100.0, 200.0, 0.5, np.nan]
    p = [110.0, 180.0, 1.5, 5.0]
    assert forecast.mae(y, p) == pytest.approx((10 + 20 + 1) / 3)
    assert forecast.mape(y, p) == pytest.approx(100 * (0.1 + 0.1) / 2)  # |y| < 1 skipped
    assert np.isnan(forecast.mae([np.nan], [1.0]))
    assert np.isnan(forecast.mape([0.2], [1.0]))
    assert forecast.evaluate(y, p, "total_load_mwh").keys() == {"mae", "mape"}
    assert forecast.evaluate(y, p, "price_eur_mwh").keys() == {"mae"}


def test_time_split_is_chronological():
    data = forecast.build_training_set(HISTORY, TARGET)
    train, test = forecast.time_split(data, test_days=14)
    assert test["issue_date"].nunique() == 14
    assert train["issue_date"].max() < test["issue_date"].min()
    assert train["target_hour_utc"].max() < test["target_hour_utc"].min()
    assert len(train) + len(test) == len(data)
    with pytest.raises(ValueError):
        forecast.time_split(data, test_days=data["issue_date"].nunique())


# --- output rows and SQL -------------------------------------------------------------------------


def test_forecast_rows_match_table_columns():
    feats = forecast.build_features(HISTORY, None, ISSUE, TARGET)
    rows = forecast.forecast_rows(feats, np.arange(len(feats)), TARGET, 3)
    assert list(rows.columns) == [n for n, _, _ in transforms.GOLD_FORECAST_HOURLY_COLUMNS]
    assert rows["model_version"].eq("3").all()
    assert rows["issue_date"].eq(ISSUE_DAY).all()
    assert rows["target_hour_local"].dt.tz is None


def test_model_name():
    assert forecast.model_name("workspace", "energy_dev", "price_eur_mwh") == (
        "workspace.energy_dev.forecaster_price_eur_mwh"
    )
    with pytest.raises(ValueError):
        forecast.model_name("workspace", "energy_dev", "solar_mwh")


def test_create_table_sql_enables_timestamp_ntz():
    sql = forecast.create_table_sql(
        "`c`.`s`.`gold_forecast_hourly`", transforms.GOLD_FORECAST_HOURLY_COLUMNS, "It's a test."
    )
    assert sql.startswith("CREATE TABLE IF NOT EXISTS `c`.`s`.`gold_forecast_hourly` (")
    assert "target_hour_local TIMESTAMP_NTZ COMMENT" in sql
    assert "COMMENT 'It\\'s a test.'" in sql
    assert sql.endswith("TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')")
    plain = forecast.create_table_sql("t", transforms.GOLD_FORECAST_ACCURACY_COLUMNS, "x")
    assert "TBLPROPERTIES" not in plain


def test_merge_sql_keys():
    sql = forecast.merge_forecast_sql("`c`.`s`.`gold_forecast_hourly`")
    assert "USING forecast_updates AS s" in sql
    for key in ("target_hour_utc", "target", "issue_date"):
        assert f"t.{key} = s.{key}" in sql
    assert sql.endswith("WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")


def test_accuracy_sql_uses_baseline_lag_and_mape_targets():
    sql = forecast.accuracy_sql("acc", "fc", "hourly")
    assert sql.startswith("INSERT OVERWRITE acc\n")
    assert "stack(2, 'total_load_mwh', total_load_mwh, 'price_eur_mwh', price_eur_mwh)" in sql
    assert "INTERVAL 168 HOURS" in sql
    assert "WHEN target IN ('total_load_mwh') THEN 100 * avg(" in sql
    assert "abs(actual) >= 1" in sql
    names = [n for n, _, _ in transforms.GOLD_FORECAST_ACCURACY_COLUMNS]
    select = sql.split("SELECT\n")[-1]
    positions = [select.index(f" {n}") if n not in ("target",) else 0 for n in names]
    assert positions == sorted(positions)  # output columns in table order


def test_spark_schema_plain_ddl():
    assert forecast.spark_schema([("a", "DATE", "x"), ("b", "DOUBLE", "y")]) == "a DATE, b DOUBLE"
