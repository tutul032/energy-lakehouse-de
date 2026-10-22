# Databricks notebook source
# MAGIC %md
# MAGIC # 11_score_forecast
# MAGIC Issues the day-ahead forecast (task `score_forecast` of `daily_refresh_job`): for each
# MAGIC target loads `models:/<catalog>.<schema>.forecaster_<target>@champion`, builds features
# MAGIC for every hour of the local (Europe/Berlin) day D+1 from `gold_energy_weather_hourly`
# MAGIC (history up to now) and `gold_weather_forecast_hourly` (weather issued before now), and
# MAGIC upserts `gold_forecast_hourly` with MERGE on (target_hour_utc, target, issue_date).
# MAGIC Idempotent: a re-run on the same day replaces that day's rows. A target without a champion
# MAGIC is skipped with a warning (train it with `train_forecast_job` first).

# COMMAND ----------

# MAGIC %pip install -q -r forecast_requirements.txt

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import sys

import mlflow
import pandas as pd
from mlflow.tracking import MlflowClient

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse import forecast
from energy_lakehouse.transforms import GOLD_FORECAST_HOURLY_COLUMNS, GOLD_TABLE_COMMENTS
from energy_lakehouse.utils import table_name

# Collected timestamps are naive; with a UTC session they are UTC (forecast.to_utc relies on it).
spark.conf.set("spark.sql.session.timeZone", "UTC")

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")


def fqn(table: str) -> str:
    return table_name(catalog, schema, table)


issue_time = pd.Timestamp.now(tz="UTC").floor("min")
print(
    f"Issuing forecasts in {catalog}.{schema} at {issue_time} for local day "
    f"{forecast.target_hours(issue_time)[0].tz_convert('Europe/Berlin').date()}"
)

# COMMAND ----------

history = (
    spark.table(fqn("gold_energy_weather_hourly")).select("hour_utc", *forecast.TARGETS).toPandas()
)
weather = (
    spark.table(fqn("gold_weather_forecast_hourly"))
    .select("hour_utc", *forecast.WEATHER_FEATURES, "issued_at")
    .toPandas()
)

target_table = fqn("gold_forecast_hourly")
spark.sql(
    forecast.create_table_sql(
        target_table, GOLD_FORECAST_HOURLY_COLUMNS, GOLD_TABLE_COMMENTS["gold_forecast_hourly"]
    )
)

# COMMAND ----------

mlflow.set_registry_uri("databricks-uc")
client = MlflowClient()
frames = []

for target in forecast.TARGETS:
    name = forecast.model_name(catalog, schema, target)
    try:
        version = client.get_model_version_by_alias(name, forecast.CHAMPION_ALIAS).version
    except Exception as exc:  # no registered model or no champion yet
        print(f"WARNING: skipping {target}: no {name}@{forecast.CHAMPION_ALIAS} ({exc})")
        continue
    model = mlflow.pyfunc.load_model(f"models:/{name}@{forecast.CHAMPION_ALIAS}")
    features = forecast.build_features(history, weather, issue_time, target)
    missing_weather = int(features[list(forecast.WEATHER_FEATURES)].isna().any(axis=1).sum())
    if missing_weather:
        print(f"WARNING: {target}: {missing_weather} target hours without weather forecast")
    predictions = model.predict(features[list(forecast.FEATURE_COLUMNS)])
    frames.append(forecast.forecast_rows(features, predictions, target, version))
    print(f"{target}: {len(features)} hours from {name} v{version}")

# COMMAND ----------

if frames:
    rows = pd.concat(frames, ignore_index=True)
    updates = spark.createDataFrame(rows, forecast.spark_schema(GOLD_FORECAST_HOURLY_COLUMNS))
    updates.createOrReplaceTempView(forecast.STAGING_VIEW)
    spark.sql(forecast.merge_forecast_sql(target_table))
    print(f"OK: merged {len(rows)} forecast rows into {target_table}")
else:
    print(f"No champion models in {catalog}.{schema}; nothing scored")
