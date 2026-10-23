# Databricks notebook source
# MAGIC %md
# MAGIC # 05_verify_contracts
# MAGIC Verifies the contracted tables of the target schema against `contracts/*.yaml` (task
# MAGIC `verify_contracts` of `daily_refresh_job`): (a) schema via `information_schema.columns`
# MAGIC (missing/extra columns, type mismatches), (b) quality rules (violation % above
# MAGIC `max_violation_pct`), (c) freshness (`sla.freshness_column` older than
# MAGIC `sla.freshness_hours`). Prints one result table and fails listing every failed check.
# MAGIC Contracts with status `draft` or `retired` are skipped (`contracts.is_enforced`).

# COMMAND ----------

import os
import sys
from datetime import UTC, datetime

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse import contracts, quality
from energy_lakehouse.quality import CheckResult

# Collected timestamps are naive; with a UTC session they are UTC (freshness relies on it).
spark.conf.set("spark.sql.session.timeZone", "UTC")

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
print(f"Verifying contracts in {catalog}.{schema}")

# COMMAND ----------

now = datetime.now(UTC)
results = []

for contract in contracts.load_contracts():
    table = contract.table
    if not contracts.is_enforced(contract):
        print(f"SKIPPED {table}: contract status {contract.status}")
        continue
    columns = spark.sql(contracts.columns_query(catalog, schema, table)).collect()
    results.append(
        contracts.schema_check(contract, [(r["column_name"], r["full_data_type"]) for r in columns])
    )
    if not columns:
        continue  # table missing: the schema check already failed, other queries would too

    for rule in contracts.quality_rules(contract):
        try:
            row = spark.sql(contracts.quality_sql(contract, rule, catalog, schema)).first()
            results.append(
                contracts.quality_result(
                    contract, rule, int(row["total_rows"]), int(row["violations"])
                )
            )
        except Exception as exc:  # e.g. a rule referencing a missing column
            name = f"{table} {rule.name}"
            results.append(CheckResult(name, False, f"query failed: {contracts.short_error(exc)}"))

    try:
        max_ts = spark.sql(contracts.freshness_sql(contract, catalog, schema)).first()["max_ts"]
        results.append(contracts.freshness_result(contract, max_ts, now))
    except Exception as exc:
        name = f"{table} freshness"
        results.append(CheckResult(name, False, f"query failed: {contracts.short_error(exc)}"))

# COMMAND ----------

spark.createDataFrame(
    quality.summary_rows(results), "check STRING, status STRING, detail STRING"
).show(len(results), truncate=False)

quality.raise_on_failures(results)
print(f"OK: all {len(results)} contract checks passed")
