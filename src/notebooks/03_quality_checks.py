# Databricks notebook source
# MAGIC %md
# MAGIC # 03_quality_checks
# MAGIC Post-refresh checks (task `quality_checks` of `daily_refresh_job`). Fails the task with a
# MAGIC clear message if `gold_energy_hourly` is older than 72 h, `gold_energy_weather_hourly` has
# MAGIC no weather in the last 7 days, or `silver_energy_long` / `silver_weather_hourly` is empty.
# MAGIC Thresholds live in `energy_lakehouse.quality`.

# COMMAND ----------

from datetime import UTC, datetime

from energy_lakehouse import quality
from energy_lakehouse.transforms import WEATHER_ROLE_AGGS
from energy_lakehouse.utils import table_name

# Collected timestamps are naive; with a UTC session they are UTC (quality._as_utc relies on it).
spark.conf.set("spark.sql.session.timeZone", "UTC")

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
print(f"Checking {catalog}.{schema}")


def fqn(table: str) -> str:
    return table_name(catalog, schema, table)


# COMMAND ----------

now = datetime.now(UTC)
results = []

for table in ("silver_energy_long", "silver_weather_hourly"):
    results.append(quality.non_empty_check(table, spark.table(fqn(table)).count()))

max_hour = spark.sql(f"SELECT max(hour_utc) AS m FROM {fqn('gold_energy_hourly')}").first()["m"]
results.append(quality.freshness_check("gold_energy_hourly", max_hour, now))

weather_sql = quality.recent_weather_rows_sql(
    fqn("gold_energy_weather_hourly"), [a.column for a in WEATHER_ROLE_AGGS]
)
weather_rows = spark.sql(weather_sql).first()["n"]
results.append(quality.recent_weather_check("gold_energy_weather_hourly", weather_rows))

# COMMAND ----------

summary = spark.createDataFrame(
    quality.summary_rows(results), "check STRING, status STRING, detail STRING"
)
summary.show(truncate=False)

quality.raise_on_failures(results)
print(f"OK: all {len(results)} quality checks passed")
