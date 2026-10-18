"""Gold: hourly energy joined with weather aggregated by location role."""

import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F

# `root_path` (../src) is on sys.path already; this fallback covers older runtimes/CLIs.
_src = spark.conf.get("bundle.sourcePath", None)
if _src and _src not in sys.path:
    sys.path.append(_src)

from energy_lakehouse.transforms import (
    GOLD_HOURLY_COLUMNS,
    ddl_schema,
    gold_weather_columns,
    weather_role_agg_specs,
)

WEATHER_SPECS = weather_role_agg_specs()
COLUMNS = gold_weather_columns(WEATHER_SPECS)


@dp.materialized_view(
    name="gold_energy_weather_hourly",
    comment=(
        "gold_energy_hourly (one row per UTC hour) left-joined with Open-Meteo weather "
        "aggregated by location role: load-centre temperature, onshore/offshore 100 m wind, "
        "solar radiation. Weather columns are NULL where no observation exists yet."
    ),
    schema=ddl_schema(COLUMNS),
    # hour_local is TIMESTAMP_NTZ; Delta requires the timestampNtz table feature to be enabled
    # explicitly (else DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT).
    table_properties={"delta.feature.timestampNtz": "supported"},
)
def gold_energy_weather_hourly():
    # Conditional aggregation: each column only sees locations with its role.
    aggs = [
        getattr(F, s.func)(F.when(F.col("role") == s.role, F.col(s.variable))).alias(s.column)
        for s in WEATHER_SPECS
    ]
    weather = (
        spark.read.table("silver_weather_hourly")
        .groupBy(F.date_trunc("hour", "timestamp_utc").alias("hour_utc"))
        .agg(*aggs)
    )
    return (
        spark.read.table("gold_energy_hourly")
        .select(*[name for name, _, _ in GOLD_HOURLY_COLUMNS])
        .join(weather, "hour_utc", "left")
        .select(*[name for name, _, _ in COLUMNS])
    )
