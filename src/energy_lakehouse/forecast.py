"""Day-ahead forecast of gold_energy_hourly targets: features, baseline, metrics, SQL (no Spark).

Setup: a forecast is issued daily around 05:17 UTC on (Europe/Berlin) day D for every hour of the
local day D+1 (23/24/25 hours). Only information available at issue time may be used:

- target history: only hours that ended at or before the issue time; every lag is at least
  ``MIN_LAG_HOURS`` (48 h) before the target hour, so it lies before the issue time for all D+1
  hours. ``build_features`` raises ``LeakageError`` otherwise.
- weather: the issued forecast at scoring time (``gold_weather_forecast_hourly``, ``issued_at``
  must not be after the issue time). For training no historical forecasts exist, so observed
  weather stands in for them ("perfect prognosis"); the model is then scored on forecasts.

The notebooks (``10_train_forecast``, ``11_score_forecast``, ``12_forecast_accuracy``) turn
Spark tables into pandas, call these functions and run the rendered SQL.
"""

import platform
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, time, timedelta
from importlib import metadata

import holidays
import numpy as np
import pandas as pd

from energy_lakehouse.contracts import sql_string
from energy_lakehouse.transforms import (
    GOLD_FORECAST_HOURLY_COLUMNS,
    LOCAL_TZ,
    WEATHER_ROLE_AGGS,
    ddl_schema,
)

TARGETS: tuple[str, ...] = ("total_load_mwh", "price_eur_mwh")
# Prices can be ~0 or negative, so percentage errors are meaningless for them: MAE only.
MAPE_TARGETS: tuple[str, ...] = ("total_load_mwh",)
MAPE_MIN_ABS = 1.0  # MAPE skips hours with |actual| below this

ISSUE_TIME_UTC = time(5, 17)
MIN_LAG_HOURS = 48
LAG_HOURS: tuple[int, ...] = (48, 72, 168, 336)
ROLLING_DAYS = 7  # same-hour mean over the last 7 available days ...
ROLLING_MAX_DAYS_BACK = 14  # ... looking back at most this far (gaps are skipped)
BASELINE_LAG_HOURS = 168  # seasonal naive: same hour one week earlier
TEST_DAYS = 14

CALENDAR_FEATURES: tuple[str, ...] = ("hour_local", "dow", "month", "is_weekend", "is_holiday")
LAG_FEATURES: tuple[str, ...] = tuple(f"lag_{h}h" for h in LAG_HOURS)
ROLLING_FEATURE = "same_hour_mean_7d"
WEATHER_FEATURES: tuple[str, ...] = tuple(a.column for a in WEATHER_ROLE_AGGS)
FEATURE_COLUMNS: tuple[str, ...] = (
    *CALENDAR_FEATURES,
    *LAG_FEATURES,
    ROLLING_FEATURE,
    *WEATHER_FEATURES,
)

# HistGradientBoostingRegressor settings (fixed random_state: reproducible training runs).
MODEL_PARAMS: dict[str, object] = {
    "max_iter": 400,
    "learning_rate": 0.05,
    "max_leaf_nodes": 31,
    "l2_regularization": 1.0,
    "random_state": 42,
}
CHAMPION_ALIAS = "champion"
MIN_FORECAST_HOURS = 23  # a local day has 23, 24 or 25 hours
STAGING_VIEW = "forecast_updates"
# MLflow run tags with the library versions used for training; the pickled model only loads
# reliably where numpy and scikit-learn match (major.minor) at scoring time.
PYTHON_VERSION_TAG = "python_version"
VERSION_TAGS: dict[str, str] = {"numpy": "numpy_version", "scikit-learn": "sklearn_version"}

_HOUR = pd.Timedelta(hours=1)


class LeakageError(AssertionError):
    """A feature would use information that was not available at issue time."""


# --- time helpers --------------------------------------------------------------------------------


def to_utc(values):
    """UTC-aware timestamps from a scalar, Series or DatetimeIndex (naive values are UTC)."""
    if isinstance(values, pd.Series):
        values = pd.to_datetime(values)
        return values.dt.tz_localize("UTC") if values.dt.tz is None else values.dt.tz_convert("UTC")
    if isinstance(values, pd.DatetimeIndex):
        return values.tz_localize("UTC") if values.tz is None else values.tz_convert("UTC")
    ts = pd.Timestamp(values)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def issue_time_for(day: date) -> pd.Timestamp:
    """Nominal issue time on local day ``day`` (05:17 UTC, same calendar day in Berlin)."""
    return pd.Timestamp(f"{day.isoformat()} {ISSUE_TIME_UTC.isoformat()}", tz="UTC")


