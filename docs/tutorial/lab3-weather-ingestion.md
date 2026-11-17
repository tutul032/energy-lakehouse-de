# Lab 3 — Adding Weather Data: Open-Meteo Ingestion and an Energy × Weather Gold Table

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 2 — Auto Loader and Declarative Pipelines. Next: Lab 4 — Daily automated refresh with GitHub Actions, a service principal and OIDC.

## 1. Goal

Energy data alone tells you *what* happened. Weather tells you *why*: wind and sun drive renewable generation, and that drives the price. In this lab you add a second data source and join it with the energy data.

By the end you will have:

- A weather extractor for **Open-Meteo** (hourly temperature, wind at 10 m and 100 m, solar radiation, cloud cover) for 7 locations in Germany
- A one-command **sync script** that extracts both sources on your laptop, uploads them to the landing volume, verifies the upload and optionally runs the pipeline
- A fix for a known Lab 1 gap: the **newest SMARD chunks are refreshed** on every run, so late-published hours are filled in
- Three new pipeline tables: `bronze_weather_raw`, `silver_weather_hourly`, `gold_energy_weather_hourly`
- Your first analytical result: the **merit-order effect**. More wind means a higher renewables share and lower electricity prices.

**Architecture**

```
SMARD API ───┐                              ┌─► bronze_smard_raw ──► silver_energy_long ──► gold_energy_hourly ─┐
             ├─► laptop ─► landing volume ──┤                                                                   ├─► gold_energy_weather_hourly
Open-Meteo ──┘   (sync script)              └─► bronze_weather_raw ─► silver_weather_hourly ──────────────────────┘
```

**Time:** about 2–3 hours.

## 2. Prerequisites

