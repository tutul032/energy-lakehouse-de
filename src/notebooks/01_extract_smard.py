# Databricks notebook source
# MAGIC %md
# MAGIC # 01_extract_smard
# MAGIC Download the latest weekly SMARD chunks (raw JSON, unchanged) into the UC volume
# MAGIC `/Volumes/<catalog>/<schema>/landing/smard/...`. Existing files are skipped (idempotent),
# MAGIC except the `refresh_latest` newest chunks per series, which are always re-downloaded so
# MAGIC late-published hours get filled.

# COMMAND ----------

import logging
import os
import sys

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse.smard import DEFAULT_REFRESH_LATEST, extract
from energy_lakehouse.smard_config import DEFAULT_RESOLUTION, SERIES
from energy_lakehouse.utils import table_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")
dbutils.widgets.text("weeks", "8")
dbutils.widgets.text("refresh_latest", str(DEFAULT_REFRESH_LATEST))

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
weeks = int(dbutils.widgets.get("weeks"))
refresh_latest = int(dbutils.widgets.get("refresh_latest"))

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {table_name(catalog, schema)}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {table_name(catalog, schema, 'landing')}")

base_dir = f"/Volumes/{catalog}/{schema}/landing"
print(f"Landing dir: {base_dir}, weeks: {weeks}, refresh_latest: {refresh_latest}")

# COMMAND ----------

summary = extract(base_dir, SERIES, DEFAULT_RESOLUTION, weeks, refresh_latest=refresh_latest)
print(summary)

if summary["failed"]:
    raise RuntimeError(f"SMARD extract had failures: {summary}")
