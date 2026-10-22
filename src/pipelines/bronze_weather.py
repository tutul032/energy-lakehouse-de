"""Bronze: raw Open-Meteo responses from the landing volume, one row per file (Auto Loader)."""

import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F

# `root_path` (../src) is on sys.path already; this fallback covers older runtimes/CLIs.
_src = spark.conf.get("bundle.sourcePath", None)
if _src and _src not in sys.path:
    sys.path.append(_src)

from energy_lakehouse.transforms import (
    ARCHIVE_RUN_REGEX,
    LOCATION_REGEX,
    RUN_DATE_REGEX,
    RUN_TS_REGEX,
    RUN_TS_SPARK_FORMAT,
    weather_raw_schema,
)

WEATHER_LANDING_PATH = spark.conf.get("weather_landing_path")


@dp.table(
    name="bronze_weather_raw",
    comment="Raw Open-Meteo hourly responses ingested by Auto Loader; one row per landing file.",
)
def bronze_weather_raw():
    path = F.col("_metadata.file_path")
    run_ts = F.regexp_extract(path, RUN_TS_REGEX, 1)
    run_date = F.regexp_extract(path, RUN_DATE_REGEX, 1)
    archive_end = F.regexp_extract(path, ARCHIVE_RUN_REGEX, 2)
    # Same rules as transforms.parse_weather_landing_path. The `!= ""` guards keep to_timestamp('')
    # from failing under ANSI mode.
    run_ts_utc = (
        F.when(run_ts != "", F.to_timestamp(run_ts, RUN_TS_SPARK_FORMAT))
        .when(run_date != "", F.to_timestamp(run_date))  # old daily naming: 00:00 UTC
        .when(archive_end != "", F.to_timestamp(F.date_add(F.to_date(archive_end), 1)))
    )
    is_archive = archive_end != ""
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("multiLine", "true")
        # `extract --overwrite` rewrites an archive file in place; re-ingest it when that happens.
        .option("cloudFiles.allowOverwrites", "true")
        # No partition inference: location_id/run_ts come from _metadata.file_path below, and
        # inferring key=value dirs fails (CF_PARTITON_INFERENCE_ERROR) on the shared landing path.
        .option("cloudFiles.partitionColumns", "")
        .schema(weather_raw_schema())
        .load(WEATHER_LANDING_PATH)
        # Explicit select: drops any Hive partition columns Auto Loader may add from the path.
        .select(
            F.regexp_extract(path, LOCATION_REGEX, 1).alias("location_id"),
            # Archive backfill files (run_date=archive_<start>_<end>.json) get NULL: they are not
            # a forecast run.
            F.when(~is_archive, F.to_date(run_ts_utc)).alias("run_date"),
            run_ts_utc.alias("run_ts_utc"),
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
