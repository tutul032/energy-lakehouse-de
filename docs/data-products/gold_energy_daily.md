# gold_energy_daily

<!-- Generated from contracts/gold_energy_daily.yaml by scripts/render_contract_docs.py. Do not edit by hand. -->

Daily German energy totals and price statistics per Europe/Berlin calendar day (SMARD, CC BY 4.0). One row per local day.

| | |
|---|---|
| Contract | `energy.gold_energy_daily` v1.2.0 (active) |
| Owner | Rezaul Tutul (rezaul.tutul@yahoo.com) |
| Table (prod) | `workspace.energy_prod.gold_energy_daily` |
| Grain | one row per date_local (Europe/Berlin calendar day) |
| Primary key | `date_local` |
| Freshness SLA | newest `date_local` at most 96 h old |
| Refresh | daily ~05:17 UTC |
| Consumers | `energy-consumers` (SELECT) |
| Tags | `contract_id=energy.gold_energy_daily`, `contract_version=1.2.0`, `data_product=energy_market_daily`, `domain=energy`, `layer=gold`, `license=CC-BY-4.0`, `source=smard` |

## Columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `date_local` | DATE | no | Calendar day in Europe/Berlin. |
| `total_load_mwh` | DOUBLE | yes | Daily sum of total_load_mwh (MWh). |
| `solar_mwh` | DOUBLE | yes | Daily sum of solar_mwh (MWh). |
| `wind_onshore_mwh` | DOUBLE | yes | Daily sum of wind_onshore_mwh (MWh). |
| `wind_offshore_mwh` | DOUBLE | yes | Daily sum of wind_offshore_mwh (MWh). |
| `lignite_mwh` | DOUBLE | yes | Daily sum of lignite_mwh (MWh). |
| `hard_coal_mwh` | DOUBLE | yes | Daily sum of hard_coal_mwh (MWh). |
| `natural_gas_mwh` | DOUBLE | yes | Daily sum of natural_gas_mwh (MWh). |
| `renewables_mwh` | DOUBLE | yes | Daily sum of renewables_mwh (MWh). |
| `price_avg_eur_mwh` | DOUBLE | yes | Average hourly day-ahead price (EUR/MWh). |
| `price_min_eur_mwh` | DOUBLE | yes | Lowest hourly day-ahead price (EUR/MWh). |
| `price_max_eur_mwh` | DOUBLE | yes | Highest hourly day-ahead price (EUR/MWh). |
| `renewables_share_avg` | DOUBLE | yes | Average of the hourly renewables_share. |
| `hour_count` | BIGINT | no | Hours in the day: 24, or 23/25 on DST change days. |
| `load_hours` | BIGINT | no | Hours with a published total_load_mwh (non-NULL). |
| `price_hours` | BIGINT | no | Hours with a published price_eur_mwh (non-NULL). |
| `expected_hours` | INT | no | Local hours in the day: 24, or 23/25 on DST change days. |
| `is_complete` | BOOLEAN | no | True if load_hours and price_hours both equal expected_hours. |

## Quality rules

Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of
`daily_refresh_job`); the job fails if a rule exceeds its violation limit.

| Rule | Check | Max violations |
|---|---|---|
| `required_columns_not_null` | not NULL: `date_local`, `hour_count`, `load_hours`, `price_hours`, `expected_hours`, `is_complete` | 0% |
| `unique_day` | unique: `date_local` | 0% |
| `complete_days` | `hour_count BETWEEN 23 AND 25` (only rows where `date_local < (SELECT date_sub(max(date_local), 1) FROM {table})`) | 0% |
| `complete_day_load_matches_hourly` | SQL check (violations): `SELECT count(*) AS violations FROM {catalog}.{schema}.gold_energy_daily d JOIN (SELECT to_date(hour_local) AS date_local, sum(total_load_mwh) AS hourly_sum FROM {catalog}.{schema}.gold_energy_hourly GROUP BY 1) h USING (date_local) WHERE d.is_complete AND abs(d.total_load_mwh - h.hourly_sum) > 0.001 * h.hourly_sum` | 0 rows |
| `price_stats_ordered` | `price_avg_eur_mwh IS NULL OR (price_min_eur_mwh <= price_avg_eur_mwh + 0.001 AND price_avg_eur_mwh <= price_max_eur_mwh + 0.001)` | 0% |
| `share_avg_in_range` | `renewables_share_avg IS NULL OR renewables_share_avg BETWEEN 0 AND 1.5` | 0% |

## Request access

Read access goes to the consumer groups: `energy-consumers` (SELECT).

1. Ask the owner (rezaul.tutul@yahoo.com) to add your user or service principal to the group.
2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; `USE CATALOG` on `workspace` is granted once by a workspace admin.
3. Query it:

```sql
SELECT * FROM workspace.energy_prod.gold_energy_daily LIMIT 10;
```

To change the contract, open a pull request against `contracts/gold_energy_daily.yaml` and bump `version` (see *Data products* in the README).
