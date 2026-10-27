# Databricks notebook source
# MAGIC %md
# MAGIC # 12_forecast_accuracy
# MAGIC Rebuilds `gold_forecast_accuracy` (task `forecast_accuracy` of `daily_refresh_job`): joins
# MAGIC `gold_forecast_hourly` with the actuals in `gold_energy_hourly` and computes, per target
# MAGIC and issue_date, `n_hours`, `mae`, `mape` (NULL for price) and the same errors of the
# MAGIC seasonal naive baseline (actual 168 h earlier) for the same hours. `INSERT OVERWRITE` keeps
# MAGIC the table (comments, tags, grants) and refreshes days whose actuals arrived late.

# COMMAND ----------


from energy_lakehouse import forecast
from energy_lakehouse.transforms import (
    GOLD_FORECAST_ACCURACY_COLUMNS,
    GOLD_FORECAST_HOURLY_COLUMNS,
    GOLD_TABLE_COMMENTS,
)
from energy_lakehouse.utils import table_name

spark.conf.set("spark.sql.session.timeZone", "UTC")

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")


def fqn(table: str) -> str:
    return table_name(catalog, schema, table)


forecast_table = fqn("gold_forecast_hourly")
accuracy_table = fqn("gold_forecast_accuracy")

# COMMAND ----------

# Both tables may not exist yet on the first run (no champion model, nothing scored).
spark.sql(
    forecast.create_table_sql(
        forecast_table, GOLD_FORECAST_HOURLY_COLUMNS, GOLD_TABLE_COMMENTS["gold_forecast_hourly"]
    )
)
spark.sql(
    forecast.create_table_sql(
        accuracy_table,
        GOLD_FORECAST_ACCURACY_COLUMNS,
        GOLD_TABLE_COMMENTS["gold_forecast_accuracy"],
    )
)
spark.sql(forecast.accuracy_sql(accuracy_table, forecast_table, fqn("gold_energy_hourly")))

# COMMAND ----------

spark.sql(f"SELECT * FROM {accuracy_table} ORDER BY issue_date DESC, target LIMIT 28").show(
    truncate=False
)
print(f"OK: rebuilt {accuracy_table}")
