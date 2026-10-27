"""Silver: hourly weather forecast per location from the newest daily run (issue-time view)."""

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

from energy_lakehouse.transforms import (
    SILVER_WEATHER_FORECAST_HOURLY_COLUMNS,
    SILVER_WEATHER_FORECAST_HOURLY_COMMENT,
    ddl_schema,
    weather_location_rows,
)
from energy_lakehouse.weather_config import HOURLY_VARIABLES

COLUMNS = SILVER_WEATHER_FORECAST_HOURLY_COLUMNS


@dp.materialized_view(
    name="silver_weather_forecast_hourly",
    comment=SILVER_WEATHER_FORECAST_HOURLY_COMMENT,
    schema=ddl_schema(COLUMNS),
)
@dp.expect_or_fail("valid_timestamp", "timestamp_utc IS NOT NULL")
@dp.expect_or_drop("has_measurements", "temperature_2m IS NOT NULL")
# Same style as silver_weather_hourly: NULLs belong to has_measurements, not the plausibility rules.
@dp.expect("plausible_temperature", "temperature_2m IS NULL OR temperature_2m BETWEEN -40 AND 50")
@dp.expect("plausible_wind", "wind_speed_100m IS NULL OR wind_speed_100m BETWEEN 0 AND 75")
def silver_weather_forecast_hourly():
    locations = spark.createDataFrame(
        weather_location_rows(), "location_id STRING, location_name STRING, role STRING"
    )
    file_cols = [
        "location_id",
        "run_date",
        "run_ts_utc",
        "source_file",
        "file_modification_time",
        "ingested_at",
    ]
    array_cols = ["time", *HOURLY_VARIABLES]
    # One file per location: the newest daily run by run_ts_utc (archive files have run_date
    # NULL). Ties (old run_date= naming) go to the newest copy.
    newest_run = Window.partitionBy("location_id").orderBy(
        F.col("run_ts_utc").desc(),
        F.col("file_modification_time").desc(),
        F.col("ingested_at").desc(),
        F.col("source_file").desc(),
    )
    newest_files = (
        spark.read.table("bronze_weather_raw")
        .where("run_date IS NOT NULL")
        .withColumn("_rn", F.row_number().over(newest_run))
        .where("_rn = 1")
    )
    return (
        newest_files
        # Flatten first so arrays_zip names the struct fields after the top-level columns.
        .select(*file_cols, *[F.col(f"hourly.{c}").alias(c) for c in array_cols])
        .select(*file_cols, F.explode(F.arrays_zip(*array_cols)).alias("p"))
        .select(
            *file_cols,
            F.col("p.time").alias("ts_s"),
            F.timestamp_seconds(F.col("p.time")).alias("timestamp_utc"),
            *[F.col(f"p.{v}").alias(v) for v in HOURLY_VARIABLES],
        )
        # Keep only hours after the issue time (the file's run timestamp): what was known as a
        # forecast. NULL timestamps are kept so valid_timestamp can fail on them.
        .where("timestamp_utc IS NULL OR timestamp_utc > run_ts_utc")
        .join(F.broadcast(locations), "location_id", "left")
        .select(*[name for name, _, _ in COLUMNS])
    )
