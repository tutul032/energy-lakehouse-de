"""Gold: hourly energy joined with weather aggregated by location role."""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

from energy_lakehouse.transforms import (
    GOLD_ENERGY_HOURLY_COLUMNS,
    GOLD_ENERGY_WEATHER_HOURLY_COLUMNS,
    GOLD_TABLE_COMMENTS,
    ddl_schema,
    weather_role_agg_specs,
)

WEATHER_SPECS = weather_role_agg_specs()
# Built from WEATHER_ROLE_AGGS in transforms.py; also the column list of the data contract.
COLUMNS = GOLD_ENERGY_WEATHER_HOURLY_COLUMNS


@dp.materialized_view(
    name="gold_energy_weather_hourly",
    comment=GOLD_TABLE_COMMENTS["gold_energy_weather_hourly"],
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
        .select(*[name for name, _, _ in GOLD_ENERGY_HOURLY_COLUMNS])
        .join(weather, "hour_utc", "left")
        .select(*[name for name, _, _ in COLUMNS])
    )
