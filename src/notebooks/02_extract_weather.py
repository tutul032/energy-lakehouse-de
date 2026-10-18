# Databricks notebook source
# MAGIC %md
# MAGIC # 02_extract_weather
# MAGIC Download hourly Open-Meteo weather per location (raw JSON, unchanged) into the UC volume
# MAGIC `/Volumes/<catalog>/<schema>/landing/weather/location=<id>/run_date=<yyyy-mm-dd>.json`.
# MAGIC One file per location and UTC run date; existing files are skipped (idempotent).

# COMMAND ----------

import logging
import os
import sys

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse.utils import table_name
from energy_lakehouse.weather import extract
from energy_lakehouse.weather_config import DEFAULT_PAST_DAYS, LOCATIONS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")
dbutils.widgets.text("past_days", str(DEFAULT_PAST_DAYS))

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
past_days = int(dbutils.widgets.get("past_days"))

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {table_name(catalog, schema)}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {table_name(catalog, schema, 'landing')}")

base_dir = f"/Volumes/{catalog}/{schema}/landing"
print(f"Landing dir: {base_dir}, past_days: {past_days}")

# COMMAND ----------

summary = extract(base_dir, LOCATIONS, past_days)
print(summary)

if summary["failed"]:
    raise RuntimeError(f"Weather extract had failures: {summary}")
