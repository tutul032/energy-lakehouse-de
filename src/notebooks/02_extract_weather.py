# Databricks notebook source
# MAGIC %md
# MAGIC # 02_extract_weather
# MAGIC Download hourly Open-Meteo weather per location (raw JSON, unchanged) into the shared
# MAGIC landing volume `<landing_root>/weather/location=<id>/run_date=<yyyy-mm-dd>.json` (default
# MAGIC `/Volumes/workspace/energy_raw/landing`, used by all targets). One file per location and UTC
# MAGIC run date; existing files are skipped (idempotent). The volume must already exist.

# COMMAND ----------

import logging
import os
import sys

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse.weather import extract
from energy_lakehouse.weather_config import DEFAULT_PAST_DAYS, LOCATIONS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)

# COMMAND ----------

dbutils.widgets.text("landing_root", "/Volumes/workspace/energy_raw/landing")
dbutils.widgets.text("past_days", str(DEFAULT_PAST_DAYS))

base_dir = dbutils.widgets.get("landing_root").rstrip("/")
past_days = int(dbutils.widgets.get("past_days"))

# COMMAND ----------

if not os.path.isdir(base_dir):
    raise RuntimeError(f"Landing volume {base_dir} not found (create it once, outside the bundle).")

print(f"Landing dir: {base_dir}, past_days: {past_days}")

# COMMAND ----------

summary = extract(base_dir, LOCATIONS, past_days)
print(summary)

if summary["failed"]:
    raise RuntimeError(f"Weather extract had failures: {summary}")