def issue_date(issue_time) -> date:
    """Local (Europe/Berlin) day D of an issue time."""
    return to_utc(issue_time).tz_convert(LOCAL_TZ).date()


def target_hours(issue_time) -> pd.DatetimeIndex:
    """UTC start of every hour of the local day after the issue day (23, 24 or 25 hours)."""
    day = issue_date(issue_time) + timedelta(days=1)
    start = pd.Timestamp(day.isoformat(), tz=LOCAL_TZ)
    end = pd.Timestamp((day + timedelta(days=1)).isoformat(), tz=LOCAL_TZ)
    return pd.date_range(start, end, freq="h", inclusive="left").tz_convert("UTC")


# --- features ------------------------------------------------------------------------------------


def _check_lags(lags: Iterable[int]) -> tuple[int, ...]:
    lags = tuple(lags)
    if too_short := [h for h in lags if h < MIN_LAG_HOURS]:
        raise LeakageError(f"lags {too_short} are shorter than MIN_LAG_HOURS={MIN_LAG_HOURS}")
    return lags


def _hourly_series(df: pd.DataFrame, column: str) -> pd.Series:
    """``column`` indexed by UTC ``hour_utc``: non-null values, one per hour (last wins), sorted."""
    s = pd.Series(df[column].to_numpy(dtype=float), index=to_utc(pd.DatetimeIndex(df["hour_utc"])))
    s = s[~s.index.duplicated(keep="last")].dropna()
    return s.sort_index()


def available_history(history_df: pd.DataFrame, issue_time) -> pd.DataFrame:
    """Rows of hours that had ended at ``issue_time`` (``hour_utc + 1 h <= issue_time``)."""
    hours = to_utc(pd.to_datetime(history_df["hour_utc"]))
    return history_df.loc[(hours + _HOUR <= to_utc(issue_time)).to_numpy()]


def german_holidays(years: Iterable[int]) -> set[date]:
    """German nationwide public holidays (``holidays`` package, country DE, no subdivision)."""
    return set(holidays.country_holidays("DE", years=sorted(set(years))))


def calendar_features(hours_utc: pd.DatetimeIndex) -> pd.DataFrame:
    """Local-time calendar features (all int64) for UTC hours."""
    local = to_utc(hours_utc).tz_convert(LOCAL_TZ)
    holiday_dates = german_holidays(local.year)
    dow = local.day_of_week.to_numpy()
    return pd.DataFrame(
        {
            "hour_local": local.hour.to_numpy(),
            "dow": dow,
            "month": local.month.to_numpy(),
            "is_weekend": dow >= 5,
            "is_holiday": [d in holiday_dates for d in local.date],
        }
    ).astype("int64")


def same_hour_mean(
    series: pd.Series,
    hours: pd.DatetimeIndex,
    days: int = ROLLING_DAYS,
    max_days_back: int = ROLLING_MAX_DAYS_BACK,
) -> np.ndarray:
    """Mean of the same hour over the last ``days`` available days, the newest >= 48 h back.

    Candidates are ``t - 24 h * k`` for k = 2 .. max_days_back; missing days are skipped and the
    newest ``days`` values are averaged (NaN if none).
    """
    first_k = MIN_LAG_HOURS // 24
    offsets = range(first_k, max_days_back + 1)
    matrix = np.column_stack(
        [series.reindex(hours - pd.Timedelta(hours=24 * k)).to_numpy() for k in offsets]
    )
    available = ~np.isnan(matrix)
    keep = available & (np.cumsum(available, axis=1) <= days)
    counts = keep.sum(axis=1)
    sums = np.where(keep, matrix, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)


