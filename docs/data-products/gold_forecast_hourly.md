# gold_forecast_hourly

<!-- Generated from contracts/gold_forecast_hourly.yaml by scripts/render_contract_docs.py. Do not edit by hand. -->

Day-ahead forecasts of total_load_mwh and price_eur_mwh: issued daily around 05:17 UTC on local day D for every hour of the Europe/Berlin day D+1, using only data available at issue time (champion model per target). One row per target_hour_utc, target and issue_date.

| | |
|---|---|
| Contract | `energy.gold_forecast_hourly` v0.1.1 (draft) |
| Owner | Rezaul Tutul (rezaul.tutul@yahoo.com) |
| Table (prod) | `workspace.energy_prod.gold_forecast_hourly` |
| Grain | one row per target_hour_utc, target and issue_date |
| Primary key | `target_hour_utc`, `target`, `issue_date` |
| Freshness SLA | newest `issue_time_utc` at most 36 h old |
| Refresh | daily ~05:17 UTC (score_forecast task) |
| Consumers | `energy-consumers` (SELECT) |
| Tags | `contract_id=energy.gold_forecast_hourly`, `contract_version=0.1.1`, `data_product=energy_forecast_hourly`, `domain=energy`, `layer=gold`, `license=CC-BY-4.0`, `source=smard+open-meteo` |

## Columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `target_hour_utc` | TIMESTAMP | no | Start of the forecast hour (UTC). |
| `target_hour_local` | TIMESTAMP_NTZ | no | Start of the forecast hour, local wall-clock time (Europe/Berlin). |
| `target` | STRING | no | Forecast target: gold_energy_hourly column (total_load_mwh or price_eur_mwh). |
| `forecast` | DOUBLE | no | Forecast value in the target's unit (MWh or EUR/MWh). |
| `model_version` | STRING | no | Unity Catalog version of forecaster_<target> (alias champion) that made the forecast. |
| `issue_time_utc` | TIMESTAMP | no | When the forecast was issued (UTC); only data available then was used. |
| `issue_date` | DATE | no | Local (Europe/Berlin) issue day D; the forecast covers local day D+1. |

## Quality rules

Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of
`daily_refresh_job`); the job fails if a rule exceeds its violation limit.

| Rule | Check | Max violations |
|---|---|---|
| `required_columns_not_null` | not NULL: `target_hour_utc`, `target_hour_local`, `target`, `forecast`, `model_version`, `issue_time_utc`, `issue_date` | 0% |
| `unique_forecast` | unique: `target_hour_utc`, `target`, `issue_date` | 0% |
| `known_target` | `target IN ('total_load_mwh', 'price_eur_mwh')` | 0% |
| `issued_before_target_hour` | `target_hour_utc > issue_time_utc` | 0% |
| `covers_next_local_day` | `to_date(target_hour_local) = date_add(issue_date, 1)` | 0% |
| `load_plausible` | `target <> 'total_load_mwh' OR forecast BETWEEN 20000 AND 100000` | 0% |
| `price_plausible` | `target <> 'price_eur_mwh' OR forecast BETWEEN -500 AND 4000` | 0% |
| `latest_issue_complete` | `target IN (SELECT target FROM {table} WHERE issue_date = (SELECT max(issue_date) FROM {table}) GROUP BY target HAVING count(*) BETWEEN 23 AND 25)` (only rows where `issue_date = (SELECT max(issue_date) FROM {table})`) | 0% |

## Request access

Read access goes to the consumer groups: `energy-consumers` (SELECT).

1. Ask the owner (rezaul.tutul@yahoo.com) to add your user or service principal to the group.
2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; `USE CATALOG` on `workspace` is granted once by a workspace admin.
3. Query it:

```sql
SELECT * FROM workspace.energy_prod.gold_forecast_hourly LIMIT 10;
```

To change the contract, open a pull request against `contracts/gold_forecast_hourly.yaml` and bump `version` (see *Data products* in the README).
