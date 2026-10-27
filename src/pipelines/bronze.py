"""Bronze: raw SMARD chunks from the landing volume, one row per file (Auto Loader)."""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

from energy_lakehouse.transforms import CHUNK_TS_REGEX, FILTER_ID_REGEX, RESOLUTION_REGEX

LANDING_PATH = spark.conf.get("landing_path")

# meta_data is kept as raw JSON text; Spark serialises the nested object into the string.
RAW_SCHEMA = "meta_data STRING, series ARRAY<ARRAY<DOUBLE>>"


@dp.table(
    name="bronze_smard_raw",
    comment="Raw SMARD chart_data chunks ingested by Auto Loader; one row per landing file.",
)
def bronze_smard_raw():
    path = F.col("_metadata.file_path")
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("multiLine", "true")
        # The extractor re-downloads the newest SMARD chunks in place; without this, Auto Loader
        # ignores the rewritten files and late-published hours never reach silver.
        .option("cloudFiles.allowOverwrites", "true")
        # No partition inference: filter_id/resolution come from _metadata.file_path below, and
        # inferring key=value dirs fails (CF_PARTITON_INFERENCE_ERROR) on the shared landing path.
        .option("cloudFiles.partitionColumns", "")
        .schema(RAW_SCHEMA)
        .load(LANDING_PATH)
        # Explicit select: drops any Hive partition columns Auto Loader may add from the path.
        .select(
            F.regexp_extract(path, FILTER_ID_REGEX, 1).cast("int").alias("filter_id"),
            F.regexp_extract(path, RESOLUTION_REGEX, 1).alias("resolution"),
            F.regexp_extract(path, CHUNK_TS_REGEX, 1).cast("bigint").alias("chunk_ts_ms"),
            "meta_data",
            "series",
            path.alias("source_file"),
            F.col("_metadata.file_modification_time").alias("file_modification_time"),
            F.current_timestamp().alias("ingested_at"),
        )
    )
