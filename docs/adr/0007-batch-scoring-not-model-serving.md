# 7. Batch scoring instead of model serving

- **Status:** Accepted
- **Date:** 2026-10-23 (recorded 2026-10-27)

## Context
The project forecasts hourly load and day-ahead price for the next Europe/Berlin day. Forecasts
are needed once a day, after the morning refresh. The consumers are a dashboard, a Genie agent
and an accuracy table; none of them calls a model at request time.

## Decision
- `train_forecast_job` (weekly) trains a `HistGradientBoostingRegressor` per target, logs it to
  MLflow and registers it in Unity Catalog as `<catalog>.<schema>.forecaster_<target>`. The alias
  `champion` moves only if the new model beats the seasonal-naive baseline (same hour one week
  earlier) on MAE over the last 14 days.
- `daily_refresh_job` scores in batch: `score_forecast` loads the `champion` models and MERGEs the
  forecasts for every hour of D+1 into `gold_forecast_hourly`; `forecast_accuracy` recomputes
  MAE/MAPE vs. the baseline into `gold_forecast_accuracy`.
- Training and scoring run in the same serverless environment (same wheel, same pinned
  scikit-learn/mlflow/holidays); a test enforces that the definitions are identical.
- Features use only data available at issue time; `build_features` raises `LeakageError`
  otherwise.

## Consequences
- No always-on endpoint to pay for, secure or monitor; forecasts are ordinary governed tables that
  the dashboard, Genie and contracts can use.
- Forecasts exist only for issue dates on which the job ran; a missed day is not backfilled.
- Moving to real-time scoring would need a serving endpoint; the UC-registered model with
  signature and input example is already in the format serving expects.
