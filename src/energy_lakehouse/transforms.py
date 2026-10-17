"""Pure helpers for the energy pipeline (no Spark imports, unit-tested locally).

The pipeline code in ``src/pipelines/`` turns these into Spark expressions.
"""

import re
from collections.abc import Iterable

from energy_lakehouse.smard_config import SERIES, SmardSeries

# Landing path: .../smard/filter=<id>/resolution=<res>/<ts>.json
# Patterns are valid both as Python and Java regexes (Spark ``regexp_extract``).
FILTER_ID_REGEX = r"filter=(\d+)"
RESOLUTION_REGEX = r"resolution=([^/\\]+)"
CHUNK_TS_REGEX = r"(\d+)\.json$"

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