def _weather_features(weather_df: pd.DataFrame | None, hours, issue_time) -> pd.DataFrame:
    """Role-aggregated weather for ``hours``; NaN where missing.

    If ``weather_df`` has ``issued_at`` (a forecast table), every row used must have been issued
    at or before ``issue_time``.
    """
    out = pd.DataFrame(index=range(len(hours)), columns=list(WEATHER_FEATURES), dtype=float)
    if weather_df is None or weather_df.empty:
        return out
    w = weather_df.copy()
    w.index = to_utc(pd.DatetimeIndex(w["hour_utc"]))
    w = w[~w.index.duplicated(keep="last")].reindex(hours)
    if "issued_at" in w.columns:
        issued = to_utc(pd.to_datetime(w["issued_at"])).dropna()
        if (issued > to_utc(issue_time)).any():
            raise LeakageError(f"weather issued after the issue time {issue_time}")
    for column in WEATHER_FEATURES:
        if column in w.columns:
            out[column] = w[column].to_numpy(dtype=float)
    return out


def build_features(
    history_df: pd.DataFrame,
    weather_df: pd.DataFrame | None,
    issue_time,
    target: str,
    lags: Sequence[int] = LAG_HOURS,
) -> pd.DataFrame:
    """Feature rows for every hour of local day D+1, using only data available at ``issue_time``.

    ``history_df``: ``hour_utc`` + ``target`` (e.g. gold_energy_weather_hourly). ``weather_df``:
    ``hour_utc`` + ``WEATHER_FEATURES`` (+ optional ``issued_at``) covering the target hours.
    Returns ``issue_time_utc, issue_date, target_hour_utc, target_hour_local`` + FEATURE_COLUMNS.
    """
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {TARGETS}")
    lags = _check_lags(lags)
    issue_time = to_utc(issue_time)
    hours = target_hours(issue_time)

    # Leakage guard 1: every lagged source hour (incl. the rolling mean's, >= MIN_LAG back) ended
    # before the issue time.
    for lag in (*lags, MIN_LAG_HOURS):
        newest_source_end = hours.max() - pd.Timedelta(hours=lag) + _HOUR
        if newest_source_end > issue_time:
            raise LeakageError(f"lag {lag} h reaches past the issue time {issue_time}")

    history = available_history(history_df, issue_time)
    series = _hourly_series(history, target)
    # Leakage guard 2: nothing newer than the issue time is left in the history used.
    if not series.empty and series.index.max() + _HOUR > issue_time:
        raise LeakageError("history contains hours after the issue time")

    frame = pd.DataFrame(
        {
            "issue_time_utc": issue_time,
            "issue_date": issue_date(issue_time),
            "target_hour_utc": hours,
            "target_hour_local": hours.tz_convert(LOCAL_TZ).tz_localize(None),
        }
    )
    frame = pd.concat([frame, calendar_features(hours)], axis=1)
    for lag, name in zip(lags, (f"lag_{h}h" for h in lags), strict=True):
        frame[name] = series.reindex(hours - pd.Timedelta(hours=lag)).to_numpy()
    frame[ROLLING_FEATURE] = same_hour_mean(series, hours)
    weather = _weather_features(weather_df, hours, issue_time)
    for column in WEATHER_FEATURES:
        frame[column] = weather[column].to_numpy(dtype=float)
    return frame


