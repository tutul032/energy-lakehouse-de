"""ML-assisted data quality: anomaly detectors for the energy tables (pandas, no Spark).

``20_detect_anomalies`` reads silver/gold into pandas, runs the detectors below and MERGEs their
rows into ``gold_dq_anomalies``; ``21_dq_summary_llm`` turns the day's summary into an LLM
prompt. Every detector returns a DataFrame with ``ANOMALY_COLUMNS``: check_name, entity,
ts_utc (UTC-aware), value, expected, lower, upper, score (>= 0, higher = more abnormal),
severity (``critical`` | ``warning``) and reason.

- ``seasonal_robust_z``: per entity and local hour-of-week, median/MAD of the previous weeks.
- ``multivariate_isolation_forest``: IsolationForest over the hourly gold features (warning).
- ``completeness``: present vs expected (23/24/25) hours per series and local day.
- ``physics_rules``: solar output while the solar locations report no radiation (critical).
- ``forecast_drift``: model worse than the seasonal naive baseline, or MAPE too high, on
  consecutive issue dates.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta

import numpy as np
import pandas as pd

from energy_lakehouse.contracts import sql_string
from energy_lakehouse.forecast import to_utc
from energy_lakehouse.transforms import GOLD_DQ_ANOMALIES_COLUMNS, LOCAL_TZ

CRITICAL = "critical"
WARNING = "warning"
ANOMALY_COLUMNS: tuple[str, ...] = tuple(
    name for name, _, _ in GOLD_DQ_ANOMALIES_COLUMNS if name != "detected_at"
)
KEY_COLUMNS: tuple[str, ...] = ("check_name", "entity", "ts_utc")
FLOAT_COLUMNS: tuple[str, ...] = ("value", "expected", "lower", "upper", "score")
TEST_PREFIX = "TEST:"  # entity prefix of injected test anomalies (never fail the run)

SEASONAL_Z = "seasonal_robust_z"
ISOLATION_FOREST = "isolation_forest"
COMPLETENESS = "completeness"
SOLAR_AT_NIGHT = "physics_solar_at_night"
DRIFT_MAE = "forecast_drift_mae"
DRIFT_MAPE = "forecast_drift_mape"

# Severities each check can emit; the daily summary has one row per pair (n = 0 if none found).
CHECK_SEVERITIES: dict[str, tuple[str, ...]] = {
    SEASONAL_Z: (CRITICAL, WARNING),
    ISOLATION_FOREST: (WARNING,),
    COMPLETENESS: (CRITICAL, WARNING),
    SOLAR_AT_NIGHT: (CRITICAL,),
    DRIFT_MAE: (WARNING,),
    DRIFT_MAPE: (CRITICAL,),
}

SCORE_DAYS = 7  # every detector reports the last 7 days

# Robust z = 0.6745 * (x - median) / MAD (for normal data MAD = 0.6745 sigma).
MAD_TO_Z = 0.6745
SEASONAL_WINDOW_WEEKS = 8
SEASONAL_MIN_HISTORY = 5
SEASONAL_WARN_Z = 4.0
SEASONAL_CRIT_Z = 8.0
# Negative or spiky prices are often real: prices are checked by the isolation forest only.
SEASONAL_EXCLUDED_SERIES: tuple[str, ...] = ("day_ahead_price",)

IF_ENTITY = "gold_energy_weather_hourly"
IF_HOUR_FEATURES: tuple[str, ...] = ("hour_sin", "hour_cos")
IF_GOLD_COLUMNS: tuple[str, ...] = (
    "total_load_mwh",
    "price_eur_mwh",
    "solar_mwh",
    "wind_onshore_mwh",
    "wind_offshore_mwh",
    "lignite_mwh",
    "natural_gas_mwh",
    "wind_speed_100m_onshore_avg_ms",
    "wind_speed_100m_offshore_ms",
    "shortwave_radiation_solar_avg_wm2",
)
IF_FEATURES: tuple[str, ...] = (*IF_GOLD_COLUMNS, *IF_HOUR_FEATURES)
IF_CONTAMINATION = 0.005
IF_RANDOM_STATE = 42
IF_N_ESTIMATORS = 200
IF_TRAIN_DAYS = 365
IF_MIN_TRAIN_ROWS = 14 * 24
IF_TOP_FEATURES = 3

COMPLETENESS_DAYS = 7
COMPLETENESS_WARN_PCT = 5.0  # missing share above which a series-day is a warning ...
COMPLETENESS_CRIT_PCT = 20.0  # ... and above which it is critical

SOLAR_ENTITY = "solar_mwh"
SOLAR_NIGHT_MAX_MWH = 100.0
SOLAR_NIGHT_RADIATION_WM2 = 1.0

DRIFT_MAE_DAYS = 3
DRIFT_MAPE_DAYS = 2
DRIFT_MAPE_PCT = 6.0

ANOMALY_VIEW = "dq_anomaly_updates"
SUMMARY_VIEW = "dq_summary_updates"
TOP_ANOMALIES = 10
# German "today" (Europe/Berlin) in SQL; the notebooks use it for detection_date.
LOCAL_TODAY_SQL = f"to_date(from_utc_timestamp(current_timestamp(), '{LOCAL_TZ}'))"

_HOUR = pd.Timedelta(hours=1)
_SEVERITY_RANK = {CRITICAL: 0, WARNING: 1}


# --- helpers -------------------------------------------------------------------------------------


def _frame(records: Iterable[Mapping]) -> pd.DataFrame:
    """Anomaly rows with ``ANOMALY_COLUMNS`` (UTC ``ts_utc``, float measures), possibly empty."""
    df = pd.DataFrame.from_records(list(records), columns=list(ANOMALY_COLUMNS))
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    for column in FLOAT_COLUMNS:
        df[column] = df[column].astype(float)
    return df


def empty_anomalies() -> pd.DataFrame:
    """No anomalies (all columns, correct dtypes)."""
    return _frame([])


def _naive_ns(values: pd.Series) -> np.ndarray:
    """UTC timestamps as naive ``datetime64[ns]`` (fast comparisons)."""
    return to_utc(values).dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")


def local_day_start(day: date) -> pd.Timestamp:
    """UTC start of the local (Europe/Berlin) calendar day ``day``."""
    return pd.Timestamp(day.isoformat(), tz=LOCAL_TZ).tz_convert("UTC")


def expected_hours(day: date) -> int:
    """Hours of the local day: 24, or 23/25 on DST change days."""
    end = local_day_start(day + timedelta(days=1))
    return int((end - local_day_start(day)) / _HOUR)


def combine(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """All detector rows, one per (check_name, entity, ts_utc), critical / high score first."""
    frames = [f for f in frames if not f.empty]
    if not frames:
        return empty_anomalies()
    df = pd.concat(frames, ignore_index=True)
    df = df.assign(_rank=df["severity"].map(_SEVERITY_RANK).fillna(9))
    df = df.sort_values(["_rank", "score"], ascending=[True, False], kind="stable")
    df = df.drop_duplicates(list(KEY_COLUMNS)).drop(columns="_rank")
    return df.reset_index(drop=True)


# --- (a) seasonal robust z ----------------------------------------------------------------------


def seasonal_robust_z(
    df: pd.DataFrame,
    value_col: str = "value",
    by: Sequence[str] = ("entity",),
    window_weeks: int = SEASONAL_WINDOW_WEEKS,
    warn_z: float = SEASONAL_WARN_Z,
    crit_z: float = SEASONAL_CRIT_Z,
    score_from=None,
    min_history: int = SEASONAL_MIN_HISTORY,
) -> pd.DataFrame:
    """Robust z-score of each point vs the same local hour-of-week in the previous weeks.

    ``df`` has ``by`` columns, ``ts_utc`` and ``value_col``. For every point at or after
    ``score_from`` (default: all), the history is the same entity and local (Europe/Berlin)
    hour-of-week within ``window_weeks`` before it (the point itself excluded). Points with fewer
    than ``min_history`` history values or MAD == 0 are skipped. ``|z| >= warn_z`` is a warning,
    ``|z| >= crit_z`` critical; ``lower``/``upper`` is the median +- the warning distance.
    """
    by = list(by)
    data = df.loc[df[value_col].notna(), [*by, "ts_utc", value_col]].copy()
    if data.empty:
        return empty_anomalies()
    data["ts_utc"] = to_utc(data["ts_utc"])
    local = data["ts_utc"].dt.tz_convert(LOCAL_TZ)
    data["_how"] = local.dt.day_of_week * 24 + local.dt.hour
    start = None if score_from is None else to_utc(score_from).tz_localize(None).to_datetime64()
    window = np.timedelta64(window_weeks * 7 * 24, "h")
    records = []
    for keys, group in data.groupby([*by, "_how"], sort=False):
        group = group.sort_values("ts_utc")
        times = _naive_ns(group["ts_utc"])
        values = group[value_col].to_numpy(dtype=float)
        entity = "/".join(str(k) for k in keys[:-1])
        for i, (t, x) in enumerate(zip(times, values, strict=True)):
            if start is not None and t < start:
                continue
            history = values[(times >= t - window) & (times < t)]
            if len(history) < min_history:
                continue
            median = float(np.median(history))
            mad = float(np.median(np.abs(history - median)))
            if mad == 0:
                continue
            z = MAD_TO_Z * (x - median) / mad
            if abs(z) < warn_z:
                continue
            half_range = warn_z * mad / MAD_TO_Z
            records.append(
                {
                    "check_name": SEASONAL_Z,
                    "entity": entity,
                    "ts_utc": group["ts_utc"].iloc[i],
                    "value": x,
                    "expected": median,
                    "lower": median - half_range,
                    "upper": median + half_range,
                    "score": abs(z),
                    "severity": CRITICAL if abs(z) >= crit_z else WARNING,
                    "reason": (
                        f"robust z {z:+.1f} vs same hour-of-week median {median:.6g} "
                        f"({len(history)} weeks)"
                    ),
                }
            )
    return _frame(records)


def inject_test_anomaly(
    df: pd.DataFrame, now, entity: str = "total_load", factor: float = 0.5
) -> pd.DataFrame:
    """``df`` plus a copy of ``entity`` named ``TEST:<entity>`` with one manipulated value.

    In the copy, the most recent complete hour (``ts_utc + 1 h <= now``) is multiplied by
    ``factor``; the real series is untouched, so the anomaly is clearly marked as a test.
    """
    copy = df.loc[(df["entity"] == entity) & df["value"].notna()].copy()
    copy["ts_utc"] = to_utc(copy["ts_utc"])
    complete = copy["ts_utc"] + _HOUR <= to_utc(now)
    if not complete.any():
        raise ValueError(f"no complete hour of {entity!r} to inject a test anomaly into")
    latest = copy.loc[complete, "ts_utc"].max()
    copy.loc[copy["ts_utc"] == latest, "value"] *= factor
    copy["entity"] = TEST_PREFIX + entity
    return pd.concat([df, copy], ignore_index=True)


# --- (b) multivariate isolation forest ----------------------------------------------------------


def add_hour_features(df: pd.DataFrame, local_col: str = "hour_local") -> pd.DataFrame:
    """``hour_sin`` / ``hour_cos`` of the local hour (cyclic: 23 h is next to 0 h)."""
    angle = 2 * np.pi * pd.to_datetime(df[local_col]).dt.hour.to_numpy() / 24
    return df.assign(hour_sin=np.sin(angle), hour_cos=np.cos(angle))


def _robust_sigma(x: np.ndarray, median: np.ndarray) -> np.ndarray:
    """Per-column robust sigma (MAD / 0.6745); 0 where MAD is 0."""
    return np.median(np.abs(x - median), axis=0) / MAD_TO_Z


def hourly_robust_z(
    x_train: np.ndarray, hours_train: np.ndarray, x_score: np.ndarray, hours_score: np.ndarray
) -> np.ndarray:
    """Robust z of each scored value vs the training median of the same local hour of day.

    Per-hour medians keep e.g. solar at noon from looking extreme against an all-day median
    near 0. Where the hour's MAD is 0 the column's overall MAD-sigma is used, then its std, then 1.
    """
    overall = _robust_sigma(x_train, np.median(x_train, axis=0))
    std = x_train.std(axis=0)
    overall = np.where(overall > 0, overall, np.where(std > 0, std, 1.0))
    z = np.zeros_like(x_score, dtype=float)
    for hour in np.unique(hours_score):
        ref = x_train[hours_train == hour]
        ref = ref if len(ref) else x_train
        median = np.median(ref, axis=0)
        sigma = _robust_sigma(ref, median)
        rows = hours_score == hour
        z[rows] = (x_score[rows] - median) / np.where(sigma > 0, sigma, overall)
    return z


def multivariate_isolation_forest(
    gold_df: pd.DataFrame,
    features: Sequence[str] = IF_FEATURES,
    contamination: float = IF_CONTAMINATION,
    random_state: int = IF_RANDOM_STATE,
    score_from=None,
    train_days: int = IF_TRAIN_DAYS,
    min_train_rows: int = IF_MIN_TRAIN_ROWS,
    top_n: int = IF_TOP_FEATURES,
) -> pd.DataFrame:
    """IsolationForest outliers among the hours from ``score_from`` (warnings only).

    ``gold_df`` has ``hour_utc``, ``hour_local`` and the features (``hour_sin``/``hour_cos`` are
    derived from ``hour_local`` if missing); hours with any NULL feature are dropped. The forest
    is trained on the last ``train_days`` of data, which include the scored hours (default: the
    newest 7 days): it isolates points it has seen, while an unseen point beyond the training
    range would just land in the leaf of the most extreme training points. For the same reason
    every tree uses all training rows (with the default 256 a single outlier is in ~3 % of the
    trees). ``value`` = anomaly score (-score_samples), ``upper`` = threshold, ``score`` =
    value - upper; ``reason`` lists the ``top_n`` features with the largest robust z vs their
    training medians of the same local hour. Raises if fewer than ``min_train_rows``.
    """
    data = gold_df.copy()
    if any(f in IF_HOUR_FEATURES and f not in data for f in features):
        data = add_hour_features(data)
    data["hour_utc"] = to_utc(data["hour_utc"])
    data = data.dropna(subset=list(features))
    if data.empty:
        raise ValueError("isolation forest: no hours with all features")
    newest = data["hour_utc"].max()
    if score_from is None:
        score_from = newest - pd.Timedelta(days=SCORE_DAYS) + _HOUR
    train = data[data["hour_utc"] > newest - pd.Timedelta(days=train_days)]
    if len(train) < min_train_rows:
        raise ValueError(
            f"isolation forest: {len(train)} training hours (need at least {min_train_rows})"
        )
    scored = data[data["hour_utc"] >= to_utc(score_from)]
    if scored.empty:
        return empty_anomalies()

    # Imported here: the other detectors stay usable where scikit-learn cannot load (local
    # Windows with Smart App Control blocks scipy's DLLs); jobs get it from the environment.
    from sklearn.ensemble import IsolationForest

    x_train = train[list(features)].to_numpy(dtype=float)
    x_score = scored[list(features)].to_numpy(dtype=float)
    model = IsolationForest(
        n_estimators=IF_N_ESTIMATORS,
        max_samples=1.0,
        contamination=contamination,
        random_state=random_state,
    ).fit(x_train)
    anomaly_score = -model.score_samples(x_score)
    threshold = float(-model.offset_)

    explain = [j for j, f in enumerate(features) if f not in IF_HOUR_FEATURES]
    z = hourly_robust_z(
        x_train[:, explain],
        pd.to_datetime(train["hour_local"]).dt.hour.to_numpy(),
        x_score[:, explain],
        pd.to_datetime(scored["hour_local"]).dt.hour.to_numpy(),
    )
    records = []
    for i in np.flatnonzero(model.predict(x_score) == -1):
        top = np.argsort(-np.abs(z[i]), kind="stable")[:top_n]
        deviations = ", ".join(f"{features[explain[j]]} z={z[i, j]:+.1f}" for j in top)
        records.append(
            {
                "check_name": ISOLATION_FOREST,
                "entity": IF_ENTITY,
                "ts_utc": scored["hour_utc"].iloc[i],
                "value": anomaly_score[i],
                "expected": np.nan,
                "lower": np.nan,
                "upper": threshold,
                "score": anomaly_score[i] - threshold,
                "severity": WARNING,
                "reason": f"unusual combination: {deviations}",
            }
        )
    return _frame(records)


# --- (c) completeness ---------------------------------------------------------------------------


def completeness(
    df: pd.DataFrame,
    today: date,
    days: int = COMPLETENESS_DAYS,
    entities: Iterable[str] | None = None,
    value_col: str = "value",
    warn_pct: float = COMPLETENESS_WARN_PCT,
    crit_pct: float = COMPLETENESS_CRIT_PCT,
) -> pd.DataFrame:
    """Missing hours per entity and complete local day (the ``days`` days before ``today``).

    Expected hours are 23/24/25 (DST); present hours are distinct UTC hours with a non-null
    value. Missing share > ``crit_pct`` is critical, > ``warn_pct`` a warning. ``entities``
    (default: those in ``df``) lets a series that delivered nothing at all show up as missing.
    """
    data = df.loc[df[value_col].notna(), ["entity", "ts_utc"]].copy()
    hours = to_utc(data["ts_utc"]).dt.floor("h")
    data = data.assign(hour=hours, day=hours.dt.tz_convert(LOCAL_TZ).dt.date)
    present = data.groupby(["entity", "day"])["hour"].nunique().to_dict()
    entities = sorted(set(data["entity"])) if entities is None else list(entities)
    records = []
    for entity in entities:
        for back in range(days, 0, -1):
            day = today - timedelta(days=back)
            expected = expected_hours(day)
            n_present = min(int(present.get((entity, day), 0)), expected)
            missing = expected - n_present
            pct = 100 * missing / expected
            if pct <= warn_pct:
                continue
            records.append(
                {
                    "check_name": COMPLETENESS,
                    "entity": entity,
                    "ts_utc": local_day_start(day),
                    "value": n_present,
                    "expected": expected,
                    "lower": expected * (1 - warn_pct / 100),
                    "upper": expected,
                    "score": pct,
                    "severity": CRITICAL if pct > crit_pct else WARNING,
                    "reason": f"{missing} of {expected} hours missing on {day} ({pct:.0f}%)",
                }
            )
    return _frame(records)


# --- (d) physics rules --------------------------------------------------------------------------


def physics_rules(
    gold_df: pd.DataFrame,
    max_mwh: float = SOLAR_NIGHT_MAX_MWH,
    max_radiation: float = SOLAR_NIGHT_RADIATION_WM2,
) -> pd.DataFrame:
    """Critical: ``solar_mwh > max_mwh`` while there is no sun (radiation < ``max_radiation``).

    Open-Meteo radiation at hour H is the mean of the preceding hour, while SMARD's hour H covers
    [H, H+1); "night" therefore needs radiation below the limit at both H and H+1. Hours whose
    next-hour radiation is unknown are not checked.
    """
    rad_col = "shortwave_radiation_solar_avg_wm2"
    data = gold_df[["hour_utc", SOLAR_ENTITY, rad_col]].copy()
    data["hour_utc"] = to_utc(data["hour_utc"])
    radiation = data.drop_duplicates("hour_utc", keep="last").set_index("hour_utc")[rad_col]
    next_rad = radiation.reindex(data["hour_utc"] + _HOUR).to_numpy(dtype=float)
    rad = data[rad_col].to_numpy(dtype=float)
    solar = data[SOLAR_ENTITY].to_numpy(dtype=float)
    flagged = (rad < max_radiation) & (next_rad < max_radiation) & (solar > max_mwh)
    records = [
        {
            "check_name": SOLAR_AT_NIGHT,
            "entity": SOLAR_ENTITY,
            "ts_utc": data["hour_utc"].iloc[i],
            "value": solar[i],
            "expected": np.nan,
            "lower": np.nan,
            "upper": max_mwh,
            "score": solar[i] / max_mwh,
            "severity": CRITICAL,
            "reason": (
                f"solar output at night: {solar[i]:.0f} MWh with radiation "
                f"{rad[i]:.1f}/{next_rad[i]:.1f} W/m2"
            ),
        }
        for i in np.flatnonzero(flagged)
    ]
    return _frame(records)


# --- (e) forecast drift -------------------------------------------------------------------------


def _streak_records(
    group: pd.DataFrame,
    bad: pd.Series,
    min_days: int,
    check_name: str,
    severity: str,
    value_col: str,
    expected: pd.Series,
    limit: pd.Series,
    reason: str,
) -> list[dict]:
    """Rows for issue dates ending a run of >= ``min_days`` consecutive bad calendar days.

    ``limit`` is the value the metric must not exceed (``upper``; ``score`` = value / limit).
    """
    records, streak, previous = [], 0, None
    rows = zip(group["issue_date"], bad, group[value_col], expected, limit, strict=True)
    for day, is_bad, value, expected_value, upper in rows:
        consecutive = previous is not None and day == previous + timedelta(days=1)
        streak = (streak + 1 if consecutive else 1) if is_bad else 0
        previous = day
        if streak >= min_days:
            records.append(
                {
                    "check_name": check_name,
                    "entity": group["target"].iloc[0],
                    "ts_utc": local_day_start(day),
                    "value": value,
                    "expected": expected_value,
                    "lower": np.nan,
                    "upper": upper,
                    "score": value / upper if upper > 0 else value,
                    "severity": severity,
                    "reason": reason.format(days=streak),
                }
            )
    return records


def forecast_drift(
    accuracy_df: pd.DataFrame | None,
    mae_days: int = DRIFT_MAE_DAYS,
    mape_days: int = DRIFT_MAPE_DAYS,
    mape_pct: float = DRIFT_MAPE_PCT,
    since: date | None = None,
) -> pd.DataFrame:
    """Forecast quality drift per target from gold_forecast_accuracy rows.

    Warning: model ``mae`` > ``baseline_mae`` on ``mae_days`` consecutive issue dates.
    Critical: model ``mape`` > ``mape_pct`` on ``mape_days`` consecutive issue dates (NULL MAPE,
    i.e. price, never counts). One row per issue date that completes or extends such a run;
    with ``since``, only issue dates from then on are reported (runs may start earlier).
    Empty or missing input gives no rows.
    """
    if accuracy_df is None or accuracy_df.empty:
        return empty_anomalies()
    data = accuracy_df[["target", "issue_date", "mae", "mape", "baseline_mae"]].copy()
    data["issue_date"] = pd.to_datetime(data["issue_date"]).dt.date
    for column in ("mae", "mape", "baseline_mae"):
        data[column] = data[column].astype(float)
    records = []
    for _, group in data.groupby("target", sort=True):
        group = group.sort_values("issue_date").reset_index(drop=True)
        records += _streak_records(
            group,
            group["mae"] > group["baseline_mae"],
            mae_days,
            DRIFT_MAE,
            WARNING,
            "mae",
            group["baseline_mae"],
            group["baseline_mae"],
            "model MAE above seasonal naive baseline on {days} consecutive issue dates",
        )
        records += _streak_records(
            group,
            group["mape"] > mape_pct,
            mape_days,
            DRIFT_MAPE,
            CRITICAL,
            "mape",
            pd.Series(np.nan, index=group.index),
            pd.Series(mape_pct, index=group.index),
            f"model MAPE above {mape_pct:g}% on {{days}} consecutive issue dates",
        )
    rows = _frame(records)
    if since is not None:
        rows = rows[rows["ts_utc"] >= local_day_start(since)].reset_index(drop=True)
    return rows


# --- results, summary, SQL ----------------------------------------------------------------------


def summary_rows(anomalies: pd.DataFrame, detection_date: date) -> pd.DataFrame:
    """gold_dq_daily_summary rows: n per (check_name, severity), 0 for pairs without rows."""
    counts = anomalies.groupby(["check_name", "severity"]).size().to_dict()
    pairs = [(c, s) for c, severities in CHECK_SEVERITIES.items() for s in severities]
    pairs += sorted(set(counts) - set(pairs))
    return pd.DataFrame(
        {
            "detection_date": [detection_date] * len(pairs),
            "check_name": [c for c, _ in pairs],
            "severity": [s for _, s in pairs],
            "n": [int(counts.get(p, 0)) for p in pairs],
        }
    )


def critical_failures(anomalies: pd.DataFrame) -> list[str]:
    """One line per critical row that should fail the run (``TEST:`` entities excluded)."""
    critical = anomalies[
        (anomalies["severity"] == CRITICAL) & ~anomalies["entity"].str.startswith(TEST_PREFIX)
    ]
    return [
        f"{r.check_name} {r.entity} {r.ts_utc:%Y-%m-%d %H:%M} UTC: {r.reason}"
        for r in critical.itertuples(index=False)
    ]


def to_spark_rows(anomalies: pd.DataFrame, detected_at) -> pd.DataFrame:
    """Rows in GOLD_DQ_ANOMALIES_COLUMNS order: naive UTC timestamps, NaN as None (NULL)."""
    rows = anomalies[list(ANOMALY_COLUMNS)].copy()
    rows["ts_utc"] = to_utc(rows["ts_utc"]).dt.tz_localize(None)
    rows["detected_at"] = to_utc(detected_at).tz_localize(None)
    rows = rows.astype(object).where(rows.notna(), None)
    return rows[[name for name, _, _ in GOLD_DQ_ANOMALIES_COLUMNS]]


def merge_anomalies_sql(fqn: str, source: str = ANOMALY_VIEW) -> str:
    """Upsert on (check_name, entity, ts_utc); a re-detection updates values and detected_at."""
    on = " AND ".join(f"t.{c} = s.{c}" for c in KEY_COLUMNS)
    return (
        f"MERGE INTO {fqn} AS t USING {source} AS s ON {on} "
        "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *"
    )


def _date_sql(day: date) -> str:
    return f"DATE '{day.isoformat()}'"


def replace_summary_sql(fqn: str, day: date, source: str = SUMMARY_VIEW) -> str:
    """Replace the summary of ``day`` atomically (re-runs on the same day overwrite it)."""
    return (
        f"INSERT INTO {fqn} REPLACE WHERE detection_date = {_date_sql(day)} SELECT * FROM {source}"
    )


def summary_for_day_sql(fqn: str, day: date) -> str:
    """``check_name, severity, n`` of ``day`` from gold_dq_daily_summary."""
    return (
        f"SELECT check_name, severity, n FROM {fqn} "
        f"WHERE detection_date = {_date_sql(day)} ORDER BY check_name, severity"
    )


def top_anomalies_sql(fqn: str, day: date, limit: int = TOP_ANOMALIES) -> str:
    """Anomalies detected on local ``day``: critical first, then by score."""
    return (
        f"SELECT check_name, entity, ts_utc, severity, score, reason FROM {fqn} "
        f"WHERE to_date(from_utc_timestamp(detected_at, '{LOCAL_TZ}')) = {_date_sql(day)} "
        "ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, score DESC, ts_utc DESC "
        f"LIMIT {int(limit)}"
    )


def llm_prompt(day: date, summary: Iterable[Mapping], top: Iterable[Mapping]) -> str:
    """Short prompt: the day's counts per check/severity and the top anomalies."""
    counts = [f"- {r['check_name']} / {r['severity']}: {r['n']}" for r in summary]
    lines = [
        f"- [{r['severity']}] {r['check_name']} {r['entity']} at {r['ts_utc']} UTC: {r['reason']}"
        for r in top
    ]
    return "\n".join(
        [
            "You are a data engineer. Summarize today's data-quality status of a German energy "
            "lakehouse (SMARD load/generation/prices, Open-Meteo weather, day-ahead forecasts) "
            "in at most 5 sentences for an operations channel. Name critical issues first and "
            "suggest a likely cause or next step. Entities starting with TEST: are injected "
            "test anomalies; mention them only as tests.",
            f"Detection date (Europe/Berlin): {day.isoformat()}",
            "Counts per check and severity:",
            *(counts or ["- none"]),
            "Top anomalies:",
            *(lines or ["- none"]),
        ]
    )


def ai_query_sql(endpoint: str, prompt: str) -> str:
    """``SELECT ai_query(endpoint, prompt) AS summary_text`` with both as string literals."""
    return f"SELECT ai_query({sql_string(endpoint)}, {sql_string(prompt)}) AS summary_text"
