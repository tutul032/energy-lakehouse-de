"""Gold: wide hourly and daily energy tables for dashboards and Genie."""

import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F

# `root_path` (../src) is on sys.path already; this fallback covers older runtimes/CLIs.
_src = spark.conf.get("bundle.sourcePath", None)
if _src and _src not in sys.path:
    sys.path.append(_src)

from energy_lakehouse.transforms import (
    LOCAL_TZ,
    RENEWABLE_COLUMNS,
    ddl_schema,
    energy_columns,
    hourly_agg_specs,
)

ENERGY_COLS = energy_columns()

HOURLY_COLUMNS = [
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

DAILY_COLUMNS = [
    ("date_local", "DATE", f"Calendar day in {LOCAL_TZ}."),
    *[(c, "DOUBLE", f"Daily sum of {c} (MWh).") for c in [*ENERGY_COLS, "renewables_mwh"]],
    ("price_avg_eur_mwh", "DOUBLE", "Average hourly day-ahead price (EUR/MWh)."),
    ("price_min_eur_mwh", "DOUBLE", "Lowest hourly day-ahead price (EUR/MWh)."),
    ("price_max_eur_mwh", "DOUBLE", "Highest hourly day-ahead price (EUR/MWh)."),
    ("renewables_share_avg", "DOUBLE", "Average of the hourly renewables_share."),
    ("hour_count", "BIGINT", "Hours in the day: 24, or 23/25 on DST change days."),
]


@dp.materialized_view(
    name="gold_energy_hourly",
    comment="Hourly German load, generation by source and day-ahead price (one row per UTC hour).",
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


@dp.materialized_view(
    name="gold_energy_daily",
    comment=f"Daily German energy totals and price statistics by local calendar day ({LOCAL_TZ}).",
    schema=ddl_schema(DAILY_COLUMNS),
)
def gold_energy_daily():
    sums = [F.sum(c).alias(c) for c in [*ENERGY_COLS, "renewables_mwh"]]
    return (
        spark.read.table("gold_energy_hourly")
        .groupBy(F.to_date("hour_local").alias("date_local"))
        .agg(
            *sums,
            F.avg("price_eur_mwh").alias("price_avg_eur_mwh"),
            F.min("price_eur_mwh").alias("price_min_eur_mwh"),
            F.max("price_eur_mwh").alias("price_max_eur_mwh"),
            F.avg("renewables_share").alias("renewables_share_avg"),
            F.count(F.lit(1)).alias("hour_count"),
        )
        .select(*[name for name, _, _ in DAILY_COLUMNS])
    )
