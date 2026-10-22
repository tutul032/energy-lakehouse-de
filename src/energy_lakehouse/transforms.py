"""Pure helpers for the energy pipeline (no Spark imports, unit-tested locally).

The pipeline code in ``src/pipelines/`` turns these into Spark expressions.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from energy_lakehouse.smard_config import SERIES, SmardSeries
from energy_lakehouse.weather_config import HOURLY_VARIABLES, LOCATIONS, WeatherLocation

# Landing path: .../smard/filter=<id>/resolution=<res>/<ts>.json
# Patterns are valid both as Python and Java regexes (Spark ``regexp_extract``).
FILTER_ID_REGEX = r"filter=(\d+)"
RESOLUTION_REGEX = r"resolution=([^/\\]+)"
CHUNK_TS_REGEX = r"(\d+)\.json$"

# Weather landing path: .../weather/location=<id>/run_date=<yyyy-mm-dd>.json
LOCATION_REGEX = r"location=([^/\\]+)"
RUN_DATE_REGEX = r"run_date=(\d{4}-\d{2}-\d{2})\.json$"

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


def parse_weather_landing_path(path: str) -> tuple[str, str]:
    """Return ``(location_id, run_date)`` parsed from a weather landing file path."""
    return _extract(LOCATION_REGEX, path), _extract(RUN_DATE_REGEX, path)


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


# gold_energy_daily columns: (name, type, comment), per local calendar day.
GOLD_ENERGY_DAILY_COLUMNS: list[tuple[str, str, str]] = [
    ("date_local", "DATE", f"Calendar day in {LOCAL_TZ}."),
    *[(c, "DOUBLE", f"Daily sum of {c} (MWh).") for c in [*energy_columns(), "renewables_mwh"]],
    ("price_avg_eur_mwh", "DOUBLE", "Average hourly day-ahead price (EUR/MWh)."),
    ("price_min_eur_mwh", "DOUBLE", "Lowest hourly day-ahead price (EUR/MWh)."),
    ("price_max_eur_mwh", "DOUBLE", "Highest hourly day-ahead price (EUR/MWh)."),
    ("renewables_share_avg", "DOUBLE", "Average of the hourly renewables_share."),
    ("hour_count", "BIGINT", "Hours in the day: 24, or 23/25 on DST change days."),
]

GOLD_ENERGY_WEATHER_HOURLY_COLUMNS: list[tuple[str, str, str]] = gold_weather_columns()

# Single source for the gold schemas: used by the pipeline and checked against contracts/*.yaml.
GOLD_TABLE_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "gold_energy_hourly": GOLD_ENERGY_HOURLY_COLUMNS,
    "gold_energy_daily": GOLD_ENERGY_DAILY_COLUMNS,
    "gold_energy_weather_hourly": GOLD_ENERGY_WEATHER_HOURLY_COLUMNS,
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
}
