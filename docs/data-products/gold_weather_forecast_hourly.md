# gold_weather_forecast_hourly

<!-- Generated from contracts/gold_weather_forecast_hourly.yaml by scripts/render_contract_docs.py. Do not edit by hand. -->

Open-Meteo day-ahead weather forecast aggregated by location role, one row per future UTC hour: load-centre temperature, onshore/offshore 100 m wind, solar radiation (Open-Meteo, CC BY 4.0). Built from the newest daily run per location; only hours after its issue time.

| | |
|---|---|
| Contract | `energy.gold_weather_forecast_hourly` v1.0.1 (active) |
| Owner | Rezaul Tutul (rezaul.tutul@yahoo.com) |
| Table (prod) | `workspace.energy_prod.gold_weather_forecast_hourly` |
| Grain | one row per hour_utc |
| Primary key | `hour_utc` |
| Freshness SLA | newest `issued_at` at most 30 h old |
| Refresh | daily ~05:17 UTC |
| Consumers | `energy-consumers` (SELECT) |
| Tags | `contract_id=energy.gold_weather_forecast_hourly`, `contract_version=1.0.1`, `data_product=weather_forecast_hourly`, `domain=energy`, `layer=gold`, `license=CC-BY-4.0`, `source=open-meteo` |

## Columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `hour_utc` | TIMESTAMP | no | Start of the hour (UTC). |
| `hour_local` | TIMESTAMP_NTZ | no | Start of the hour, local wall-clock time (Europe/Berlin). |
| `temperature_load_avg_c` | DOUBLE | yes | Mean 2 m air temperature over the load-centre locations (deg C). |
| `wind_speed_100m_onshore_avg_ms` | DOUBLE | yes | Mean 100 m wind speed over the onshore-wind locations (m/s). |
| `wind_speed_100m_offshore_ms` | DOUBLE | yes | 100 m wind speed at the offshore-wind location(s), averaged if several (m/s). |
| `shortwave_radiation_solar_avg_wm2` | DOUBLE | yes | Mean shortwave (global horizontal) radiation over the solar locations (W/m2). |
| `issued_at` | TIMESTAMP | no | Newest run timestamp (UTC) of the forecast files behind this hour (issue time). |

## Quality rules

Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of
`daily_refresh_job`); the job fails if a rule exceeds its violation limit.

| Rule | Check | Max violations |
|---|---|---|
| `required_columns_not_null` | not NULL: `hour_utc`, `hour_local`, `issued_at` | 0% |
| `unique_hour` | unique: `hour_utc` | 0% |
| `within_forecast_horizon` | `hour_utc <= issued_at + INTERVAL 3 DAYS` | 0% |
| `wind_onshore_plausible` | `wind_speed_100m_onshore_avg_ms IS NULL OR wind_speed_100m_onshore_avg_ms BETWEEN 0 AND 75` | 0% |
| `wind_offshore_plausible` | `wind_speed_100m_offshore_ms IS NULL OR wind_speed_100m_offshore_ms BETWEEN 0 AND 75` | 0% |
| `temperature_plausible` | `temperature_load_avg_c IS NULL OR temperature_load_avg_c BETWEEN -40 AND 50` | 0% |
| `radiation_plausible` | `shortwave_radiation_solar_avg_wm2 IS NULL OR shortwave_radiation_solar_avg_wm2 BETWEEN 0 AND 1400` | 0% |

## Request access

Read access goes to the consumer groups: `energy-consumers` (SELECT).

1. Ask the owner (rezaul.tutul@yahoo.com) to add your user or service principal to the group.
2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; `USE CATALOG` on `workspace` is granted once by a workspace admin.
3. Query it:

```sql
SELECT * FROM workspace.energy_prod.gold_weather_forecast_hourly LIMIT 10;
```

To change the contract, open a pull request against `contracts/gold_weather_forecast_hourly.yaml` and bump `version` (see *Data products* in the README).