- Labs 0–2 completed. The pipeline runs green.
- VS Code open in `C:\dev\energy-lakehouse-de`, terminal open (**Ctrl+`**)

### About the data

**Open-Meteo** is a free weather API with no API key. We use the forecast endpoint with `past_days`, which returns up to 92 days of recent hourly history plus a short forecast:

```
https://api.open-meteo.com/v1/forecast
  ?latitude=52.52&longitude=13.41
  &hourly=temperature_2m,wind_speed_10m,wind_speed_100m,shortwave_radiation,cloud_cover
  &past_days=63&forecast_days=1
  &timezone=GMT&timeformat=unixtime&wind_speed_unit=ms
```

The response holds parallel arrays: `hourly.time` contains Unix timestamps **in seconds**, and each variable has an array of the same length.

**Why 7 locations, and why roles?** Germany's power system depends on weather in different regions for different reasons. Each location gets a **role** that tells the gold layer what to use it for:

| location_id | Coordinates | Role | Why |
| --- | --- | --- | --- |
| hamburg | 53.55, 9.99 | onshore_wind | Northern Germany has most onshore wind farms |
| north_sea | 54.5, 6.5 | offshore_wind | Offshore wind parks |
| berlin | 52.52, 13.41 | load | Big cities: temperature affects demand |
| essen | 51.46, 7.01 | load | Ruhr area |
| frankfurt | 50.11, 8.68 | load | Rhine-Main area |
| munich | 48.14, 11.58 | solar | Southern Germany has most solar |
| stuttgart | 48.78, 9.18 | solar | Baden-Württemberg |

Attribute the data in your README: *Weather data by Open-Meteo.com (CC BY 4.0).*

---

## 3. Steps

### Step 01 — Check whether Databricks can reach the internet

**What and why:** if Databricks could call the APIs itself, extraction could run as a Databricks job. On Free Edition, outbound internet is restricted to a limited set of trusted domains. Verifying your identity with LinkedIn is supposed to unlock it.

**Action:** complete the LinkedIn verification in workspace **Settings** (if offered), wait a few minutes, then run in a serverless notebook:

```python
import requests
print(requests.get("https://www.smard.de/app/chart_data/410/DE/index_hour.json", timeout=20).status_code)
print(requests.get("https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&hourly=temperature_2m&past_days=1", timeout=20).status_code)
```

**Expected result:** `200` and `200` if outbound access works.

**What happened in my case:** even two days after LinkedIn verification, both calls still failed with a connection error (see Troubleshooting P1). So this lab extracts on the **laptop** and uploads to Databricks, the same fallback as Lab 1. The design works either way, and in Lab 4 the laptop is replaced by GitHub Actions.

---

### Step 02 — Generate the weather extractor

**What and why:** a small, Spark-free Python module that downloads raw weather JSON per location and stores it unchanged. It follows the same design as the SMARD extractor: raw data stays raw, existing files are skipped, failed requests are retried, and everything is tested offline. In the same step we fix the SMARD gap from Lab 1: the newest 2 SMARD chunks are always re-downloaded, because SMARD fills in hours late.

**Option A — Claude Code prompt:**

```text
Read CLAUDE.md first. Add an Open-Meteo weather extractor, mirroring the SMARD extractor design. Do not deploy.

API (no key): https://api.open-meteo.com/v1/forecast
  params: latitude, longitude, hourly=temperature_2m,wind_speed_10m,wind_speed_100m,shortwave_radiation,cloud_cover,
          past_days=<N, max 92>, forecast_days=1, timezone=GMT, timeformat=unixtime, wind_speed_unit=ms
  response: {"latitude":..,"longitude":..,"hourly_units":{..},"hourly":{"time":[unix_s,..],"temperature_2m":[..],..}}

1. src/energy_lakehouse/weather_config.py — dataclass list of locations (location_id, name, lat, lon, role):
   hamburg 53.55,9.99 onshore_wind; north_sea 54.5,6.5 offshore_wind; berlin 52.52,13.41 load;
   essen 51.46,7.01 load; frankfurt 50.11,8.68 load; munich 48.14,11.58 solar; stuttgart 48.78,9.18 solar.
2. src/energy_lakehouse/weather.py — pure, Spark-free: build_url(), fetch(session, location, past_days),
   output_path(base_dir, location, run_date) -> f"{base_dir}/weather/location={id}/run_date={yyyy-mm-dd}.json",
   extract(base_dir, locations, past_days, run_date=today_utc, overwrite=False): raw JSON unchanged,
   skip existing, retries 3x with backoff, 0.2 s pause, summary {written, skipped, failed}.
3. Extend src/energy_lakehouse/extract_local.py with a --source {smard,weather,all} flag
   (default all) and --past-days (default 63) for weather.
4. src/notebooks/02_extract_weather.py — Databricks notebook, same pattern as 01_extract_smard.py.
5. Fix the known SMARD gap: in smard.extract(), always re-download (overwrite) the newest `refresh_latest`
   chunks per series (default 2), so late-published hours get filled. Keep older chunks idempotent.
   Update its tests.
6. tests/test_weather.py — offline tests with a fixture JSON: URL params, output_path, idempotent skip, retries.
7. Update CLAUDE.md.

List files changed and checkpoint commands at the end.
```

**Option B:** copy the files from my repo at tag `lab3a-complete`.

**Expected result:** new files `weather_config.py`, `weather.py`, `02_extract_weather.py`, `tests/test_weather.py`, plus updated `extract_local.py`, `smard.py` and their tests.

**Why the folder layout `weather/location=<id>/run_date=<date>.json`?** Each run saves the full recent history per location as a new file. The silver layer later keeps the **newest value per (location, hour)**, so a later run can correct earlier data. It's the same "newest file wins" rule as for SMARD.

---

### Step 03 — Test locally and run a smoke test

**What and why:** first check that the code is clean and the logic is correct without internet, then do one real download.

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -m energy_lakehouse.extract_local --out data/landing --source weather
Get-ChildItem data\landing\weather -Recurse -File | Measure-Object
```

**Expected result:**
- all checks and tests green
- summary `written: 7, skipped: 0, failed: 0`
- `Count : 7`

Open `data\landing\weather\location=berlin\run_date=<today>.json`. `hourly.time` has about (63 + 1) × 24 ≈ 1,536 entries, and `temperature_2m` values are plausible for the season.

Run the command again: `written: 0, skipped: 7`. The extractor is idempotent.

📸 `lab3-step03-local-weather.png`

---

### Step 04 — Create a sync script (extract → upload → verify → run)

**What and why:** until now you typed four or five commands to get fresh data into Databricks. A script makes this one command, makes it repeatable, and (most importantly) **fails loudly** if something goes wrong. This matters: a silently skipped upload cost me an evening of debugging (see Troubleshooting P3). The same logic becomes the automated GitHub Actions job in Lab 4.

**Option A — Claude Code prompt:**

```text
Add scripts/sync_landing.ps1 (PowerShell) that:
1. runs `uv run python -m energy_lakehouse.extract_local --out data/landing --source all`
2. uploads BOTH data/landing/smard and data/landing/weather to
   dbfs:/Volumes/<catalog>/<schema>/landing/<source> with `databricks fs cp -r ... --overwrite`
3. parameters: -Profile (default free), -Catalog (default workspace), -Schema (default energy_dev), -RunPipeline switch
4. if -RunPipeline: `databricks bundle run energy_pipeline -t dev -p <Profile>`
5. sets $ErrorActionPreference = "Stop", checks $LASTEXITCODE after EVERY external command and stops
   with a clear error message
6. after uploading, verifies each target with
   `databricks fs ls dbfs:/Volumes/<catalog>/<schema>/landing/<source> -p <Profile>` and fails if it is empty
7. prints a short summary at the end.
Document it in CLAUDE.md and README.
```

**Run it:**

```powershell
.\scripts\sync_landing.ps1
```

If PowerShell says *"running scripts is disabled on this system"*, allow local scripts once for your user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Expected result:** extractor summaries for SMARD (e.g. `written: 16`, the refreshed newest 2 chunks × 8 series) and weather, then both uploads, then the verification for `smard` and `weather` passes.

**Check in SQL Editor:**

```sql
LIST '/Volumes/workspace/energy_dev/landing/';
LIST '/Volumes/workspace/energy_dev/landing/weather/';
```

**Expected:** `smard/` and `weather/` under `landing/`, and 7 `location=...` folders under `weather/`. Or in **Catalog Explorer → workspace → energy_dev → Volumes → landing**.

📸 `lab3-step04-volume-weather.png`

---

### Step 05 — Extend the pipeline with weather

**What and why:** three new tables following the medallion pattern from Lab 2:
- **bronze_weather_raw:** Auto Loader reads new weather files, plus lineage columns.
- **silver_weather_hourly:** the parallel arrays are zipped and exploded into one row per (location, hour), forecast hours are removed, newest file wins, and there are null-safe quality rules.
- **gold_energy_weather_hourly:** weather is aggregated **by role** (average wind over onshore locations, solar radiation over solar locations, temperature over load locations) and joined onto the hourly energy table.

**Option A — Claude Code prompt:**

```text
Extend the Lakeflow pipeline with weather. Same conventions as bronze/silver/gold for SMARD. Do not deploy.

1. src/pipelines/bronze_weather.py — streaming table bronze_weather_raw via Auto Loader from
   configuration key weather_landing_path (/Volumes/${catalog}/${schema}/landing/weather), json, multiLine,
   explicit schema for the response; add location_id and run_date (regex from path), source_file,
   file_modification_time, ingested_at.
2. src/pipelines/silver_weather.py — materialized view silver_weather_hourly: zip hourly arrays
   (arrays_zip) and explode -> one row per (location_id, timestamp_utc) with temperature_2m, wind_speed_10m,
   wind_speed_100m, shortwave_radiation, cloud_cover; Open-Meteo time is unix SECONDS (not milliseconds);
   join name/role from weather_config; dedupe keeping the newest file_modification_time;
   only keep timestamps <= now (drop forecast hours).
   Null-safe expectations, one thing per rule:
     expect_or_fail valid_timestamp: timestamp_utc IS NOT NULL
     expect_or_drop has_measurements: temperature_2m IS NOT NULL
     expect plausible_temperature: temperature_2m IS NULL OR temperature_2m BETWEEN -40 AND 50
     expect plausible_wind: wind_speed_100m IS NULL OR wind_speed_100m BETWEEN 0 AND 75
3. src/pipelines/gold_weather.py — materialized view gold_energy_weather_hourly:
   per hour_utc, aggregate weather by role: avg temperature over 'load' locations, avg wind_speed_100m over
   'onshore_wind', wind_speed_100m of 'offshore_wind', avg shortwave_radiation over 'solar';
   LEFT JOIN onto gold_energy_hourly on hour_utc. Keep hour_local (TIMESTAMP_NTZ -> needs
   table_properties {"delta.feature.timestampNtz": "supported"}). Add table and column comments.
4. Add weather_landing_path to resources/energy_pipeline.yml configuration.
5. Add pure helpers + tests (role aggregation mapping) in transforms.py / tests.
6. Update CLAUDE.md.
```

**Key design points:**
- **Seconds vs. milliseconds:** SMARD timestamps are in milliseconds, Open-Meteo's in seconds. Using the wrong conversion puts every weather row in 1970, and the join finds nothing.
- **LEFT JOIN onto energy:** every energy hour stays in gold, even if weather is missing for it. Missing weather shows up as `null`, which makes gaps visible instead of hiding rows.
- **Drop forecast hours in silver:** gold should only contain observed history, not predictions.

---

### Step 06 — Deploy, run and verify

```powershell
uv run pytest
databricks bundle deploy -t dev -p free
databricks bundle run energy_pipeline -t dev -p free
```

Or, as a single command that also syncs fresh data first:

```powershell
.\scripts\sync_landing.ps1 -RunPipeline
```

**Expected result:** `Update ... is COMPLETED.` The pipeline graph (`databricks bundle open energy_pipeline -t dev -p free`) now shows 7 tables.

**Check each layer in SQL Editor.** Always verify layer by layer: when the final number is wrong, you immediately know where to look.

```sql
-- Bronze: one row per weather file. Expected: files = 7 (more after repeated syncs), locations = 7
SELECT count(*) AS files, count(DISTINCT location_id) AS locations
FROM workspace.energy_dev.bronze_weather_raw;

-- Silver: ~7 x 1,500 rows, timestamps in the last ~9 weeks (not 1970!)
SELECT count(*) AS rows, min(timestamp_utc) AS first_ts, max(timestamp_utc) AS last_ts
FROM workspace.energy_dev.silver_weather_hourly;

-- Gold: almost every energy hour has weather
SELECT count(*) AS hours,
       count(wind_speed_100m_onshore_avg_ms) AS hours_with_weather
FROM workspace.energy_dev.gold_energy_weather_hourly;
```

**Expected:** `hours` ≈ 1,488 and `hours_with_weather` almost the same.

Column names depend on the generated code. Check yours with:

```sql
DESCRIBE workspace.energy_dev.gold_energy_weather_hourly;
```

In my run the onshore wind column is called `wind_speed_100m_onshore_avg_ms`.

📸 `lab3-step06-pipeline-graph.png`

---

### Step 07 — Analyse: the merit-order effect

**What and why:** the first real insight from your lakehouse. In the electricity market, plants are dispatched from cheapest to most expensive (the **merit order**). Wind and solar have almost zero marginal cost, so when they produce a lot, expensive gas plants aren't needed and the price drops.

**First attempt, all hours:**

```sql
SELECT round(wind_speed_100m_onshore_avg_ms) AS wind_ms,
       round(avg(price_eur_mwh), 1)          AS avg_price,
       round(avg(renewables_share), 2)       AS ren_share,
       count(*)                              AS hours
FROM workspace.energy_dev.gold_energy_weather_hourly
WHERE wind_speed_100m_onshore_avg_ms IS NOT NULL
GROUP BY 1
ORDER BY 1;
```

**My result (first rows):**

| wind_ms | avg_price | ren_share | hours |
| --- | --- | --- | --- |
| 0 | 166.7 | 0.37 | 4 |
| 1 | 156.4 | 0.52 | 29 |
| 2 | 186.1 | 0.37 | 68 |
| 3 | 193.3 | 0.36 | 121 |

The trend isn't clean: at 1 m/s the renewables share is 0.52. The reason is **solar**. Calm, sunny midday hours have a high renewables share without any wind. Solar is a confounding factor.

**Second attempt, night hours only (no solar):**

```sql
SELECT round(wind_speed_100m_onshore_avg_ms) AS wind_ms,
       round(avg(price_eur_mwh), 1)          AS avg_price,
       round(avg(renewables_share), 2)       AS ren_share,
       count(*)                              AS hours
FROM workspace.energy_dev.gold_energy_weather_hourly
WHERE wind_speed_100m_onshore_avg_ms IS NOT NULL
  AND hour(hour_local) NOT BETWEEN 7 AND 18      -- night: no solar
GROUP BY 1
ORDER BY 1;
```

**My result (first rows):**

| wind_ms | avg_price | ren_share | hours |
| --- | --- | --- | --- |
| 0 | 213.4 | 0.08 | 2 |
| 1 | 201.9 | 0.13 | 6 |
| 2 | 237.0 | 0.13 | 30 |
| 3 | 230.1 | 0.14 | 50 |
| 4 | 194.8 | 0.20 | 67 |
| 5 | 188.4 | 0.23 | 113 |

Now the renewables share rises steadily with wind speed and the price starts to fall. That's the merit-order effect in your own data.

**How to read it correctly:**
- **Prices look high** because "night" here (19:00–06:59) includes the **evening peak** around 19–21 h, the most expensive hours of the day. For pure night, use `hour(hour_local) BETWEEN 0 AND 5`.
- **Few hours at 0–1 m/s** (2 and 6 hours), so these averages are noisy. Add `HAVING count(*) >= 20` to show only reliable buckets.

**Make it a chart:** in the result panel click **+ → Visualization → Line chart**, x = `wind_ms`, y = `avg_price` (optionally `ren_share` on a second axis). Save it.

📸 `lab3-step07-merit-order-chart.png`

---

### Step 08 — Commit and tag

```powershell
uv run pytest
git add .
git commit -m "feat: weather ingestion, energy-weather gold table, sync script with upload verification"
git push
git tag lab3a-complete
git push --tags
```

**Expected result:** CI is green. `data/` is not in the commit.

---

## 4. Checkpoint

All of the following must be true:

1. `LIST '/Volumes/workspace/energy_dev/landing/'` shows `smard/` and `weather/`.
2. `bronze_weather_raw` has 7 locations.
3. `silver_weather_hourly` timestamps cover the last ~9 weeks.
4. In `gold_energy_weather_hourly`, `hours_with_weather` ≈ `hours`.
5. The night-only query shows the renewables share rising with wind speed.
6. `.\scripts\sync_landing.ps1 -RunPipeline` runs end to end without errors.
7. CI is green.

---

## 5. Troubleshooting — problems I actually hit

The method throughout: **check the data layer by layer, from the end back to the source, and change code only once you know the cause.**

### P1 — LinkedIn verification didn't unlock outbound internet

**Symptom:** two days after verifying, `requests.get(...)` from a Databricks notebook still raised a connection error for both SMARD and Open-Meteo.
**Cause:** Free Edition network limits aren't under our control. Verification may be pending or may not apply to every domain.
**Solution:** don't depend on it. Extract on the laptop and upload with the CLI (`sync_landing.ps1`). In Lab 4, GitHub Actions takes over the laptop's role. Its runners have internet.

### P2 — My query used a column name that doesn't exist

**Symptom:** an error that the column `onshore_wind_100m` can't be resolved.
**Cause:** the generated code named the column `wind_speed_100m_onshore_avg_ms`. Generated code doesn't always match the names in a tutorial.
**Solution:** check real names with `DESCRIBE workspace.energy_dev.gold_energy_weather_hourly;` and adjust the query.

### P3 — Gold had no weather: `hours_with_weather = 0`

**Symptom:**

| hours | hours_with_weather |
| --- | --- |
| 1488 | 0 |

The energy side was complete, but every weather column was null. The pipeline still said `COMPLETED`.

**Investigation, layer by layer from the end:**

1. **Bronze:**
   ```sql
   SELECT count(*) AS files, count(DISTINCT location_id) AS locations
   FROM workspace.energy_dev.bronze_weather_raw;
   ```
   Result: `0 | 0`. Weather never entered the pipeline, so silver and gold were empty only as a consequence.
2. **Volume:**
   ```sql
   LIST '/Volumes/workspace/energy_dev/landing/weather/';
   ```
   Result: *no such file or directory*. Catalog Explorer showed only `smard/` under `landing/`.
3. **Laptop:**
   ```powershell
   uv run python -m energy_lakehouse.extract_local --out data/landing --source weather
   ```
   Result: `{'written': 0, 'skipped': 7, 'failed': 0}`. The files existed locally. Extraction was fine.

**Cause:** the files were extracted but **never uploaded**. The upload step had failed silently: no error, so nothing looked wrong. The pipeline ran correctly on an empty folder. Auto Loader simply found no files.

**Solution:**
1. Upload manually to confirm the path works:
   ```powershell
   databricks fs cp -r data\landing\weather dbfs:/Volumes/workspace/energy_dev/landing/weather --overwrite -p free
   ```
2. Rerun the pipeline: `databricks bundle run energy_pipeline -t dev -p free`. Bronze then had 7 files, and gold had weather for almost every hour.
3. Fix the root cause: the sync script now checks `$LASTEXITCODE` after every command and verifies each upload target with `databricks fs ls`, failing if it's empty (Step 04).

**Lesson:** *a step that fails silently is worse than a step that fails loudly.* Every data movement should be verified, not assumed.

### P4 — The wind-vs-price trend looked noisy

**Symptom:** with all hours included, renewables share jumped around at low wind speeds (0.52 at 1 m/s).
**Cause:** solar generation at calm, sunny midday hours, a confounding variable.
**Solution:** filter to night hours (`hour(hour_local) NOT BETWEEN 7 AND 18`), and exclude small buckets (`HAVING count(*) >= 20`).

### P5 — "Running scripts is disabled on this system"

**Cause:** Windows blocks PowerShell scripts by default.
**Solution:** `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (one time, only for your user).

### Other problems you may hit

| Problem | Cause | Fix |
| --- | --- | --- |
| Silver weather timestamps are in 1970 | Open-Meteo uses Unix **seconds**, but the code used `timestamp_millis` | Use `timestamp_seconds(...)` or `from_unixtime(...)` for weather |
| Gold weather values shifted by 1–2 hours | Join on local time on one side and UTC on the other | Join on `hour_utc` on both sides |
| Weather columns null for the newest hours | Silver drops forecast hours; the latest energy hours may not have weather yet | Expected. The next sync fills them. |
| Nested `landing/weather/weather/` folder | `fs cp -r` target pointed at a parent folder | Delete the nested folder, fix the target path, rebuild with `--full-refresh-all` (dev only) |
| Bronze weather rows increase on every sync | Each sync writes a new `run_date` file per location | Expected. Silver deduplicates with "newest file wins". |

---

## 6. What you learned

- Adding a **second data source** to an existing lakehouse without changing the first
- Designing **location roles**, so the gold layer aggregates weather where it matters physically
- Handling **different time units** (milliseconds vs. seconds) and joining sources on UTC
- Refreshing **late-arriving data** (the newest SMARD chunks) while keeping older loads idempotent
- Writing a **sync script that fails loudly** and verifies every upload
- **Layer-by-layer debugging**: bronze → volume → laptop, to find a silent failure
- A first **analytical insight**: the merit-order effect, and how to remove a **confounding factor** (solar) from the analysis

**Interview-ready summary:** *"I joined German power-market data with weather from 7 role-based locations in a medallion pipeline. A silent upload failure produced an empty join. I traced it layer by layer from gold back to the laptop, then made the sync script verify every upload. The result shows the merit-order effect: at night, higher wind speeds raise the renewables share and lower the day-ahead price."*

---

## 7. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-19 |
| OS | Windows 11 |
| Databricks | Free Edition (serverless), no outbound internet |
| Databricks CLI | `<output of databricks -v>` |
| uv | `<output of uv --version>` |
| Python | 3.12.x |
| Data | SMARD (8 series), Open-Meteo (7 locations, 63 past days), hourly |

**Data attribution:** Bundesnetzagentur | SMARD.de (CC BY 4.0); weather data by Open-Meteo.com (CC BY 4.0).

**Next:** Lab 4 — Daily automated refresh: a GitHub Actions workflow authenticated with a Databricks service principal via OIDC runs extract → upload → pipeline on a schedule.
