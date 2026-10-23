# gold_dq_daily_summary

<!-- Generated from contracts/gold_dq_daily_summary.yaml by scripts/render_contract_docs.py. Do not edit by hand. -->

Daily anomaly counts per check and severity from the detect_anomalies run of each local (Europe/Berlin) day; checks that found nothing have n = 0. One row per detection_date, check_name and severity.

| | |
|---|---|
| Contract | `energy.gold_dq_daily_summary` v0.1.0 (draft) |
| Owner | Rezaul Tutul (rezaul.tutul@yahoo.com) |
| Table (prod) | `workspace.energy_prod.gold_dq_daily_summary` |
| Grain | one row per detection_date, check_name and severity |
| Primary key | `detection_date`, `check_name`, `severity` |
| Freshness SLA | newest `detection_date` at most 48 h old |
| Refresh | daily ~05:17 UTC (detect_anomalies task) |
| Consumers | `energy-consumers` (SELECT) |
| Tags | `contract_id=energy.gold_dq_daily_summary`, `contract_version=0.1.0`, `data_product=energy_dq_daily_summary`, `domain=data_quality`, `layer=gold`, `source=smard+open-meteo` |

## Columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `detection_date` | DATE | no | Local (Europe/Berlin) day of the detection run. |
| `check_name` | STRING | no | Detector (see gold_dq_anomalies.check_name). |
| `severity` | STRING | no | critical or warning. |
| `n` | BIGINT | no | Rows flagged by the check with this severity in the run (0 = none). |

## Quality rules

Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of
`daily_refresh_job`); the job fails if a rule exceeds its violation limit.

| Rule | Check | Max violations |
|---|---|---|
| `required_columns_not_null` | not NULL: `detection_date`, `check_name`, `severity`, `n` | 0% |
| `unique_day_check_severity` | unique: `detection_date`, `check_name`, `severity` | 0% |
| `known_severity` | `severity IN ('critical', 'warning')` | 0% |
| `n_non_negative` | `n >= 0` | 0% |

## Request access

Read access goes to the consumer groups: `energy-consumers` (SELECT).

1. Ask the owner (rezaul.tutul@yahoo.com) to add your user or service principal to the group.
2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; `USE CATALOG` on `workspace` is granted once by a workspace admin.
3. Query it:

```sql
SELECT * FROM workspace.energy_prod.gold_dq_daily_summary LIMIT 10;
```

To change the contract, open a pull request against `contracts/gold_dq_daily_summary.yaml` and bump `version` (see *Data products* in the README).
