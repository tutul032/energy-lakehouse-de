# Databricks notebook source
# MAGIC %md
# MAGIC # 20_detect_anomalies
# MAGIC ML-assisted data quality (task `detect_anomalies` of `daily_refresh_job`). Reads
# MAGIC `silver_energy_long`, `gold_energy_weather_hourly` and `gold_forecast_accuracy` into pandas
# MAGIC and runs the detectors of `energy_lakehouse.anomalies` on the last 7 days: seasonal robust
# MAGIC z per SMARD series (price excluded), an IsolationForest over the hourly gold features,
# MAGIC completeness per series and local day, the solar-at-night rule and forecast drift.
# MAGIC MERGEs the rows into `gold_dq_anomalies` on (check_name, entity, ts_utc) and replaces the
# MAGIC day's counts in `gold_dq_daily_summary`. Fails if an input window is empty or a critical
# MAGIC anomaly was found (entities starting with `TEST:` excluded).
# MAGIC
# MAGIC `inject_test_anomaly = true` (dev schemas only) adds the series `TEST:total_load`, a copy of
# MAGIC `total_load` with the most recent complete hour halved, so the pipeline can be tested
# MAGIC end to end without touching real data.

# COMMAND ----------

import os
import sys
from datetime import timedelta

import pandas as pd

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse import anomalies, forecast
from energy_lakehouse.smard_config import SERIES
from energy_lakehouse.transforms import (
    GOLD_DQ_ANOMALIES_COLUMNS,
    GOLD_DQ_DAILY_SUMMARY_COLUMNS,
    GOLD_TABLE_COMMENTS,
)
from energy_lakehouse.utils import table_name

# Collected timestamps are naive; with a UTC session they are UTC (forecast.to_utc relies on it).
spark.conf.set("spark.sql.session.timeZone", "UTC")

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")
dbutils.widgets.text("inject_test_anomaly", "false")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
inject = dbutils.widgets.get("inject_test_anomaly").strip().lower() == "true"
if inject and not schema.endswith("_dev"):
    raise ValueError(f"inject_test_anomaly is for dev schemas only, not {schema}")


def fqn(table: str) -> str:
    return table_name(catalog, schema, table)


now = pd.Timestamp.now(tz="UTC").floor("min")
today = spark.sql(f"SELECT {anomalies.LOCAL_TODAY_SQL} AS d").first()["d"]
score_from = now - pd.Timedelta(days=anomalies.SCORE_DAYS)
print(f"Detecting anomalies in {catalog}.{schema} at {now} (local day {today}), inject={inject}")

# COMMAND ----------

# Inputs: SMARD long (8 weeks of history before the scored week), gold hourly (365 days).
silver_from = score_from - pd.Timedelta(weeks=anomalies.SEASONAL_WINDOW_WEEKS, days=1)
silver = (
    spark.table(fqn("silver_energy_long"))
    .where(f"timestamp_utc >= TIMESTAMP '{silver_from:%Y-%m-%d %H:%M:%S}'")
    .selectExpr("series_name AS entity", "timestamp_utc AS ts_utc", "value")
    .toPandas()
)
silver["ts_utc"] = forecast.to_utc(silver["ts_utc"])

gold_from = now - pd.Timedelta(days=anomalies.IF_TRAIN_DAYS + 1)
gold = (
    spark.table(fqn("gold_energy_weather_hourly"))
    .where(f"hour_utc >= TIMESTAMP '{gold_from:%Y-%m-%d %H:%M:%S}'")
    .select("hour_utc", "hour_local", *anomalies.IF_GOLD_COLUMNS)
    .toPandas()
)
gold["hour_utc"] = forecast.to_utc(gold["hour_utc"])

accuracy_table = fqn("gold_forecast_accuracy")
accuracy = (
    spark.table(accuracy_table).toPandas() if spark.catalog.tableExists(accuracy_table) else None
)

# Never "succeed" without having looked at data.
n_silver = int((silver["ts_utc"] >= score_from).sum())
gold_recent = gold[gold["hour_utc"] >= score_from]
empty = [
    name for name, n in (("silver_energy_long", n_silver), ("gold", len(gold_recent))) if not n
]
if empty:
    raise RuntimeError(f"No rows since {score_from} in {', '.join(empty)}: nothing to check")
print(
    f"silver rows: {len(silver)} ({n_silver} scored), gold hours: {len(gold)} "
    f"({len(gold_recent)} scored), accuracy rows: {0 if accuracy is None else len(accuracy)}"
)

# COMMAND ----------

seasonal_input = silver[~silver["entity"].isin(anomalies.SEASONAL_EXCLUDED_SERIES)]
if inject:
    seasonal_input = anomalies.inject_test_anomaly(seasonal_input, now)

frames = {
    anomalies.SEASONAL_Z: anomalies.seasonal_robust_z(
        seasonal_input, "value", score_from=score_from
    ),
    anomalies.ISOLATION_FOREST: anomalies.multivariate_isolation_forest(
        gold, score_from=score_from
    ),
    anomalies.COMPLETENESS: anomalies.completeness(
        silver, today, entities=[s.name for s in SERIES]
    ),
    anomalies.SOLAR_AT_NIGHT: anomalies.physics_rules(gold_recent),
    # Runs may start earlier; only issue dates of the last 7 local days are reported.
    "forecast_drift": anomalies.forecast_drift(
        accuracy, since=today - timedelta(days=anomalies.SCORE_DAYS)
    ),
}
for name, frame in frames.items():
    print(f"{name}: {len(frame)} rows")
rows = anomalies.combine(frames.values())

# COMMAND ----------

anomaly_table = fqn("gold_dq_anomalies")
summary_table = fqn("gold_dq_daily_summary")
spark.sql(
    forecast.create_table_sql(
        anomaly_table, GOLD_DQ_ANOMALIES_COLUMNS, GOLD_TABLE_COMMENTS["gold_dq_anomalies"]
    )
)
spark.sql(
    forecast.create_table_sql(
        summary_table, GOLD_DQ_DAILY_SUMMARY_COLUMNS, GOLD_TABLE_COMMENTS["gold_dq_daily_summary"]
    )
)

if not rows.empty:
    updates = spark.createDataFrame(
        anomalies.to_spark_rows(rows, now), forecast.spark_schema(GOLD_DQ_ANOMALIES_COLUMNS)
    )
    updates.createOrReplaceTempView(anomalies.ANOMALY_VIEW)
    spark.sql(anomalies.merge_anomalies_sql(anomaly_table))
print(f"OK: merged {len(rows)} anomaly rows into {anomaly_table}")

summary = anomalies.summary_rows(rows, today)
spark.createDataFrame(
    summary, forecast.spark_schema(GOLD_DQ_DAILY_SUMMARY_COLUMNS)
).createOrReplaceTempView(anomalies.SUMMARY_VIEW)
spark.sql(anomalies.replace_summary_sql(summary_table, today))
print(f"OK: wrote {len(summary)} summary rows for {today} into {summary_table}")

# COMMAND ----------

print(summary.to_string(index=False))
if not rows.empty:
    print(rows[["check_name", "entity", "ts_utc", "severity", "score", "reason"]].head(20))

failures = anomalies.critical_failures(rows)
if failures:
    raise RuntimeError(
        f"{len(failures)} critical data-quality anomalies:\n"
        + "\n".join(f"- {f}" for f in failures)
    )
print("OK: no critical anomalies (TEST: entities excluded)")
