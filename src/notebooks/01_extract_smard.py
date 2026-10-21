# Databricks notebook source
# MAGIC %md
# MAGIC # 01_extract_smard
# MAGIC Download the latest weekly SMARD chunks (raw JSON, unchanged) into the shared landing
# MAGIC volume `<landing_root>/smard/...` (default `/Volumes/workspace/energy_raw/landing`, used by
# MAGIC all targets). Existing files are skipped (idempotent), except the `refresh_latest` newest
# MAGIC chunks per series, which are always re-downloaded so late-published hours get filled.
# MAGIC The volume is not created here; it must already exist.

# COMMAND ----------

import logging
import os
import sys

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse.smard import DEFAULT_REFRESH_LATEST, extract
from energy_lakehouse.smard_config import DEFAULT_RESOLUTION, SERIES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)

# COMMAND ----------

dbutils.widgets.text("landing_root", "/Volumes/workspace/energy_raw/landing")
dbutils.widgets.text("weeks", "8")
dbutils.widgets.text("refresh_latest", str(DEFAULT_REFRESH_LATEST))

base_dir = dbutils.widgets.get("landing_root").rstrip("/")
weeks = int(dbutils.widgets.get("weeks"))
refresh_latest = int(dbutils.widgets.get("refresh_latest"))

# COMMAND ----------

if not os.path.isdir(base_dir):
    raise RuntimeError(f"Landing volume {base_dir} not found (create it once, outside the bundle).")

print(f"Landing dir: {base_dir}, weeks: {weeks}, refresh_latest: {refresh_latest}")

# COMMAND ----------

summary = extract(base_dir, SERIES, DEFAULT_RESOLUTION, weeks, refresh_latest=refresh_latest)
print(summary)

if summary["failed"]:
    raise RuntimeError(f"SMARD extract had failures: {summary}")