def build_training_set(
    history_df: pd.DataFrame,
    target: str,
    weather_df: pd.DataFrame | None = None,
    issue_dates: Iterable[date] | None = None,
) -> pd.DataFrame:
    """``build_features`` for every issue day plus the actual value ``y``; rows without y dropped.

    ``weather_df`` defaults to ``history_df`` (observed weather as a stand-in for the forecast).
    Default issue days: from 15 days after the first hour (all lags can exist) to the last day
    whose D+1 has actuals.
    """
    weather_df = history_df if weather_df is None else weather_df
    actual = _hourly_series(history_df, target)
    if actual.empty:
        raise ValueError(f"no {target} values in the history")
    if issue_dates is None:
        local = actual.index.tz_convert(LOCAL_TZ)
        first = local.min().date() + timedelta(days=max(LAG_HOURS) // 24 + 1)
        last = local.max().date() - timedelta(days=1)
        issue_dates = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    frames = [
        build_features(history_df, weather_df, issue_time_for(d), target) for d in issue_dates
    ]
    if not frames:
        raise ValueError("not enough history for a single training day")
    data = pd.concat(frames, ignore_index=True)
    data["y"] = actual.reindex(pd.DatetimeIndex(data["target_hour_utc"])).to_numpy()
    return data.dropna(subset=["y"]).reset_index(drop=True)


# --- baseline, metrics, split --------------------------------------------------------------------


def seasonal_naive(
    history_df: pd.DataFrame,
    target: str,
    lag_hours: int = BASELINE_LAG_HOURS,
    hours=None,
) -> pd.Series:
    """Baseline: the value ``lag_hours`` earlier, for ``hours`` (default: the history's hours)."""
    _check_lags([lag_hours])
    series = _hourly_series(history_df, target)
    hours = series.index if hours is None else to_utc(pd.DatetimeIndex(hours))
    values = series.reindex(hours - pd.Timedelta(hours=lag_hours)).to_numpy()
    return pd.Series(values, index=hours, name=f"seasonal_naive_{lag_hours}h")


def _pairs(y_true, y_pred) -> tuple[np.ndarray, np.ndarray]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    ok = ~(np.isnan(y_true) | np.isnan(y_pred))
    return y_true[ok], y_pred[ok]


def mae(y_true, y_pred) -> float:
    """Mean absolute error over pairs where both values exist (NaN if none)."""
    y_true, y_pred = _pairs(y_true, y_pred)
    return float(np.mean(np.abs(y_pred - y_true))) if len(y_true) else float("nan")


def mape(y_true, y_pred, min_abs: float = MAPE_MIN_ABS) -> float:
    """Mean absolute percentage error in %, skipping hours with ``|y_true| < min_abs``."""
    y_true, y_pred = _pairs(y_true, y_pred)
    keep = np.abs(y_true) >= min_abs
    if not keep.any():
        return float("nan")
    return float(100 * np.mean(np.abs(y_pred[keep] - y_true[keep]) / np.abs(y_true[keep])))


def evaluate(y_true, y_pred, target: str) -> dict[str, float]:
    """``{"mae": ...}`` plus ``"mape"`` for targets in MAPE_TARGETS (not for price)."""
    metrics = {"mae": mae(y_true, y_pred)}
    if target in MAPE_TARGETS:
        metrics["mape"] = mape(y_true, y_pred)
    return metrics


def time_split(
    df: pd.DataFrame, test_days: int = TEST_DAYS, date_col: str = "issue_date"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split: the last ``test_days`` distinct ``date_col`` days form the test set."""
    days = sorted(pd.unique(df[date_col]))
    if len(days) <= test_days:
        raise ValueError(f"need more than {test_days} days, got {len(days)}")
    first_test = days[-test_days]
    is_test = (df[date_col] >= first_test).to_numpy()
    return df.loc[~is_test].copy(), df.loc[is_test].copy()


# --- output rows, model names, SQL ---------------------------------------------------------------


def model_name(catalog: str, schema: str, target: str) -> str:
    """Unity Catalog model name: ``<catalog>.<schema>.forecaster_<target>``."""
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}")
    return f"{catalog}.{schema}.forecaster_{target}"


def runtime_version_tags() -> dict[str, str]:
    """Python, numpy and scikit-learn versions of the current environment, keyed by tag name."""
    tags = {PYTHON_VERSION_TAG: platform.python_version()}
    tags.update({tag: metadata.version(package) for package, tag in VERSION_TAGS.items()})
    return tags


def major_minor(version: str) -> str:
    """``"1.26.4"`` -> ``"1.26"``."""
    return ".".join(version.split(".")[:2])


def version_mismatches(trained: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    """Messages for numpy/scikit-learn whose major.minor differ between training and scoring.

    A tag missing from the training run (models trained before the tags existed) is reported too,
    since compatibility is then unknown.
    """
    messages = []
    for package, tag in VERSION_TAGS.items():
        trained_version, current_version = trained.get(tag), current.get(tag)
        if not trained_version:
            messages.append(f"{package}: training version not recorded (scoring {current_version})")
        elif major_minor(trained_version) != major_minor(current_version or ""):
            messages.append(f"{package}: trained with {trained_version}, scoring {current_version}")
    return messages


def forecast_rows(
    features: pd.DataFrame, predictions, target: str, model_version: str
) -> pd.DataFrame:
    """Rows for gold_forecast_hourly (column order of GOLD_FORECAST_HOURLY_COLUMNS)."""
    rows = pd.DataFrame(
        {
            "target_hour_utc": features["target_hour_utc"].to_numpy(),
            "target_hour_local": features["target_hour_local"].to_numpy(),
            "target": target,
            "forecast": np.asarray(predictions, dtype=float),
            "model_version": str(model_version),
            "issue_time_utc": features["issue_time_utc"].to_numpy(),
            "issue_date": features["issue_date"].to_numpy(),
        }
    )
    return rows[[name for name, _, _ in GOLD_FORECAST_HOURLY_COLUMNS]]


def spark_schema(columns: Iterable[tuple[str, str, str]]) -> str:
    """Plain DDL ``name TYPE, ...`` (no comments) for spark.createDataFrame."""
    return ", ".join(f"{name} {dtype}" for name, dtype, _ in columns)


def create_table_sql(fqn: str, columns: Sequence[tuple[str, str, str]], comment: str) -> str:
    """``CREATE TABLE IF NOT EXISTS`` with column comments (+ timestampNtz feature if needed)."""
    sql = f"CREATE TABLE IF NOT EXISTS {fqn} ({ddl_schema(columns)}) COMMENT {sql_string(comment)}"
    if any(dtype.upper() == "TIMESTAMP_NTZ" for _, dtype, _ in columns):
        sql += " TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')"
    return sql


def merge_forecast_sql(fqn: str, source: str = STAGING_VIEW) -> str:
    """Idempotent upsert on (target_hour_utc, target, issue_date): re-runs replace that day."""
    return (
        f"MERGE INTO {fqn} AS t USING {source} AS s "
        "ON t.target_hour_utc = s.target_hour_utc AND t.target = s.target "
        "AND t.issue_date = s.issue_date "
        "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *"
    )


def issue_counts_sql(fqn: str, day: date) -> str:
    """Rows per target for issue_date ``day`` (``target, n_rows``)."""
    return (
        f"SELECT target, count(*) AS n_rows FROM {fqn} "
        f"WHERE issue_date = DATE '{day.isoformat()}' GROUP BY target"
    )


def scoring_problems(counts: Mapping[str, int], issues: Mapping[str, str]) -> list[str]:
    """Why a scoring run must fail: targets with an issue, or fewer than 23 rows for the day."""
    problems = [f"{target}: {reason}" for target, reason in issues.items()]
    for target in TARGETS:
        n_rows = counts.get(target, 0)
        if target not in issues and n_rows < MIN_FORECAST_HOURS:
            problems.append(f"{target}: {n_rows} rows (expected at least {MIN_FORECAST_HOURS})")
    return problems


def _mape_sql(pred: str) -> str:
    return (
        f"100 * avg(CASE WHEN abs(actual) >= {MAPE_MIN_ABS:g} "
        f"THEN abs({pred} - actual) / abs(actual) END)"
    )


def accuracy_sql(accuracy_fqn: str, forecast_fqn: str, hourly_fqn: str) -> str:
    """Rebuild gold_forecast_accuracy: per target and issue_date, model vs seasonal naive.

    Only hours with an actual value count; the baseline is the actual ``BASELINE_LAG_HOURS``
    earlier (same as ``seasonal_naive``). MAPE is NULL for targets outside MAPE_TARGETS.
    """
    stack = ", ".join(f"{sql_string(t)}, {t}" for t in TARGETS)
    mape_targets = ", ".join(sql_string(t) for t in MAPE_TARGETS)
    return f"""INSERT OVERWRITE {accuracy_fqn}
WITH actuals AS (
  SELECT hour_utc, stack({len(TARGETS)}, {stack}) AS (target, actual)
  FROM {hourly_fqn}
),
joined AS (
  SELECT f.target, f.issue_date, f.forecast, a.actual, b.actual AS baseline
  FROM {forecast_fqn} AS f
  JOIN actuals AS a ON a.hour_utc = f.target_hour_utc AND a.target = f.target
  LEFT JOIN actuals AS b
    ON b.hour_utc = f.target_hour_utc - INTERVAL {BASELINE_LAG_HOURS} HOURS AND b.target = f.target
  WHERE a.actual IS NOT NULL
)
SELECT
  target,
  issue_date,
  count(*) AS n_hours,
  avg(abs(forecast - actual)) AS mae,
  CASE WHEN target IN ({mape_targets}) THEN {_mape_sql("forecast")} END AS mape,
  avg(abs(baseline - actual)) AS baseline_mae,
  CASE WHEN target IN ({mape_targets}) THEN {_mape_sql("baseline")} END AS baseline_mape
FROM joined
GROUP BY target, issue_date"""
