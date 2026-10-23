# silver_weather_forecast_hourly

<!-- Generated from contracts/silver_weather_forecast_hourly.yaml by scripts/render_contract_docs.py. Do not edit by hand. -->

Open-Meteo hourly weather forecast per location (one row per location_id and timestamp_utc), from the newest daily run per location; only hours after that file's run timestamp (the forecast available at issue time).

| | |
|---|---|
| Contract | `energy.silver_weather_forecast_hourly` v1.1.0 (active) |
| Owner | Rezaul Tutul (rezaul.tutul@yahoo.com) |
| Table (prod) | `workspace.energy_prod.silver_weather_forecast_hourly` |
| Grain | one row per location_id and timestamp_utc |
| Primary key | `location_id`, `timestamp_utc` |
| Freshness SLA | newest `ingested_at` at most 30 h old |
| Refresh | daily ~05:17 UTC |
| Consumers | `energy-consumers` (SELECT) |
| Tags | `contract_id=energy.silver_weather_forecast_hourly`, `contract_version=1.1.0`, `data_product=weather_forecast_hourly`, `domain=energy`, `layer=silver`, `license=CC-BY-4.0`, `source=open-meteo` |

## Columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `location_id` | STRING | no | Weather location id (weather_config.LOCATIONS). |
| `location_name` | STRING | yes | Human-readable location name. |
| `role` | STRING | yes | SMARD series the location is a proxy for (load, solar, ...). |
| `ts_s` | BIGINT | no | Forecast hour as unix seconds (UTC), as delivered by Open-Meteo. |
| `timestamp_utc` | TIMESTAMP | no | Start of the forecast hour (UTC). |
| `temperature_2m` | DOUBLE | no | Forecast 2 m air temperature (deg C). |
| `wind_speed_10m` | DOUBLE | yes | Forecast 10 m wind speed (m/s). |
| `wind_speed_100m` | DOUBLE | yes | Forecast 100 m wind speed (m/s). |
| `shortwave_radiation` | DOUBLE | yes | Forecast shortwave (global horizontal) radiation (W/m2). |
| `cloud_cover` | DOUBLE | yes | Forecast total cloud cover (%). |
| `run_date` | DATE | no | UTC run date of the landing file (newest per location). |
| `run_ts_utc` | TIMESTAMP | no | UTC run timestamp of the landing file = forecast issue time. |
| `source_file` | STRING | no | Landing file the forecast comes from. |
| `file_modification_time` | TIMESTAMP | yes | Modification time of the landing file. |
| `ingested_at` | TIMESTAMP | no | Bronze ingestion time of the landing file. |

## Quality rules

Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of
`daily_refresh_job`); the job fails if a rule exceeds its violation limit.

| Rule | Check | Max violations |
|---|---|---|
| `required_columns_not_null` | not NULL: `location_id`, `ts_s`, `timestamp_utc`, `temperature_2m`, `run_date`, `run_ts_utc`, `source_file`, `ingested_at` | 0% |
| `unique_location_hour` | unique: `location_id`, `timestamp_utc` | 0% |
| `future_hours_only` | `timestamp_utc > run_ts_utc` | 0% |
| `known_location` | not NULL: `role` | 0% |
| `temperature_plausible` | `temperature_2m BETWEEN -40 AND 50` | 0% |
| `wind_plausible` | `wind_speed_100m IS NULL OR wind_speed_100m BETWEEN 0 AND 75` | 0% |
| `radiation_plausible` | `shortwave_radiation IS NULL OR shortwave_radiation BETWEEN 0 AND 1400` | 0% |

## Request access

Read access goes to the consumer groups: `energy-consumers` (SELECT).

1. Ask the owner (rezaul.tutul@yahoo.com) to add your user or service principal to the group.
2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; `USE CATALOG` on `workspace` is granted once by a workspace admin.
3. Query it:

```sql
SELECT * FROM workspace.energy_prod.silver_weather_forecast_hourly LIMIT 10;
```

To change the contract, open a pull request against `contracts/silver_weather_forecast_hourly.yaml` and bump `version` (see *Data products* in the README).
