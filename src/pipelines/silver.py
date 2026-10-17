"""Silver: one row per (series, timestamp), deduplicated across overlapping chunk files."""

import sys

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

# `root_path` (../src) is on sys.path already; this fallback covers older runtimes/CLIs.
_src = spark.conf.get("bundle.sourcePath", None)
if _src and _src not in sys.path:
    sys.path.append(_src)

from energy_lakehouse.transforms import series_metadata_rows


@dp.materialized_view(
    name="silver_energy_long",
    comment=(
        "SMARD values in long format (one row per filter_id and timestamp_utc), joined with "
        "series name/unit; the newest landing file wins on duplicates."
    ),
)
@dp.expect_or_drop("value_published", "value IS NOT NULL")
@dp.expect_or_fail("valid_timestamp", "timestamp_utc IS NOT NULL")
# Each expectation checks one thing only: NULL values are value_published's job, so they pass
# here instead of being counted twice in the expectation metrics.
@dp.expect("non_negative_energy", "value IS NULL OR unit <> 'MWh' OR value >= 0")
def silver_energy_long():
    metadata = spark.createDataFrame(
        series_metadata_rows(), "filter_id INT, series_name STRING, unit STRING"
    )
    points = (
        spark.read.table("bronze_smard_raw")
        .select(
            "filter_id",
            "resolution",
            "source_file",
            "file_modification_time",
            "ingested_at",
            F.explode("series").alias("point"),
        )
        .select(
            "*",
            F.col("point")[0].cast("bigint").alias("ts_ms"),
            F.col("point")[1].alias("value"),
        )
        .withColumn("timestamp_utc", F.timestamp_millis("ts_ms"))
        .drop("point")
    )
    newest_first = Window.partitionBy("filter_id", "timestamp_utc").orderBy(
        F.col("file_modification_time").desc(), F.col("source_file").desc()
    )
    return (
        points.withColumn("_rn", F.row_number().over(newest_first))
        .where("_rn = 1")
        .join(F.broadcast(metadata), "filter_id", "left")
        .select(
            "filter_id",
            "series_name",
            "unit",
            "resolution",
            "ts_ms",
            "timestamp_utc",
            "value",
            "source_file",
            "file_modification_time",
            "ingested_at",
        )
    )
