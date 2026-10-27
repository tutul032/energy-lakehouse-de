# 4. Long silver, wide gold

- **Status:** Accepted
- **Date:** 2026-10-18 (recorded 2026-10-27)

## Context
SMARD publishes one time series per `filter_id` (load, generation per source, day-ahead price),
each in overlapping chunk files. Open-Meteo returns hourly arrays per location. Consumers
(dashboard, Genie, forecasting, anomaly detection) want one row per hour with one column per
measure.

## Decision
- **Silver is long.** `silver_energy_long` has one row per (`filter_id`, `timestamp_utc`), with
  series name and unit joined from `smard_config`. Duplicates across overlapping files resolve to
  the newest file. Expectations: `value_published` (drop), `valid_timestamp` (fail),
  `non_negative_energy` (warn). `silver_weather_hourly` is long per (`location_id`,
  `timestamp_utc`).
- **Gold is wide.** `gold_energy_hourly` has one row per UTC hour and one column per series (MWh
  summed, EUR/MWh averaged). `gold_energy_daily` aggregates per Europe/Berlin calendar day
  (`hour_count` 23/24/25). `gold_energy_weather_hourly` adds weather averaged per location role.
  Column names, types and comments are defined once in `transforms.py`.
- All timestamps are UTC; local time is derived explicitly with `Europe/Berlin`.

## Consequences
- Adding a SMARD series is a config change; silver is unchanged, and gold gains a column (a MINOR
  contract change, ADR 0005).
- Silver is the input for per-series checks (the seasonal anomaly detector reads it); gold is the
  input for dashboards, Genie and the multivariate models.
- Wide gold tables have many columns, and their schema changes when a series is added. The data
  contracts make such changes explicit.
