# Databricks notebook source
# MAGIC %md
# MAGIC # hello
# MAGIC Smoke test: create the target schema if missing, append one row with the current
# MAGIC timestamp to `_smoke_test`, and print the row count.

# COMMAND ----------

import os
import sys

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse.utils import table_name

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
target = table_name(catalog, schema, "_smoke_test")
print(f"Target table: {target}")

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {table_name(catalog, schema)}")

(
    spark.sql("SELECT current_timestamp() AS run_ts")
    .write.format("delta")
    .mode("append")
    .saveAsTable(target)
)

row_count = spark.table(target).count()
print(f"{target} row count: {row_count}")
