# Databricks notebook source
# MAGIC %md
# MAGIC # 04_apply_contracts
# MAGIC Applies `contracts/*.yaml` to the gold tables of the target schema (task `apply_contracts`
# MAGIC of `daily_refresh_job`): table and column comments and tags where they differ from the
# MAGIC contract, plus `USE SCHEMA` / `SELECT` grants for every consumer group. Idempotent: comments
# MAGIC and tags are only written on drift, grants that exist are no-ops. Tags or consumers removed
# MAGIC from a contract are not revoked automatically.

# COMMAND ----------

import os
import sys

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse import contracts

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
print(f"Applying contracts to {catalog}.{schema}")

# COMMAND ----------

summary = []
errors = []

for contract in contracts.load_contracts():
    table = contract.table
    columns = spark.sql(contracts.columns_query(catalog, schema, table)).collect()
    if not columns:
        summary.append((table, contract.version, 0, 1))
        errors.append(f"{table}: table not found in {catalog}.{schema}")
        continue
    comment_row = spark.sql(contracts.table_comment_query(catalog, schema, table)).first()
    tag_rows = spark.sql(contracts.table_tags_query(catalog, schema, table)).collect()
    statements = contracts.apply_statements(
        contract,
        catalog,
        schema,
        table_comment=comment_row["comment"] if comment_row else None,
        column_comments={r["column_name"]: r["comment"] for r in columns},
        tags={r["tag_name"]: r["tag_value"] for r in tag_rows},
    )
    failed = 0
    for statement in statements:
        print(statement)
        try:
            spark.sql(statement)
        except Exception as exc:  # report every failing statement, not just the first
            failed += 1
            errors.append(f"{table}: {statement}\n  {contracts.short_error(exc)}")
    summary.append((table, contract.version, len(statements) - failed, failed))

# COMMAND ----------

spark.createDataFrame(
    summary, "table STRING, contract_version STRING, applied INT, failed INT"
).show(truncate=False)

if errors:
    raise RuntimeError(f"{len(errors)} contract statement(s) failed:\n" + "\n".join(errors))
print(f"OK: applied {len(summary)} contract(s) to {catalog}.{schema}")
