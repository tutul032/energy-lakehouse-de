# gold_energy_hourly

<!-- Generated from contracts/gold_energy_hourly.yaml by scripts/render_contract_docs.py. Do not edit by hand. -->

Hourly German power load, day-ahead price and generation mix (SMARD, CC BY 4.0). One row per UTC hour.

| | |
|---|---|
| Contract | `energy.gold_energy_hourly` v1.0.0 (active) |
| Owner | Rezaul Tutul (rezaul.tutul@yahoo.com) |
| Table (prod) | `workspace.energy_prod.gold_energy_hourly` |
| Grain | one row per hour_utc |
| Primary key | `hour_utc` |
| Freshness SLA | newest `hour_utc` at most 72 h old |
| Refresh | daily ~05:17 UTC |
| Consumers | `energy-consumers` (SELECT) |
| Tags | `contract_id=energy.gold_energy_hourly`, `contract_version=1.0.0`, `data_product=energy_market_hourly`, `domain=energy`, `layer=gold`, `license=CC-BY-4.0`, `source=smard` |

## Columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `hour_utc` | TIMESTAMP | no | Start of the hour (UTC). |
| `hour_local` | TIMESTAMP_NTZ | no | Start of the hour, local wall-clock time (Europe/Berlin). |
| `total_load_mwh` | DOUBLE | yes | Total grid load in Germany (MWh). |
| `price_eur_mwh` | DOUBLE | yes | Day-ahead price DE-LU, hourly average (EUR/MWh). |
| `solar_mwh` | DOUBLE | yes | Solar (photovoltaics) generation (MWh). |
| `wind_onshore_mwh` | DOUBLE | yes | Onshore wind generation (MWh). |
| `wind_offshore_mwh` | DOUBLE | yes | Offshore wind generation (MWh). |
| `lignite_mwh` | DOUBLE | yes | Lignite generation (MWh). |
| `hard_coal_mwh` | DOUBLE | yes | Hard coal generation (MWh). |
| `natural_gas_mwh` | DOUBLE | yes | Natural gas generation (MWh). |
| `renewables_mwh` | DOUBLE | yes | Solar + onshore wind + offshore wind (MWh). |
| `renewables_share` | DOUBLE | yes | renewables_mwh / total_load_mwh; NULL if load is 0 or NULL. |

## Quality rules

Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of
`daily_refresh_job`); the job fails if a rule exceeds its violation limit.

| Rule | Check | Max violations |
|---|---|---|
| `required_columns_not_null` | not NULL: `hour_utc`, `hour_local` | 0% |
| `unique_hour` | unique: `hour_utc` | 0% |
| `share_in_range` | `renewables_share IS NULL OR renewables_share BETWEEN 0 AND 1.5` | 0% |
| `load_plausible` | `total_load_mwh IS NULL OR total_load_mwh BETWEEN 20000 AND 100000` | 1% |
| `price_in_market_limits` | `price_eur_mwh IS NULL OR price_eur_mwh BETWEEN -500 AND 4000` | 0% |
| `generation_non_negative` | `coalesce(solar_mwh, 0) >= 0 AND coalesce(wind_onshore_mwh, 0) >= 0 AND coalesce(wind_offshore_mwh, 0) >= 0 AND coalesce(lignite_mwh, 0) >= 0 AND coalesce(hard_coal_mwh, 0) >= 0 AND coalesce(natural_gas_mwh, 0) >= 0` | 1% |

## Request access

Read access goes to the consumer groups: `energy-consumers` (SELECT).

1. Ask the owner (rezaul.tutul@yahoo.com) to add your user or service principal to the group.
2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; `USE CATALOG` on `workspace` is granted once by a workspace admin.
3. Query it:

```sql
SELECT * FROM workspace.energy_prod.gold_energy_hourly LIMIT 10;
```

To change the contract, open a pull request against `contracts/gold_energy_hourly.yaml` and bump `version` (see *Data products* in the README).
