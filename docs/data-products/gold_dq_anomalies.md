# gold_dq_anomalies

<!-- Generated from contracts/gold_dq_anomalies.yaml by scripts/render_contract_docs.py. Do not edit by hand. -->

Data-quality anomalies from the daily detect_anomalies task: seasonal robust z-scores per SMARD series, an IsolationForest over the hourly gold features, completeness per series and local day, physics rules and forecast drift. One row per check_name, entity and ts_utc; a re-detection updates the row.

| | |
|---|---|
| Contract | `energy.gold_dq_anomalies` v0.1.0 (draft) |
| Owner | Rezaul Tutul (rezaul.tutul@yahoo.com) |
| Table (prod) | `workspace.energy_prod.gold_dq_anomalies` |
| Grain | one row per check_name, entity and ts_utc |
| Primary key | `check_name`, `entity`, `ts_utc` |
| Freshness SLA | newest `detected_at` at most 336 h old |
| Refresh | daily ~05:17 UTC (detect_anomalies task) |
| Consumers | `energy-consumers` (SELECT) |
| Tags | `contract_id=energy.gold_dq_anomalies`, `contract_version=0.1.0`, `data_product=energy_dq_anomalies`, `domain=data_quality`, `layer=gold`, `source=smard+open-meteo` |

## Columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `check_name` | STRING | no | Detector that flagged the row (seasonal_robust_z, isolation_forest, completeness, physics_solar_at_night, forecast_drift_mae, forecast_drift_mape). |
| `entity` | STRING | no | What was checked: SMARD series, gold column/table or forecast target; the prefix TEST: marks an injected test anomaly. |
| `ts_utc` | TIMESTAMP | no | Start of the flagged hour (UTC); for daily checks the start of the local (Europe/Berlin) day. |
| `value` | DOUBLE | yes | Observed value (entity unit; completeness: present hours; isolation_forest: anomaly score). |
| `expected` | DOUBLE | yes | Expected value (seasonal median, expected hours, baseline MAE); NULL if not applicable. |
| `lower` | DOUBLE | yes | Lower bound of the normal range; NULL if not applicable. |
| `upper` | DOUBLE | yes | Upper bound of the normal range or threshold; NULL if not applicable. |
| `score` | DOUBLE | no | Anomaly strength, higher = more abnormal (\|robust z\|, missing %, ratio to threshold). |
| `severity` | STRING | no | critical or warning. |
| `reason` | STRING | no | Short human-readable explanation. |
| `detected_at` | TIMESTAMP | no | Start (UTC) of the latest detection run that flagged the row. |

## Quality rules

Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of
`daily_refresh_job`); the job fails if a rule exceeds its violation limit.

| Rule | Check | Max violations |
|---|---|---|
| `required_columns_not_null` | not NULL: `check_name`, `entity`, `ts_utc`, `score`, `severity`, `reason`, `detected_at` | 0% |
| `unique_anomaly` | unique: `check_name`, `entity`, `ts_utc` | 0% |
| `known_check` | `check_name IN ('seasonal_robust_z', 'isolation_forest', 'completeness', 'physics_solar_at_night', 'forecast_drift_mae', 'forecast_drift_mape')` | 0% |
| `known_severity` | `severity IN ('critical', 'warning')` | 0% |
| `isolation_forest_warning_only` | `check_name <> 'isolation_forest' OR severity = 'warning'` | 0% |
| `score_non_negative` | `score >= 0` | 0% |
| `detected_after_ts` | `detected_at >= ts_utc` | 0% |

## Request access

Read access goes to the consumer groups: `energy-consumers` (SELECT).

1. Ask the owner (rezaul.tutul@yahoo.com) to add your user or service principal to the group.
2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; `USE CATALOG` on `workspace` is granted once by a workspace admin.
3. Query it:

```sql
SELECT * FROM workspace.energy_prod.gold_dq_anomalies LIMIT 10;
```

To change the contract, open a pull request against `contracts/gold_dq_anomalies.yaml` and bump `version` (see *Data products* in the README).
