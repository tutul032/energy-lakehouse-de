# Databricks notebook source
# MAGIC %md
# MAGIC # 10_train_forecast
# MAGIC Trains the day-ahead forecaster for one target (`total_load_mwh` or `price_eur_mwh`; task of
# MAGIC `train_forecast_job`). Reads `gold_energy_weather_hourly`, builds features with
# MAGIC `energy_lakehouse.forecast` (only data available at the 05:17 UTC issue time; observed
# MAGIC weather stands in for the weather forecast), trains a `HistGradientBoostingRegressor` and
# MAGIC compares it with the seasonal naive baseline (same hour one week earlier) on the last 14
# MAGIC days. Logs everything to MLflow (`/Users/<current user>/energy_forecast_<target>`),
# MAGIC registers `<catalog>.<schema>.forecaster_<target>` in Unity Catalog and moves the alias
# MAGIC `champion` to the new version only if it beats the baseline (MAE).

# COMMAND ----------

# MAGIC %pip install -q -r forecast_requirements.txt

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import logging
import os
import sys

import matplotlib.pyplot as plt
import mlflow
import pandas as pd
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse import forecast
from energy_lakehouse.utils import table_name

# Collected timestamps are naive; with a UTC session they are UTC (forecast.to_utc relies on it).
spark.conf.set("spark.sql.session.timeZone", "UTC")
log = logging.getLogger("train_forecast")

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")
dbutils.widgets.dropdown("target", forecast.TARGETS[0], list(forecast.TARGETS))

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
target = dbutils.widgets.get("target")
if target not in forecast.TARGETS:
    raise ValueError(f"target must be one of {forecast.TARGETS}, got {target!r}")
model_name = forecast.model_name(catalog, schema, target)
print(f"Training {model_name} from {catalog}.{schema}")

# COMMAND ----------

columns = ["hour_utc", target, *forecast.WEATHER_FEATURES]
history = (
    spark.table(table_name(catalog, schema, "gold_energy_weather_hourly"))
    .select(*columns)
    .toPandas()
)
data = forecast.build_training_set(history, target)
train, test = forecast.time_split(data, forecast.TEST_DAYS)
features = list(forecast.FEATURE_COLUMNS)
X_train, y_train = train[features], train["y"]
X_test, y_test = test[features], test["y"]
print(
    f"{len(history)} history hours -> {len(train)} train rows "
    f"({train['issue_date'].min()} .. {train['issue_date'].max()}), {len(test)} test rows "
    f"({test['issue_date'].min()} .. {test['issue_date'].max()})"
)

# COMMAND ----------

model = HistGradientBoostingRegressor(**forecast.MODEL_PARAMS)
model.fit(X_train, y_train)

pred_model = model.predict(X_test)
pred_baseline = forecast.seasonal_naive(
    history, target, forecast.BASELINE_LAG_HOURS, test["target_hour_utc"]
).to_numpy()
model_metrics = forecast.evaluate(y_test, pred_model, target)
baseline_metrics = forecast.evaluate(y_test, pred_baseline, target)
metrics = {
    **{f"model_{k}": v for k, v in model_metrics.items()},
    **{f"baseline_{k}": v for k, v in baseline_metrics.items()},
}
print(metrics)

# COMMAND ----------


def forecast_plot() -> plt.Figure:
    hours = pd.to_datetime(test["target_hour_utc"])
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(hours, y_test.to_numpy(), label="actual", linewidth=1.5)
    ax.plot(hours, pred_model, label="model", linewidth=1)
    ax.plot(hours, pred_baseline, label="seasonal naive (168 h)", linewidth=1, alpha=0.7)
    ax.set_title(f"{target}: day-ahead forecast vs actual, last {forecast.TEST_DAYS} days (UTC)")
    ax.legend()
    fig.tight_layout()
    return fig


importance = permutation_importance(
    model,
    X_test,
    y_test,
    scoring="neg_mean_absolute_error",
    n_repeats=5,
    random_state=forecast.MODEL_PARAMS["random_state"],
)
importance_df = pd.DataFrame(
    {
        "feature": features,
        "importance_mean": importance.importances_mean,
        "importance_std": importance.importances_std,
    }
).sort_values("importance_mean", ascending=False)


def importance_plot() -> plt.Figure:
    fig, ax = plt.subplots(figsize=(8, 5))
    ordered = importance_df.sort_values("importance_mean")
    ax.barh(ordered["feature"], ordered["importance_mean"], xerr=ordered["importance_std"])
    ax.set_title(f"{target}: permutation importance (MAE increase, test period)")
    fig.tight_layout()
    return fig


# COMMAND ----------

user = spark.sql("SELECT current_user() AS u").first()["u"]
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(f"/Users/{user}/energy_forecast_{target}")
client = MlflowClient()

with mlflow.start_run(run_name=f"{target} {test['issue_date'].max()}") as run:
    mlflow.log_params(
        {
            "target": target,
            "catalog": catalog,
            "schema": schema,
            "features": ",".join(features),
            "lag_hours": ",".join(map(str, forecast.LAG_HOURS)),
            "min_lag_hours": forecast.MIN_LAG_HOURS,
            "test_days": forecast.TEST_DAYS,
            "baseline": f"seasonal_naive_{forecast.BASELINE_LAG_HOURS}h",
            "n_train": len(train),
            "n_test": len(test),
            "train_start": str(train["issue_date"].min()),
            "test_end": str(test["issue_date"].max()),
            **{f"model_{k}": v for k, v in forecast.MODEL_PARAMS.items()},
        }
    )
    mlflow.log_metrics(metrics)
    mlflow.log_figure(forecast_plot(), "forecast_vs_actual.png")
    mlflow.log_figure(importance_plot(), "permutation_importance.png")
    mlflow.log_table(importance_df, "permutation_importance.json")

    example = X_train.dropna().head(5)
    example = example if len(example) else X_train.head(5)
    model_info = mlflow.sklearn.log_model(
        model,
        name="model",
        signature=infer_signature(X_train, model.predict(X_train.head(50))),
        input_example=example,
        registered_model_name=model_name,
    )
    version = model_info.registered_model_version
    mlflow.set_tag("registered_version", version)

    if model_metrics["mae"] < baseline_metrics["mae"]:
        client.set_registered_model_alias(model_name, forecast.CHAMPION_ALIAS, version)
        mlflow.set_tag("champion", "true")
        print(f"{model_name} v{version} is the new {forecast.CHAMPION_ALIAS}")
    else:
        mlflow.set_tag("champion", "false")
        log.warning(
            "%s v%s (MAE %.2f) does not beat the baseline (MAE %.2f); keeping the existing %s",
            model_name,
            version,
            model_metrics["mae"],
            baseline_metrics["mae"],
            forecast.CHAMPION_ALIAS,
        )
        print(f"WARNING: v{version} not promoted; existing {forecast.CHAMPION_ALIAS} unchanged")

print(f"MLflow run {run.info.run_id}: {metrics}")
