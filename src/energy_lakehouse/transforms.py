"""Pure helpers for the energy pipeline (no Spark imports, unit-tested locally).

The pipeline code in ``src/pipelines/`` turns these into Spark expressions.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from energy_lakehouse.smard_config import SERIES, SmardSeries
from energy_lakehouse.weather_config import (
    HOURLY_VARIABLES,
    LOCATIONS,
    RUN_TS_FORMAT,
    WeatherLocation,
)

# Landing path: .../smard/filter=<id>/resolution=<res>/<ts>.json
# Patterns are valid both as Python and Java regexes (Spark ``regexp_extract``).
FILTER_ID_REGEX = r"filter=(\d+)"
RESOLUTION_REGEX = r"resolution=([^/\\]+)"
CHUNK_TS_REGEX = r"(\d+)\.json$"

# Weather landing path, .../weather/location=<id>/ followed by one of:
#   run_ts=<yyyymmddThhmmZ>.json          daily forecast run (UTC run timestamp)
#   run_date=<yyyy-mm-dd>.json            daily forecast run, old naming (run_ts_utc = 00:00 UTC)
#   run_date=archive_<start>_<end>.json   archive backfill (run_ts_utc = end + 1 day, 00:00 UTC;
#                                         bronze run_date is NULL for these)
LOCATION_REGEX = r"location=([^/\\]+)"
RUN_TS_REGEX = r"run_ts=(\d{8}T\d{4}Z)\.json$"
RUN_DATE_REGEX = r"run_date=(\d{4}-\d{2}-\d{2})\.json$"
ARCHIVE_RUN_REGEX = r"run_date=archive_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json$"
# Spark (Java DateTimeFormatter) pattern equivalent to weather_config.RUN_TS_FORMAT.
RUN_TS_SPARK_FORMAT = "yyyyMMdd'T'HHmm'Z'"

LOCAL_TZ = "Europe/Berlin"

# Hourly aggregation per unit: energy adds up over sub-hour slots, prices are averaged.
AGG_BY_UNIT: dict[str, str] = {"MWh": "sum", "EUR/MWh": "avg"}

# Series name -> wide gold column.
PIVOT_COLUMNS: dict[str, str] = {
    "total_load": "total_load_mwh",
    "day_ahead_price": "price_eur_mwh",
    "solar": "solar_mwh",
    "wind_onshore": "wind_onshore_mwh",
    "wind_offshore": "wind_offshore_mwh",
    "lignite": "lignite_mwh",
    "hard_coal": "hard_coal_mwh",
    "natural_gas": "natural_gas_mwh",
}

RENEWABLE_COLUMNS: tuple[str, ...] = ("solar_mwh", "wind_onshore_mwh", "wind_offshore_mwh")

# gold_energy_hourly columns: (name, type, comment). Reused by gold_energy_weather_hourly.
GOLD_ENERGY_HOURLY_COLUMNS: list[tuple[str, str, str]] = [
    ("hour_utc", "TIMESTAMP", "Start of the hour (UTC)."),
    ("hour_local", "TIMESTAMP_NTZ", f"Start of the hour, local wall-clock time ({LOCAL_TZ})."),
    ("total_load_mwh", "DOUBLE", "Total grid load in Germany (MWh)."),
    ("price_eur_mwh", "DOUBLE", "Day-ahead price DE-LU, hourly average (EUR/MWh)."),
    ("solar_mwh", "DOUBLE", "Solar (photovoltaics) generation (MWh)."),
    ("wind_onshore_mwh", "DOUBLE", "Onshore wind generation (MWh)."),
    ("wind_offshore_mwh", "DOUBLE", "Offshore wind generation (MWh)."),
    ("lignite_mwh", "DOUBLE", "Lignite generation (MWh)."),
    ("hard_coal_mwh", "DOUBLE", "Hard coal generation (MWh)."),
    ("natural_gas_mwh", "DOUBLE", "Natural gas generation (MWh)."),
    ("renewables_mwh", "DOUBLE", "Solar + onshore wind + offshore wind (MWh)."),
    ("renewables_share", "DOUBLE", "renewables_mwh / total_load_mwh; NULL if load is 0 or NULL."),
]


@dataclass(frozen=True)
class WeatherRoleAgg:
    """One gold weather column: ``func`` of ``variable`` over all locations with ``role``."""

    column: str
    role: str
    variable: str
    func: str
    comment: str


# Weather variable aggregated per location role, matching what drives each SMARD series.
WEATHER_ROLE_AGGS: list[WeatherRoleAgg] = [
    WeatherRoleAgg(
        "temperature_load_avg_c",
        "load",
        "temperature_2m",
        "avg",
        "Mean 2 m air temperature over the load-centre locations (deg C).",
    ),
    WeatherRoleAgg(
        "wind_speed_100m_onshore_avg_ms",
        "onshore_wind",
        "wind_speed_100m",
        "avg",
        "Mean 100 m wind speed over the onshore-wind locations (m/s).",
    ),
    WeatherRoleAgg(
        "wind_speed_100m_offshore_ms",
        "offshore_wind",
        "wind_speed_100m",
        "avg",
        "100 m wind speed at the offshore-wind location(s), averaged if several (m/s).",
    ),
    WeatherRoleAgg(
        "shortwave_radiation_solar_avg_wm2",
        "solar",
        "shortwave_radiation",
        "avg",
        "Mean shortwave (global horizontal) radiation over the solar locations (W/m2).",
    ),
]


def _extract(pattern: str, path: str) -> str:
    match = re.search(pattern, path)
    if not match:
        raise ValueError(f"path does not match {pattern!r}: {path}")
    return match.group(1)


def parse_landing_path(path: str) -> tuple[int, str, int]:
    """Return ``(filter_id, resolution, chunk_ts_ms)`` parsed from a landing file path."""
    return (
        int(_extract(FILTER_ID_REGEX, path)),
        _extract(RESOLUTION_REGEX, path),
        int(_extract(CHUNK_TS_REGEX, path)),
    )


def parse_weather_landing_path(path: str) -> tuple[str, datetime, bool]:
    """Return ``(location_id, run_ts_utc, is_archive)`` from a weather landing file path.

    Mirrors bronze_weather: ``run_ts=`` files give their UTC run timestamp, old ``run_date=``
    files 00:00 UTC of that day, archive files 00:00 UTC of the day after their end date.
    """
    location_id = _extract(LOCATION_REGEX, path)
    if match := re.search(RUN_TS_REGEX, path):
        run_ts = datetime.strptime(match.group(1), RUN_TS_FORMAT).replace(tzinfo=UTC)
        return location_id, run_ts, False
    if match := re.search(ARCHIVE_RUN_REGEX, path):
        end = date.fromisoformat(match.group(2)) + timedelta(days=1)
        return location_id, datetime(end.year, end.month, end.day, tzinfo=UTC), True
    run_date = date.fromisoformat(_extract(RUN_DATE_REGEX, path))
    return location_id, datetime(run_date.year, run_date.month, run_date.day, tzinfo=UTC), False


def expected_local_hours(day: date, tz: str = LOCAL_TZ) -> int:
    """Hours in the local calendar day ``day``: 24, or 23/25 on DST change days.

    Same rule as gold_energy_daily.expected_hours (local midnight to next local midnight, in UTC).
    """
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time(), zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(UTC)
    return int((end - start).total_seconds() // 3600)


def agg_rule(unit: str) -> str:
    """Spark aggregate function name (``sum``/``avg``) for a series unit."""
    try:
        return AGG_BY_UNIT[unit]
    except KeyError:
        raise ValueError(f"no aggregation rule for unit {unit!r}") from None


def pivot_column(series_name: str) -> str:
    """Wide gold column for a series name."""
    try:
        return PIVOT_COLUMNS[series_name]
    except KeyError:
        raise ValueError(f"no gold column for series {series_name!r}") from None


def hourly_agg_specs(series: Iterable[SmardSeries] = SERIES) -> list[tuple[str, str, str]]:
    """``(series_name, gold_column, agg_function)`` per series, in config order."""
    return [(s.name, pivot_column(s.name), agg_rule(s.unit)) for s in series]


def energy_columns(series: Iterable[SmardSeries] = SERIES) -> list[str]:
    """Gold columns holding summed energy (MWh), in config order."""
    return [col for _, col, func in hourly_agg_specs(series) if func == "sum"]


def series_metadata_rows(series: Iterable[SmardSeries] = SERIES) -> list[tuple[int, str, str]]:
    """``(filter_id, series_name, unit)`` rows for the static metadata DataFrame."""
    return [(s.filter_id, s.name, s.unit) for s in series]


def ddl_schema(columns: Iterable[tuple[str, str, str]]) -> str:
    """Build a DDL schema string with column comments from ``(name, type, comment)``."""
    parts = []
    for name, dtype, comment in columns:
        escaped = comment.replace("\\", "\\\\").replace("'", "\\'")
        parts.append(f"{name} {dtype} COMMENT '{escaped}'")
    return ", ".join(parts)


def weather_raw_schema(variables: Iterable[str] = HOURLY_VARIABLES) -> str:
    """DDL schema for the raw Open-Meteo response (unix-second ``time`` + one array per variable).

    Values are read as DOUBLE (``cloud_cover`` arrives as integers, which Spark widens).
    """
    hourly = ", ".join(["time: ARRAY<BIGINT>", *[f"{v}: ARRAY<DOUBLE>" for v in variables]])
    return (
        "latitude DOUBLE, longitude DOUBLE, elevation DOUBLE, utc_offset_seconds INT, "
        f"timezone STRING, hourly_units MAP<STRING, STRING>, hourly STRUCT<{hourly}>"
    )


def weather_location_rows(
    locations: Iterable[WeatherLocation] = LOCATIONS,
) -> list[tuple[str, str, str]]:
    """``(location_id, location_name, role)`` rows for the static location DataFrame."""
    return [(loc.location_id, loc.name, loc.role) for loc in locations]


def weather_role_agg_specs(
    locations: Iterable[WeatherLocation] = LOCATIONS,
    aggs: Iterable[WeatherRoleAgg] = WEATHER_ROLE_AGGS,
    variables: Iterable[str] = HOURLY_VARIABLES,
) -> list[WeatherRoleAgg]:
    """Validated gold weather aggregations: every role has a location, every variable exists."""
    roles = {loc.role for loc in locations}
    known_vars = set(variables)
    specs = list(aggs)
    for spec in specs:
        if spec.role not in roles:
            raise ValueError(f"no location with role {spec.role!r} for {spec.column}")
        if spec.variable not in known_vars:
            raise ValueError(f"unknown weather variable {spec.variable!r} for {spec.column}")
        if spec.func not in ("avg", "min", "max"):
            raise ValueError(f"unsupported aggregation {spec.func!r} for {spec.column}")
    if len({s.column for s in specs}) != len(specs):
        raise ValueError("duplicate gold weather column")
    return specs


def gold_weather_columns(
    aggs: Iterable[WeatherRoleAgg] = WEATHER_ROLE_AGGS,
) -> list[tuple[str, str, str]]:
    """``(name, type, comment)`` for gold_energy_weather_hourly: energy columns + weather."""
    return [*GOLD_ENERGY_HOURLY_COLUMNS, *[(a.column, "DOUBLE", a.comment) for a in aggs]]


def gold_weather_forecast_columns(
    aggs: Iterable[WeatherRoleAgg] = WEATHER_ROLE_AGGS,
) -> list[tuple[str, str, str]]:
    """``(name, type, comment)`` for gold_weather_forecast_hourly: hour keys, weather, issue time.

    Same role aggregation (column names/comments) as gold_energy_weather_hourly, for future hours.
    """
    return [
        *GOLD_ENERGY_HOURLY_COLUMNS[:2],  # hour_utc, hour_local
        *[(a.column, "DOUBLE", a.comment) for a in aggs],
        (
            "issued_at",
            "TIMESTAMP",
            "Newest run timestamp (UTC) of the forecast files behind this hour (issue time).",
        ),
    ]


# silver_weather_forecast_hourly columns: (name, type, comment), one row per location and hour.
SILVER_WEATHER_FORECAST_HOURLY_COLUMNS: list[tuple[str, str, str]] = [
    ("location_id", "STRING", "Weather location id (weather_config.LOCATIONS)."),
    ("location_name", "STRING", "Human-readable location name."),
    ("role", "STRING", "SMARD series the location is a proxy for (load, solar, ...)."),
    ("ts_s", "BIGINT", "Forecast hour as unix seconds (UTC), as delivered by Open-Meteo."),
    ("timestamp_utc", "TIMESTAMP", "Start of the forecast hour (UTC)."),
    ("temperature_2m", "DOUBLE", "Forecast 2 m air temperature (deg C)."),
    ("wind_speed_10m", "DOUBLE", "Forecast 10 m wind speed (m/s)."),
    ("wind_speed_100m", "DOUBLE", "Forecast 100 m wind speed (m/s)."),
    ("shortwave_radiation", "DOUBLE", "Forecast shortwave (global horizontal) radiation (W/m2)."),
    ("cloud_cover", "DOUBLE", "Forecast total cloud cover (%)."),
    ("run_date", "DATE", "UTC run date of the landing file (newest per location)."),
    ("run_ts_utc", "TIMESTAMP", "UTC run timestamp of the landing file = forecast issue time."),
    ("source_file", "STRING", "Landing file the forecast comes from."),
    ("file_modification_time", "TIMESTAMP", "Modification time of the landing file."),
    ("ingested_at", "TIMESTAMP", "Bronze ingestion time of the landing file."),
]


# gold_energy_daily columns: (name, type, comment), per local calendar day.
GOLD_ENERGY_DAILY_COLUMNS: list[tuple[str, str, str]] = [
    ("date_local", "DATE", f"Calendar day in {LOCAL_TZ}."),
    *[(c, "DOUBLE", f"Daily sum of {c} (MWh).") for c in [*energy_columns(), "renewables_mwh"]],
    ("price_avg_eur_mwh", "DOUBLE", "Average hourly day-ahead price (EUR/MWh)."),
    ("price_min_eur_mwh", "DOUBLE", "Lowest hourly day-ahead price (EUR/MWh)."),
    ("price_max_eur_mwh", "DOUBLE", "Highest hourly day-ahead price (EUR/MWh)."),
    ("renewables_share_avg", "DOUBLE", "Average of the hourly renewables_share."),
    ("hour_count", "BIGINT", "Hours in the day: 24, or 23/25 on DST change days."),
    ("load_hours", "BIGINT", "Hours with a published total_load_mwh (non-NULL)."),
    ("price_hours", "BIGINT", "Hours with a published price_eur_mwh (non-NULL)."),
    ("expected_hours", "INT", "Local hours in the day: 24, or 23/25 on DST change days."),
    ("is_complete", "BOOLEAN", "True if load_hours and price_hours both equal expected_hours."),
]

GOLD_ENERGY_WEATHER_HOURLY_COLUMNS: list[tuple[str, str, str]] = gold_weather_columns()
GOLD_WEATHER_FORECAST_HOURLY_COLUMNS: list[tuple[str, str, str]] = gold_weather_forecast_columns()

# Day-ahead forecasts (written by 11_score_forecast, not the pipeline): one row per target hour,
# target and issue day.
GOLD_FORECAST_HOURLY_COLUMNS: list[tuple[str, str, str]] = [
    ("target_hour_utc", "TIMESTAMP", "Start of the forecast hour (UTC)."),
    (
        "target_hour_local",
        "TIMESTAMP_NTZ",
        f"Start of the forecast hour, local wall-clock time ({LOCAL_TZ}).",
    ),
    (
        "target",
        "STRING",
        "Forecast target: gold_energy_hourly column (total_load_mwh or price_eur_mwh).",
    ),
    ("forecast", "DOUBLE", "Forecast value in the target's unit (MWh or EUR/MWh)."),
    (
        "model_version",
        "STRING",
        "Unity Catalog version of forecaster_<target> (alias champion) that made the forecast.",
    ),
    (
        "issue_time_utc",
        "TIMESTAMP",
        "When the forecast was issued (UTC); only data available then was used.",
    ),
    ("issue_date", "DATE", f"Local ({LOCAL_TZ}) issue day D; the forecast covers local day D+1."),
]

# Forecast accuracy (written by 12_forecast_accuracy): one row per target and issue day.
GOLD_FORECAST_ACCURACY_COLUMNS: list[tuple[str, str, str]] = [
    ("target", "STRING", "Forecast target (total_load_mwh or price_eur_mwh)."),
    ("issue_date", "DATE", f"Local ({LOCAL_TZ}) issue day D of the forecast for day D+1."),
    ("n_hours", "BIGINT", "Forecast hours with an actual value in gold_energy_hourly."),
    ("mae", "DOUBLE", "Mean absolute error of the model forecast (target unit)."),
    (
        "mape",
        "DOUBLE",
        "Mean absolute percentage error of the model forecast (%), hours with |actual| < 1 "
        "skipped; NULL for price_eur_mwh.",
    ),
    (
        "baseline_mae",
        "DOUBLE",
        "Mean absolute error of the seasonal naive baseline (same hour 168 h earlier).",
    ),
    (
        "baseline_mape",
        "DOUBLE",
        "MAPE of the seasonal naive baseline (%); NULL for price_eur_mwh.",
    ),
]

# Data-quality anomalies (written by 20_detect_anomalies, MERGE): one row per check, entity, time.
GOLD_DQ_ANOMALIES_COLUMNS: list[tuple[str, str, str]] = [
    (
        "check_name",
        "STRING",
        "Detector that flagged the row (seasonal_robust_z, isolation_forest, completeness, "
        "physics_solar_at_night, forecast_drift_mae, forecast_drift_mape).",
    ),
    (
        "entity",
        "STRING",
        "What was checked: SMARD series, gold column/table or forecast target; the prefix TEST: "
        "marks an injected test anomaly.",
    ),
    (
        "ts_utc",
        "TIMESTAMP",
        f"Start of the flagged hour (UTC); for daily checks the start of the local ({LOCAL_TZ}) "
        "day.",
    ),
    (
        "value",
        "DOUBLE",
        "Observed value (entity unit; completeness: present hours; isolation_forest: anomaly "
        "score).",
    ),
    (
        "expected",
        "DOUBLE",
        "Expected value (seasonal median, expected hours, baseline MAE); NULL if not applicable.",
    ),
    ("lower", "DOUBLE", "Lower bound of the normal range; NULL if not applicable."),
    ("upper", "DOUBLE", "Upper bound of the normal range or threshold; NULL if not applicable."),
    (
        "score",
        "DOUBLE",
        "Anomaly strength, higher = more abnormal (|robust z|, missing %, ratio to threshold).",
    ),
    ("severity", "STRING", "critical or warning."),
    ("reason", "STRING", "Short human-readable explanation."),
    ("detected_at", "TIMESTAMP", "Start (UTC) of the latest detection run that flagged the row."),
]

# Daily anomaly counts (written by 20_detect_anomalies): one row per day, check and severity.
GOLD_DQ_DAILY_SUMMARY_COLUMNS: list[tuple[str, str, str]] = [
    ("detection_date", "DATE", f"Local ({LOCAL_TZ}) day of the detection run."),
    ("check_name", "STRING", "Detector (see gold_dq_anomalies.check_name)."),
    ("severity", "STRING", "critical or warning."),
    ("n", "BIGINT", "Rows flagged by the check with this severity in the run (0 = none)."),
]

# Optional LLM summary of the day's anomalies (21_dq_summary_llm); no data contract.
GOLD_DQ_DAILY_SUMMARY_TEXT_COLUMNS: list[tuple[str, str, str]] = [
    ("detection_date", "DATE", f"Local ({LOCAL_TZ}) day of the detection run."),
    ("model", "STRING", "Model (serving endpoint name) called with ai_query."),
    ("prompt", "STRING", "Prompt sent to the model."),
    ("prompt_chars", "BIGINT", "Length of the prompt in characters."),
    ("summary_text", "STRING", "Generated summary of the day's data-quality anomalies."),
    ("generated_at", "TIMESTAMP", "When the summary was generated (UTC)."),
]
GOLD_DQ_DAILY_SUMMARY_TEXT_COMMENT = (
    "LLM-written summary (ai_query) of gold_dq_daily_summary and the top anomalies per local "
    "day. One row per detection_date."
)

# Single source for the gold schemas: used by the pipeline and checked against contracts/*.yaml.
GOLD_TABLE_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "gold_energy_hourly": GOLD_ENERGY_HOURLY_COLUMNS,
    "gold_energy_daily": GOLD_ENERGY_DAILY_COLUMNS,
    "gold_energy_weather_hourly": GOLD_ENERGY_WEATHER_HOURLY_COLUMNS,
    "gold_weather_forecast_hourly": GOLD_WEATHER_FORECAST_HOURLY_COLUMNS,
    "gold_forecast_hourly": GOLD_FORECAST_HOURLY_COLUMNS,
    "gold_forecast_accuracy": GOLD_FORECAST_ACCURACY_COLUMNS,
    "gold_dq_anomalies": GOLD_DQ_ANOMALIES_COLUMNS,
    "gold_dq_daily_summary": GOLD_DQ_DAILY_SUMMARY_COLUMNS,
}

GOLD_TABLE_COMMENTS: dict[str, str] = {
    "gold_energy_hourly": (
        "Hourly German power load, day-ahead price and generation mix (SMARD, CC BY 4.0). "
        "One row per UTC hour."
    ),
    "gold_energy_daily": (
        f"Daily German energy totals and price statistics per {LOCAL_TZ} calendar day "
        "(SMARD, CC BY 4.0). One row per local day."
    ),
    "gold_energy_weather_hourly": (
        "gold_energy_hourly left-joined with Open-Meteo weather aggregated by location role: "
        "load-centre temperature, onshore/offshore 100 m wind, solar radiation "
        "(SMARD + Open-Meteo, CC BY 4.0). Weather columns are NULL where no observation "
        "exists yet."
    ),
    "gold_weather_forecast_hourly": (
        "Open-Meteo day-ahead weather forecast aggregated by location role, one row per future "
        "UTC hour: load-centre temperature, onshore/offshore 100 m wind, solar radiation "
        "(Open-Meteo, CC BY 4.0). Built from the newest daily run per location; only hours "
        "after its issue time."
    ),
    "gold_forecast_hourly": (
        "Day-ahead forecasts of total_load_mwh and price_eur_mwh: issued daily around 05:17 UTC "
        f"on local day D for every hour of the {LOCAL_TZ} day D+1, using only data available "
        "at issue time (champion model per target). One row per target_hour_utc, target and "
        "issue_date."
    ),
    "gold_forecast_accuracy": (
        "Accuracy of the day-ahead forecasts against gold_energy_hourly actuals, per target and "
        "issue_date: MAE/MAPE of the model and of the seasonal naive baseline (same hour one "
        "week earlier) for the same hours. Rebuilt after every scoring run."
    ),
    "gold_dq_anomalies": (
        "Data-quality anomalies from the daily detect_anomalies task: seasonal robust z-scores "
        "per SMARD series, an IsolationForest over the hourly gold features, completeness per "
        "series and local day, physics rules and forecast drift. One row per check_name, entity "
        "and ts_utc; a re-detection updates the row."
    ),
    "gold_dq_daily_summary": (
        f"Daily anomaly counts per check and severity from the detect_anomalies run of each local "
        f"({LOCAL_TZ}) day; checks that found nothing have n = 0. One row per detection_date, "
        "check_name and severity."
    ),
}

SILVER_WEATHER_FORECAST_HOURLY_COMMENT = (
    "Open-Meteo hourly weather forecast per location (one row per location_id and "
    "timestamp_utc), from the newest daily run per location; only hours after that file's "
    "run timestamp (the forecast available at issue time)."
)

# Every table with a data contract (contracts/<table>.yaml): the gold tables + the forecast silver.
CONTRACT_TABLE_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    **GOLD_TABLE_COLUMNS,
    "silver_weather_forecast_hourly": SILVER_WEATHER_FORECAST_HOURLY_COLUMNS,
}
CONTRACT_TABLE_COMMENTS: dict[str, str] = {
    **GOLD_TABLE_COMMENTS,
    "silver_weather_forecast_hourly": SILVER_WEATHER_FORECAST_HOURLY_COMMENT,
}
