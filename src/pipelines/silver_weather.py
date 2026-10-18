"""Silver: hourly weather per location, deduplicated across overlapping daily responses."""

import sys

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

# `root_path` (../src) is on sys.path already; this fallback covers older runtimes/CLIs.
_src = spark.conf.get("bundle.sourcePath", None)
if _src and _src not in sys.path:
    sys.path.append(_src)

from energy_lakehouse.transforms import weather_location_rows
from energy_lakehouse.weather_config import HOURLY_VARIABLES


@dp.materialized_view(
    name="silver_weather_hourly",
    comment=(
        "Open-Meteo hourly weather in long format (one row per location_id and timestamp_utc), "
        "joined with location name/role; newest landing file wins, forecast hours dropped."
    ),
)
@dp.expect_or_fail("valid_timestamp", "timestamp_utc IS NOT NULL")
@dp.expect_or_drop("has_measurements", "temperature_2m IS NOT NULL")
# Each expectation checks one thing only: NULLs are has_measurements' job (or, for wind, simply
# missing), so they pass the plausibility rules instead of being counted twice.
@dp.expect("plausible_temperature", "temperature_2m IS NULL OR temperature_2m BETWEEN -40 AND 50")
@dp.expect("plausible_wind", "wind_speed_100m IS NULL OR wind_speed_100m BETWEEN 0 AND 75")
def silver_weather_hourly():
    locations = spark.createDataFrame(
        weather_location_rows(), "location_id STRING, location_name STRING, role STRING"
    )
    file_cols = ["location_id", "run_date", "source_file", "file_modification_time", "ingested_at"]
    array_cols = ["time", *HOURLY_VARIABLES]
    points = (
        spark.read.table("bronze_weather_raw")
        # Flatten first so arrays_zip names the struct fields after the top-level columns.
        .select(*file_cols, *[F.col(f"hourly.{c}").alias(c) for c in array_cols])
        .select(*file_cols, F.explode(F.arrays_zip(*array_cols)).alias("p"))
        .select(
            *file_cols,
            F.col("p.time").alias("ts_s"),
            F.timestamp_seconds(F.col("p.time")).alias("timestamp_utc"),
            *[F.col(f"p.{v}").alias(v) for v in HOURLY_VARIABLES],
        )
        # Drop forecast hours. NULL timestamps are kept so valid_timestamp can fail on them.
        .where("timestamp_utc IS NULL OR timestamp_utc <= current_timestamp()")
    )
    newest_first = Window.partitionBy("location_id", "timestamp_utc").orderBy(
        F.col("file_modification_time").desc(),
        F.col("run_date").desc(),
        F.col("source_file").desc(),
    )
    return (
        points.withColumn("_rn", F.row_number().over(newest_first))
        .where("_rn = 1")
        .join(F.broadcast(locations), "location_id", "left")
        .select(
            "location_id",
            "location_name",
            "role",
            "ts_s",
            "timestamp_utc",
            *HOURLY_VARIABLES,
            "run_date",
            "source_file",
            "file_modification_time",
            "ingested_at",
        )
    )
