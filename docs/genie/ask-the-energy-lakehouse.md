# Ask the energy lakehouse (Genie)

Natural-language Q&A over the German energy gold tables. This page is the source for the Genie
Agent's tables, instructions, example SQL and benchmarks; recreate the agent per environment from
it (see [Recreate per environment](#recreate-per-environment)).

## Source tables

All six from `workspace.energy_<env>` (`energy_dev`, `energy_staging` or `energy_prod`), never
mixed across environments. Column descriptions come from the data contracts
([docs/data-products/](../data-products/)).

| Table | Grain | Use for |
|---|---|---|
| `gold_energy_hourly` | one row per UTC hour | load, generation by source, day-ahead price, renewables share |
| `gold_energy_daily` | one row per Europe/Berlin calendar day | daily totals and price stats; only rows with `is_complete = true` are final |
| `gold_energy_weather_hourly` | one row per UTC hour | energy plus observed weather by role (wind, solar radiation, temperature) |
| `gold_forecast_hourly` | one row per target hour, target, issue date | day-ahead forecasts of `total_load_mwh` and `price_eur_mwh` for local day D+1 |
| `gold_forecast_accuracy` | one row per target and issue date | MAE / MAPE of the model vs. the seasonal-naive baseline |
| `gold_dq_anomalies` | one row per check, entity, flagged hour | data quality anomalies (`warning` / `critical`); exclude entities starting with `TEST:` |

## Instructions

Paste into the agent's general instructions.

> **TODO: paste the full instructions text here.** The current dev agent contains only the line
> below.

```text
- A day is complete only if is_complete = true in gold_energy_daily; use only complete days for daily totals and "yesterday" KPIs.
```

## Example questions

Add each as an example SQL query (question, SQL, usage guidance).

### 1. What was the average price on windy nights in the last 7 days?

Usage guidance: Windy = onshore wind at 100 m ≥ 8 m/s; night = local hours 0–5.

```sql
SELECT round(avg(price_eur_mwh), 1) AS avg_price
FROM gold_energy_weather_hourly
WHERE wind_speed_100m_onshore_avg_ms >= 8
  AND hour(hour_local) BETWEEN 0 AND 5
  AND hour_local >= date_sub(to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin')), 7)
```

### 2. What is tomorrow's peak load forecast?

```sql
SELECT target_hour_local, round(forecast) AS forecast_mwh
FROM gold_forecast_hourly
WHERE target = 'total_load_mwh'
  AND issue_date = (SELECT max(issue_date) FROM gold_forecast_hourly)
ORDER BY forecast DESC LIMIT 1
```

### 3. How accurate was the load forecast on the latest evaluated day?

```sql
SELECT issue_date, round(mae, 1) AS mae, round(baseline_mae, 1) AS baseline_mae, round(mape, 2) AS mape
FROM gold_forecast_accuracy
WHERE target = 'total_load_mwh'
ORDER BY issue_date DESC LIMIT 1
```

## Benchmarks

Add each question with its SQL as the expected answer, then run the benchmarks after every
change to the instructions or examples.

> **TODO: paste the six benchmark questions and their expected-answer SQL.**

| # | Question | Expected answer (SQL) |
|---|---|---|
| 1 | TODO | TODO |
| 2 | TODO | TODO |
| 3 | TODO | TODO |
| 4 | TODO | TODO |
| 5 | TODO | TODO |
| 6 | TODO | TODO |

## Recreate per environment

Run this once per environment (`<env>` = `dev`, `staging` or `prod`), in the workspace UI:

1. **Create a Genie Agent:** sidebar **Genie** → **New**. Title `Ask the energy lakehouse`, SQL
   warehouse `Serverless Starter Warehouse`.
2. **Select the six tables** from `workspace.energy_<env>`: `gold_energy_hourly`,
   `gold_energy_daily`, `gold_energy_weather_hourly`, `gold_forecast_hourly`,
   `gold_forecast_accuracy`, `gold_dq_anomalies`. Check that every table comes from the same
   schema.
3. **Paste the instructions** from [Instructions](#instructions) into the general instructions.
4. **Add the examples** from [Example questions](#example-questions): question, SQL and usage
   guidance for each. Table names stay unqualified, so the same SQL works in every environment.
5. **Run the benchmarks:** add the six questions from [Benchmarks](#benchmarks) with their SQL
   as the expected answers, run them, and fix the instructions/examples until all six pass.
