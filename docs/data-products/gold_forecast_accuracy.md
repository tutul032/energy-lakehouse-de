# gold_forecast_accuracy

<!-- Generated from contracts/gold_forecast_accuracy.yaml by scripts/render_contract_docs.py. Do not edit by hand. -->

Accuracy of the day-ahead forecasts against gold_energy_hourly actuals, per target and issue_date: MAE/MAPE of the model and of the seasonal naive baseline (same hour one week earlier) for the same hours. Rebuilt after every scoring run.

| | |
|---|---|
| Contract | `energy.gold_forecast_accuracy` v0.1.0 (draft) |
| Owner | Rezaul Tutul (rezaul.tutul@yahoo.com) |
| Table (prod) | `workspace.energy_prod.gold_forecast_accuracy` |
| Grain | one row per target and issue_date |
| Primary key | `target`, `issue_date` |
| Freshness SLA | newest `issue_date` at most 120 h old |
| Refresh | daily ~05:17 UTC (forecast_accuracy task) |
| Consumers | `energy-consumers` (SELECT) |
| Tags | `contract_id=energy.gold_forecast_accuracy`, `contract_version=0.1.0`, `data_product=energy_forecast_accuracy`, `domain=energy`, `layer=gold`, `license=CC-BY-4.0`, `source=smard+open-meteo` |

## Columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `target` | STRING | no | Forecast target (total_load_mwh or price_eur_mwh). |
| `issue_date` | DATE | no | Local (Europe/Berlin) issue day D of the forecast for day D+1. |
| `n_hours` | BIGINT | no | Forecast hours with an actual value in gold_energy_hourly. |
| `mae` | DOUBLE | no | Mean absolute error of the model forecast (target unit). |
| `mape` | DOUBLE | yes | Mean absolute percentage error of the model forecast (%), hours with \|actual\| < 1 skipped; NULL for price_eur_mwh. |
| `baseline_mae` | DOUBLE | yes | Mean absolute error of the seasonal naive baseline (same hour 168 h earlier). |
| `baseline_mape` | DOUBLE | yes | MAPE of the seasonal naive baseline (%); NULL for price_eur_mwh. |

## Quality rules

Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of
`daily_refresh_job`); the job fails if a rule exceeds its violation limit.

| Rule | Check | Max violations |
|---|---|---|
| `required_columns_not_null` | not NULL: `target`, `issue_date`, `n_hours`, `mae` | 0% |
| `unique_target_day` | unique: `target`, `issue_date` | 0% |
| `hours_in_day` | `n_hours BETWEEN 1 AND 25` | 0% |
| `errors_non_negative` | `mae >= 0 AND (baseline_mae IS NULL OR baseline_mae >= 0)` | 0% |
| `no_price_mape` | `target <> 'price_eur_mwh' OR (mape IS NULL AND baseline_mape IS NULL)` | 0% |

## Request access

Read access goes to the consumer groups: `energy-consumers` (SELECT).

1. Ask the owner (rezaul.tutul@yahoo.com) to add your user or service principal to the group.
2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; `USE CATALOG` on `workspace` is granted once by a workspace admin.
3. Query it:

```sql
SELECT * FROM workspace.energy_prod.gold_forecast_accuracy LIMIT 10;
```

To change the contract, open a pull request against `contracts/gold_forecast_accuracy.yaml` and bump `version` (see *Data products* in the README).
