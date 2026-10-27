"""Gold: wide hourly and daily energy tables for dashboards and Genie."""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

from energy_lakehouse.transforms import (
    GOLD_ENERGY_DAILY_COLUMNS,
    GOLD_ENERGY_HOURLY_COLUMNS,
    GOLD_TABLE_COMMENTS,
    LOCAL_TZ,
    RENEWABLE_COLUMNS,
    ddl_schema,
    energy_columns,
    hourly_agg_specs,
)

ENERGY_COLS = energy_columns()

# Column lists live in transforms.py: the single source for these schemas and contracts/*.yaml.
HOURLY_COLUMNS = GOLD_ENERGY_HOURLY_COLUMNS
DAILY_COLUMNS = GOLD_ENERGY_DAILY_COLUMNS


@dp.materialized_view(
    name="gold_energy_hourly",
    comment=GOLD_TABLE_COMMENTS["gold_energy_hourly"],
    schema=ddl_schema(HOURLY_COLUMNS),
    # hour_local is TIMESTAMP_NTZ; Delta requires the timestampNtz table feature to be enabled
    # explicitly (else DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT).
    table_properties={"delta.feature.timestampNtz": "supported"},
)
def gold_energy_hourly():
    # Conditional aggregation instead of .pivot(): energy is summed, prices averaged, so
    # quarter-hour series roll up correctly too.
    aggs = [
        getattr(F, func)(F.when(F.col("series_name") == name, F.col("value"))).alias(col)
        for name, col, func in hourly_agg_specs()
    ]
    renewables = sum((F.col(c) for c in RENEWABLE_COLUMNS[1:]), F.col(RENEWABLE_COLUMNS[0]))
    return (
        spark.read.table("silver_energy_long")
        .groupBy(F.date_trunc("hour", "timestamp_utc").alias("hour_utc"))
        .agg(*aggs)
        .withColumn("hour_local", F.from_utc_timestamp("hour_utc", LOCAL_TZ).cast("timestamp_ntz"))
        .withColumn("renewables_mwh", renewables)
        .withColumn(
            "renewables_share",
            F.when(F.col("total_load_mwh") > 0, F.col("renewables_mwh") / F.col("total_load_mwh")),
        )
        .select(*[name for name, _, _ in HOURLY_COLUMNS])
    )


def _local_midnight_epoch(day):
    """Epoch seconds of 00:00 Europe/Berlin on ``day`` (session time zone is UTC)."""
    return F.to_utc_timestamp(day.cast("timestamp"), LOCAL_TZ).cast("long")


@dp.materialized_view(
    name="gold_energy_daily",
    comment=GOLD_TABLE_COMMENTS["gold_energy_daily"],
    schema=ddl_schema(DAILY_COLUMNS),
)
def gold_energy_daily():
    sums = [F.sum(c).alias(c) for c in [*ENERGY_COLS, "renewables_mwh"]]
    # Local midnight to next local midnight: 23/24/25 h (as transforms.expected_local_hours).
    next_day = F.date_add("date_local", 1)
    expected_hours = (
        (_local_midnight_epoch(next_day) - _local_midnight_epoch(F.col("date_local"))) / 3600
    ).cast("int")
    return (
        spark.read.table("gold_energy_hourly")
        .groupBy(F.to_date("hour_local").alias("date_local"))
        .agg(
            *sums,
            F.avg("price_eur_mwh").alias("price_avg_eur_mwh"),
            F.min("price_eur_mwh").alias("price_min_eur_mwh"),
            F.max("price_eur_mwh").alias("price_max_eur_mwh"),
            F.avg("renewables_share").alias("renewables_share_avg"),
            # Rows per day, including hours whose values SMARD has not published yet.
            F.count(F.lit(1)).alias("hour_count"),
            F.count("total_load_mwh").alias("load_hours"),
            F.count("price_eur_mwh").alias("price_hours"),
        )
        .withColumn("expected_hours", expected_hours)
        .withColumn(
            "is_complete",
            (F.col("load_hours") == F.col("expected_hours"))
            & (F.col("price_hours") == F.col("expected_hours")),
        )
        .select(*[name for name, _, _ in DAILY_COLUMNS])
    )
