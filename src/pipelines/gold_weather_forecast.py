"""Gold: day-ahead weather forecast aggregated by location role, one row per future hour."""

import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F

# `root_path` (../src) is on sys.path already; this fallback covers older runtimes/CLIs.
_src = spark.conf.get("bundle.sourcePath", None)
if _src and _src not in sys.path:
    sys.path.append(_src)

from energy_lakehouse.transforms import (
    GOLD_TABLE_COMMENTS,
    GOLD_WEATHER_FORECAST_HOURLY_COLUMNS,
    LOCAL_TZ,
    ddl_schema,
    weather_role_agg_specs,
)

# Same WEATHER_ROLE_AGGS as gold_energy_weather_hourly, so forecast and observed columns match.
WEATHER_SPECS = weather_role_agg_specs()
COLUMNS = GOLD_WEATHER_FORECAST_HOURLY_COLUMNS


@dp.materialized_view(
    name="gold_weather_forecast_hourly",
    comment=GOLD_TABLE_COMMENTS["gold_weather_forecast_hourly"],
    schema=ddl_schema(COLUMNS),
    # hour_local is TIMESTAMP_NTZ; Delta requires the timestampNtz table feature to be enabled
    # explicitly (else DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT).
    table_properties={"delta.feature.timestampNtz": "supported"},
)
def gold_weather_forecast_hourly():
    # Conditional aggregation: each column only sees locations with its role.
    aggs = [
        getattr(F, s.func)(F.when(F.col("role") == s.role, F.col(s.variable))).alias(s.column)
        for s in WEATHER_SPECS
    ]
    return (
        spark.read.table("silver_weather_forecast_hourly")
        .groupBy(F.date_trunc("hour", "timestamp_utc").alias("hour_utc"))
        .agg(*aggs, F.max("run_ts_utc").alias("issued_at"))
        .withColumn("hour_local", F.from_utc_timestamp("hour_utc", LOCAL_TZ).cast("timestamp_ntz"))
        .select(*[name for name, _, _ in COLUMNS])
    )
