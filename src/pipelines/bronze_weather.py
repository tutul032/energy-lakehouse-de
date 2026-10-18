"""Bronze: raw Open-Meteo responses from the landing volume, one row per file (Auto Loader)."""

import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F

# `root_path` (../src) is on sys.path already; this fallback covers older runtimes/CLIs.
_src = spark.conf.get("bundle.sourcePath", None)
if _src and _src not in sys.path:
    sys.path.append(_src)

from energy_lakehouse.transforms import LOCATION_REGEX, RUN_DATE_REGEX, weather_raw_schema

WEATHER_LANDING_PATH = spark.conf.get("weather_landing_path")


@dp.table(
    name="bronze_weather_raw",
    comment="Raw Open-Meteo hourly responses ingested by Auto Loader; one row per landing file.",
)
def bronze_weather_raw():
    path = F.col("_metadata.file_path")
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("multiLine", "true")
        # `extract --overwrite` rewrites a run_date file in place; re-ingest it when that happens.
        .option("cloudFiles.allowOverwrites", "true")
        .schema(weather_raw_schema())
        .load(WEATHER_LANDING_PATH)
        # Explicit select: drops any Hive partition columns Auto Loader may add from the path.
        .select(
            F.regexp_extract(path, LOCATION_REGEX, 1).alias("location_id"),
            F.to_date(F.regexp_extract(path, RUN_DATE_REGEX, 1)).alias("run_date"),
            "latitude",
            "longitude",
            "elevation",
            "hourly_units",
            "hourly",
            path.alias("source_file"),
            F.col("_metadata.file_modification_time").alias("file_modification_time"),
            F.current_timestamp().alias("ingested_at"),
        )
    )
