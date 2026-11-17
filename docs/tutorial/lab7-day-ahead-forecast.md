# Lab 7 — Day-Ahead Load and Price Forecast: Features, Baseline, MLflow, Unity Catalog Models and Daily Scoring

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 6 — Data contracts. Next: Lab 8 — ML-assisted data quality.

## 1. Goal

So far the lakehouse describes the **past**. In this lab it predicts the **future**: every morning it forecasts Germany's electricity **load** and **day-ahead price** for every hour of the next day. That's the core business question for energy trading and grid operations.

By the end you will have:

- **One year of history** (SMARD backfill + Open-Meteo archive) and a **weather forecast** for tomorrow
- Leakage-safe **features**: calendar, German public holidays, lags ≥ 48 h, weather by role
- A **baseline** (same hour last week) and a **gradient-boosting model**, compared in **MLflow**
- Models registered in **Unity Catalog** with an alias **`champion`**, promoted only if they beat the baseline
- A weekly **training job** and a daily **scoring task** writing `gold_forecast_hourly`
- `gold_forecast_accuracy`: forecast vs. actual, every day
- Guards that turn silent failures (no model, no rows) into visible ones
- Everything in dev, staging and prod via the Lab 5 release process (`v0.3.0`)

**My results (14-day hold-out test):**

| Target | Model | Baseline (same hour last week) | Improvement |
| --- | --- | --- | --- |
| Load (prod v1) | MAE 1,264 MWh, **MAPE 2.37 %** | MAE 1,742 MWh, MAPE 3.31 % | ~27 % lower error |
| Price (prod v1) | **MAE 43.0 EUR/MWh** | MAE 62.4 EUR/MWh | ~31 % lower error |

**Time:** plan a full day, plus two days of waiting for the first live accuracy row.

---

## 2. Concepts: forecasting honestly

### 2.1 The forecasting setup

| Term | In this project |
| --- | --- |
| **Issue time** | When the forecast is made: daily ~05:17 UTC on day **D** |
| **Target period** | Every hour of **D+1**, as a **German local day** (the day-ahead delivery day) |
| **Horizon** | ~19 to ~43 hours ahead |
| **Targets** | `total_load_mwh`, `price_eur_mwh` |
| **Available at issue time** | Actuals up to about D-1 (SMARD publishes with a delay), calendar, holidays, **weather forecast** for D+1 |

### 2.2 Leakage: the number-one forecasting mistake

**Leakage** means using information during training or evaluation that wouldn't exist at the moment of the real forecast. The backtest looks fantastic, and the live forecast is bad.

Typical traps here:

- **Short lags:** "load 1 hour ago" doesn't exist when forecasting tomorrow at 05:17. **Rule: every target lag ≥ 48 h** (`MIN_LAG_HOURS = 48`), enforced by a test.
- **Observed instead of forecast weather:** tomorrow's *real* wind is unknown at 05:17; only a *forecast* is. Live scoring uses the forecast. For the training history we use observed weather as a stand-in, a **documented simplification** that makes the backtest slightly optimistic.
- **Shuffled splits:** a random train/test split puts future hours into training. **Always split by time.**

**Red flag:** a load MAPE below ~1 % or a price MAE below ~5 EUR/MWh is almost certainly leakage.

### 2.3 Baseline first

A model is only worth running if it beats something simple. Our **seasonal-naive baseline**: *tomorrow at 14:00 = last week, same weekday, 14:00*. Electricity demand repeats weekly, so this baseline is strong for load (MAPE ~3.3 %), which makes beating it meaningful.

### 2.4 Metrics

| Metric | Meaning | Used for |
| --- | --- | --- |
| **MAE** | Mean absolute error, in the target's unit | Load (MWh) and price (EUR/MWh) |
| **MAPE** | Mean absolute percentage error | Load only. For prices near zero or negative, percentages explode (`nan` is intended). |

### 2.5 Champion alias: safe promotion

Each training run registers a **new model version** in Unity Catalog. The alias **`champion`** marks the version the scoring task uses. It moves **only if** the new version beats the baseline on the hold-out period. A worse model never reaches production automatically.

### 2.6 Batch scoring instead of a serving endpoint

A day-ahead forecast is needed **once a day**, so a scheduled task loads `@champion`, writes 24 rows per target, and stops. A real-time **Model Serving** endpoint (and MLflow deployment jobs) would add cost and complexity for no benefit here. They appear in Project 2, where they fit.

### 2.7 Deploy → train → score

In a **new environment**, the order matters:

1. **Deploy** the code (CI/CD, Lab 5)
2. **Train** once (no model exists yet)
3. **Score** (the daily job)

If scoring runs before training, the guard fails loudly (Troubleshooting P9).

---

## 3. Architecture

```
history: SMARD 1 year + Open-Meteo archive          tomorrow: Open-Meteo forecast (run_ts files)
        │                                                     │
        ▼                                                     ▼
 gold_energy_weather_hourly ──► features (calendar, holidays, lags ≥ 48 h, weather by role)
        │                                         │
        │ weekly: train_forecast_job              │ daily: score_forecast (daily_refresh_job)
        ▼                                         ▼
 MLflow experiment (baseline vs. GBM)      UC model workspace.energy_<env>.forecaster_<target>@champion
        └──────────────► register (+ champion if better) ┘
                                                  ▼
                               gold_forecast_hourly   (D+1, 24 rows per target)
                                                  ▼
                               gold_forecast_accuracy (forecast vs. actual, daily MAE/MAPE)
```

Daily job tasks after this lab:

```
run_pipeline → quality_checks → apply_contracts → verify_contracts → score_forecast → forecast_accuracy
```

---

## 4. Prerequisites

- Labs 0–6 completed (contracts, CI/CD, daily refresh in prod)
- Local Python from python.org (Lab 6 P1, if Smart App Control is on)
- Golden rules: branch first, commit before experiments, local checks before push, read logs, never paste secrets
- **New golden rule:** **one dependency mechanism per job: job environments.** No `%pip install` in job notebooks.

---

## 5. Steps

### Step 01 — Backfill history and add a weather forecast

**What and why:** nine weeks of data can't teach weekly and seasonal patterns. SMARD goes back to 2015, Open-Meteo has an **archive API** for history and a **forecast API** for tomorrow. Forecasts change during the day, so **every run gets its own file** (`run_ts=`), never overwritten (Troubleshooting P1).

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c feat/forecast-model
```

**Claude Code prompt:**

```text
Read CLAUDE.md first. Prepare data for a day-ahead forecast. Do not deploy.

1. Weather extractor (src/energy_lakehouse/weather.py + extract_local):
   a) --mode archive --start YYYY-MM-DD --end YYYY-MM-DD: use https://archive-api.open-meteo.com/v1/archive
      (same hourly variables, timezone=GMT, timeformat=unixtime, wind_speed_unit=ms), one file per location:
      weather/location={id}/run_date=archive_{start}_{end}.json
   b) default (daily) mode: forecast API with past_days=7 and forecast_days=3.
      File name: weather/location={id}/run_ts=YYYYMMDDTHHMMZ.json (UTC run timestamp). Never overwrite —
      every run is a new forecast. Bronze parses run_ts (or run_date for archive files) into run_ts_utc.
   Offline tests for URL builders and file naming/parsing.
2. Pipeline:
   - silver_weather_hourly: observed hours only (<= now), newest file wins per (location, hour), ordered by run_ts_utc.
   - NEW silver_weather_forecast_hourly: hours > run_ts_utc of the newest file per location (= forecast available
     at issue time). Null-safe expectations.
   - NEW gold_weather_forecast_hourly: same role aggregation as gold_energy_weather_hourly, for future hours.
3. Contracts for the two new tables + matching column constants in transforms.py (Lab 6 pattern).
4. Update sync scripts' help text and CLAUDE.md (backfill commands).
List changed files and the exact backfill commands.
```

**Backfill once** (52 weeks SMARD, 365 days weather), then the daily forecast:

```powershell
uv run pytest -q
uv run python -m energy_lakehouse.extract_local --out data/landing --source smard --weeks 52
uv run python -m energy_lakehouse.extract_local --out data/landing --source weather --mode archive --start 2025-10-15 --end 2026-10-15
uv run python -m energy_lakehouse.extract_local --out data/landing --source weather
.\scripts\sync_landing.ps1
databricks bundle deploy -t dev -p free
databricks bundle run energy_pipeline -t dev -p free
```

**Expected:** SMARD writes ~8 × 44 new weeks (existing weeks are skipped), weather writes 7 archive files + 7 `run_ts` files, the uploads are verified, and the pipeline completes. No full refresh is needed, because Auto Loader picks up the new files.

```sql
SELECT min(hour_utc), max(hour_utc), count(*) FROM workspace.energy_dev.gold_energy_weather_hourly;
SELECT min(hour_utc), max(hour_utc), count(*) FROM workspace.energy_dev.gold_weather_forecast_hourly;
```

**Expected:** history from ~October 2025 to yesterday (~8,700 hours), and forecast hours from the next hour until ~2–3 days ahead (~50–70 rows), covering tomorrow's full local day.

---

### Step 02 — Features, training, registry, scoring, accuracy

**What and why:** the forecasting core. All logic that can be pure Python lives in `forecast.py` and is unit-tested offline. Notebooks only orchestrate.

**Claude Code prompt** (includes the fixes for P3–P7 and P10):

```text
Read CLAUDE.md first. Add a day-ahead forecast for total_load_mwh and price_eur_mwh. Do not deploy.

Setup: issued daily ~05:17 UTC on day D for every hour of D+1 (Europe/Berlin local day). Only information
available at issue time may be used.

1. src/energy_lakehouse/forecast.py (pandas, Spark-free, unit-tested):
   - build_features(history_df, weather_df, issue_time): calendar (hour_local, dow, month, is_weekend,
     German public holiday via `holidays`, DE), lags of the target at 48h, 72h, 168h, 336h,
     rolling mean of the same hour over the last 7 available days ending >= 48h before target,
     weather by role (temperature load avg, wind onshore/offshore 100m, solar radiation).
   - MIN_LAG_HOURS = 48 and an assertion that no feature uses data newer than issue_time.
   - seasonal_naive(history_df, target, lag_hours=168) baseline; metrics mae, mape (skip |y| < 1); time_split(df, test_days=14).
2. src/notebooks/10_train_forecast.py — params catalog, schema, target: train sklearn HistGradientBoostingRegressor
   (fixed random_state), evaluate baseline vs model on the last 14 days. MLflow experiment
   /Users/<current user>/energy_forecast_<target>: params, metrics (baseline_mae, model_mae, baseline_mape, model_mape),
   forecast-vs-actual plot, permutation feature importance, model with signature + input example,
   tags with numpy/scikit-learn/python versions. Register {catalog}.{schema}.forecaster_<target>; set alias
   "champion" ONLY if model_mae < baseline_mae.
3. src/notebooks/11_score_forecast.py — load models:/{catalog}.{schema}.forecaster_<target>@champion for both targets
   (warn if champion's numpy/scikit-learn versions differ from the current environment), build features for D+1 from
   gold_energy_weather_hourly + gold_weather_forecast_hourly, MERGE into gold_forecast_hourly on
   (target_hour_utc, target, issue_date). issue_date = German date. FAIL if a target is skipped (no champion,
   missing weather) or has fewer than 23 rows.
4. src/notebooks/12_forecast_accuracy.py — refresh gold_forecast_accuracy (per target and issue_date: n_hours, mae, mape,
   baseline_mae) by joining forecasts with actuals in gold_energy_hourly.
5. Contracts for gold_forecast_hourly (incl. rule: latest issue_date has 23–25 rows per target) and gold_forecast_accuracy.
   "Today" is always to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin')).
6. resources/train_forecast_job.yml — serverless, tasks for load and price, weekly schedule Monday 06:37 UTC.
   resources/daily_refresh_job.yml — add score_forecast (after verify_contracts) and forecast_accuracy.
   Dependencies ONLY via job environments (NO %pip, NO dbutils.library.restartPython() in notebooks):
     environment "contracts": pydantic, pyyaml  (apply_contracts, verify_contracts)
     environment "forecast":  holidays, scikit-learn, mlflow, pydantic, pyyaml  (train tasks, score_forecast, forecast_accuracy)
   Train and score MUST use the identical "forecast" environment definition. Never install pyspark,
   databricks-connect, delta-spark, databricks-sdk, numpy, pandas or pyarrow in job environments.
7. Tests (offline): no lag < 48h; leakage test (no feature row uses data after issue_time); seasonal_naive; metrics;
   chronological split; holiday flag for 2026-12-25;
   tests/test_job_environments.py: forbidden packages absent; no "%pip"/"restartPython" in src/notebooks;
   tasks importing energy_lakehouse.forecast use an environment with holidays/scikit-learn/mlflow;
   train and score environments identical.
8. Add scikit-learn, holidays, mlflow to pyproject/uv.lock (no machine-specific uv settings). Update CLAUDE.md.
List changed files and checkpoint commands.
```

---

### Step 03 — Local checks and "test the test"

```powershell
uv sync
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
git add -A ; git commit -m "wip: forecast features, training, scoring"
```

**Prove the leakage guard:** in `forecast.py`, set `MIN_LAG_HOURS = 1` and add a 1 h lag. **Save.**

```powershell
git diff --stat                            # forecast.py must appear
uv run pytest tests/test_forecast.py -q    # expected: leakage test FAILS
git checkout -- src/energy_lakehouse/forecast.py
uv run pytest -q                           # expected: all pass
```

The checkout is safe only because you committed first (Lab 6 P2).

📸 `lab7-step03-leakage-caught.png`

---

### Step 04 — Train in dev and compare in MLflow

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run train_forecast_job -t dev -p free
```

**Expected:** both tasks `SUCCESS`.

**Where to find the results:** left sidebar → **Experiments**. On the top tabs choose **All** (not *Machine learning*), on the filter buttons **All** (not *Recent*, which only lists experiments you've opened), then search `energy_forecast`. Open the newest run → **Metrics** and **Artifacts** (the plot).

Prod runs live in the **service principal's** folder (`/Users/<sp-application-id>/energy_forecast_<target>`), so they don't appear under *Owned by me*.

**Reliable alternative:** go from the champion model to its run in a notebook:

```python
from mlflow import MlflowClient
import mlflow
mlflow.set_registry_uri("databricks-uc")
c = MlflowClient(registry_uri="databricks-uc")
for env in ["energy_dev", "energy_prod"]:
    for t in ["total_load_mwh", "price_eur_mwh"]:
        name = f"workspace.{env}.forecaster_{t}"
        try:
            v = c.get_model_version_by_alias(name, "champion")
            m = mlflow.get_run(v.run_id).data.metrics
            print(f"{env:12} {t:15} v{v.version}  model_mae={m['model_mae']:.1f}  baseline_mae={m['baseline_mae']:.1f}  "
                  f"model_mape={m.get('model_mape', float('nan')):.2f}  baseline_mape={m.get('baseline_mape', float('nan')):.2f}")
        except Exception as e:
            print(env, t, "->", type(e).__name__, e)
```

**My output:**

```
energy_dev   total_load_mwh  v2  model_mae=1387.4  baseline_mae=1784.4  model_mape=2.65  baseline_mape=3.41
energy_dev   price_eur_mwh   v2  model_mae=41.3    baseline_mae=65.9    model_mape=nan   baseline_mape=nan
energy_prod  total_load_mwh  v1  model_mae=1263.5  baseline_mae=1741.6  model_mape=2.37  baseline_mape=3.31
energy_prod  price_eur_mwh   v1  model_mae=43.0    baseline_mae=62.4    model_mape=nan   baseline_mape=nan
```

**How to read it:**
- Load: ~2.4–2.7 % MAPE vs. a strong 3.3–3.4 % baseline. That's realistic, so no sign of leakage.
- Price: ~42 EUR/MWh vs. ~64. Prices are volatile, and the model is a third better, mainly thanks to the wind forecast.
- `nan` MAPE for price is intended.

In the run's artifacts, also check:
- **the forecast-vs-actual plot:** daily shape, weekends, holidays
- **feature importance:** calendar and temperature dominate for load, wind and solar for price

📸 `lab7-step04-mlflow-compare.png`

---

### Step 05 — Check the model registry

**Catalog Explorer → workspace → energy_dev → Models** → `forecaster_total_load_mwh`, `forecaster_price_eur_mwh` → each has a version with the alias **`@champion`** and a *Source run* link.

You'll also see **"Connect deployment job"** and **"Active serving endpoints: not served"**. Both are intentionally unused (Concepts 2.6). The same two names may also appear under **Functions**; that's how Unity Catalog lists registered models in some views, not extra objects.

📸 `lab7-step05-registry.png`

---

### Step 06 — Daily scoring in dev

```powershell
databricks bundle run daily_refresh_job -t dev -p free
```

**Expected:** six tasks `SUCCESS`.

```sql
SELECT target, count(*) AS hours, min(target_hour_local), max(target_hour_local), max(model_version)
FROM workspace.energy_dev.gold_forecast_hourly
WHERE issue_date = (SELECT max(issue_date) FROM workspace.energy_dev.gold_forecast_hourly)
GROUP BY target;
```

**Expected:** 2 rows, 24 hours each (23 or 25 on a DST day), from tomorrow 00:00 to 23:00 local time.

**Don't filter with `current_date()`.** The SQL warehouse runs in **UTC**, so after 22:00/23:00 German time it still returns yesterday's date (Troubleshooting P8). For "today in Germany" use:

```sql
to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin'))
```

Run the job a second time. The counts stay the same (MERGE is idempotent).

---

### Step 07 — Sanity-check the forecast (no need to wait two days)

**What and why:** live accuracy needs actuals, which arrive one to two days later. You can still check today that the forecast is **plausible and explainable**.

**Forecast vs. same hour last week:**

```sql
WITH f AS (
  SELECT target_hour_local, target, forecast
  FROM workspace.energy_prod.gold_forecast_hourly
  WHERE issue_date = (SELECT max(issue_date) FROM workspace.energy_prod.gold_forecast_hourly)
),
last_week AS (
  SELECT hour_local + INTERVAL 7 DAYS AS target_hour_local, total_load_mwh, price_eur_mwh
  FROM workspace.energy_prod.gold_energy_hourly
)
SELECT f.target_hour_local,
       max(CASE WHEN f.target = 'total_load_mwh' THEN round(f.forecast) END)     AS load_forecast,
       max(round(lw.total_load_mwh))                                           AS load_same_hour_last_week,
       max(CASE WHEN f.target = 'price_eur_mwh'  THEN round(f.forecast, 1) END) AS price_forecast,
       max(round(lw.price_eur_mwh, 1))                                         AS price_same_hour_last_week
FROM f LEFT JOIN last_week lw ON f.target_hour_local = lw.target_hour_local
GROUP BY f.target_hour_local
ORDER BY f.target_hour_local;
```

**My result (Saturday, 24 Oct 2026, first hours):**

| Hour | Load forecast | Load last week | Price forecast | Price last week |
| --- | --- | --- | --- | --- |
| 00:00 | 43,961 | 43,273 | 69.7 | 200.4 |
| 03:00 | 41,533 | 41,784 | 78.1 | 152.4 |
| 07:00 | 46,514 | 47,590 | 112.2 | 165.2 |

Load is close to last week but not identical, which is plausible. **Price is half of last week.** Is that a bug? Check the weather:

```sql
WITH tomorrow AS (
  SELECT hour_local, wind_speed_100m_onshore_avg_ms AS wind, shortwave_radiation_solar_avg_wm2 AS solar
  FROM workspace.energy_prod.gold_weather_forecast_hourly
  WHERE to_date(hour_local) = date'2026-10-24'
),
last_week AS (
  SELECT hour_local + INTERVAL 7 DAYS AS hour_local, wind_speed_100m_onshore_avg_ms AS wind,
         shortwave_radiation_solar_avg_wm2 AS solar
  FROM workspace.energy_prod.gold_energy_weather_hourly
  WHERE to_date(hour_local) = date'2026-10-17'
)
SELECT t.hour_local, round(t.wind, 1) AS wind_tomorrow, round(l.wind, 1) AS wind_last_week,
       round(t.solar) AS solar_tomorrow, round(l.solar) AS solar_last_week
FROM tomorrow t LEFT JOIN last_week l ON t.hour_local = l.hour_local
ORDER BY t.hour_local;
```

**My result:** wind tomorrow **11–13.5 m/s** vs. **6–9 m/s** last Saturday, with solar 0 at night in both. Tomorrow is almost twice as windy, so cheap wind power pushes expensive gas plants out of the market and the price drops: **the merit-order effect from Lab 3, applied to a future day.** The model reacts to the weather forecast instead of copying last week.

**Red flags in a sanity check:** flat lines, identical values, nulls, an exact copy of last week, or weather columns that are NULL for tomorrow.

📸 `lab7-step07-sanity-check.png`

---

### Step 08 — Release to staging and prod (deploy → train → score)

```powershell
git branch --show-current            # feat/forecast-model, NOT master
git status ; git log --oneline origin/master..HEAD
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
git add -A ; git commit -m "feat: day-ahead forecast (MLflow, UC models, scoring, accuracy)"
git push -u origin feat/forecast-model
gh pr create --fill --base master
```

Wait ~10 s → `gh pr checks --watch` → ✓ → `gh pr merge --squash --delete-branch`:

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
gh run list --workflow deploy-staging.yml --limit 1
```

**Expected:** the staging **deploy** step is ✓. `score_forecast` in staging fails, because there's no model yet. That's the guard working (P9).

**Staging:** **Jobs & Pipelines → `[staging] train_forecast_job` → Run now** → ✓, then `gh workflow run deploy-staging.yml` → ✓.

**Prod:**

```powershell
gh release create v0.3.0 --target master --generate-notes --title "v0.3.0 - day-ahead forecast"
```

Approve → `deploy-prod` ✓ → **`[prod] train_forecast_job` → Run now** → ✓ → check the champions:

```
total_load_mwh {'champion': '1'}
price_eur_mwh {'champion': '1'}
```

Then score:

```powershell
gh workflow run daily-refresh.yml
```

Wait ~10 s, then `gh run watch` (start it **once** and wait; don't trigger twice, see P11).

```sql
SELECT target, count(*) AS hours, min(target_hour_local), max(target_hour_local), max(model_version)
FROM workspace.energy_prod.gold_forecast_hourly
WHERE issue_date = (SELECT max(issue_date) FROM workspace.energy_prod.gold_forecast_hourly)
GROUP BY target;
```

**Expected:** 2 rows, 24 hours each, `model_version = 1`. From now on, the daily refresh scores prod every morning, and the training job retrains every Monday.

📸 `lab7-step08-prod-forecast.png`

---

### Step 09 — Forecast vs. actual (one to two days later)

```sql
SELECT issue_date, target, n_hours, round(mae, 1) AS mae, round(mape, 2) AS mape, round(baseline_mae, 1) AS baseline_mae
FROM workspace.energy_prod.gold_forecast_accuracy
ORDER BY issue_date DESC, target;
```

**Timeline:** the forecast issued on 23 Oct is for 24 Oct. SMARD publishes the 24th's actuals with a delay, so the first row appears on ~25 Oct (possibly partial) and is complete by ~26 Oct. 25 Oct is the DST change, so that forecast day has **25 hours**.

**Expected:** model `mae` below `baseline_mae` on most days.

📸 `lab7-step09-accuracy.png`

### Step 10 — Tag

```powershell
git fetch origin ; git tag lab7-complete origin/master ; git push origin lab7-complete
```

---

## 6. Checkpoint

1. History covers ~1 year. The forecast table covers tomorrow's full local day.
2. The leakage test fails when you introduce a 1 h lag (seen once), and passes otherwise.
3. MLflow shows model vs. baseline for both targets, and the model wins.
4. Both models carry `@champion` in dev, staging and prod.
5. The daily job has six green tasks. `gold_forecast_hourly` has 24 rows per target for tomorrow.
6. The sanity check is plausible and explainable by weather.
7. `gold_forecast_accuracy` fills in from the second day on.

---

## 7. Troubleshooting — problems I actually hit

This lab produced the most problems of the series, almost all of them classic ML-in-production issues.

### P1 — Forecast table had only 5 future hours (idempotent file names)

**Symptom:** `gold_weather_forecast_hourly` → `min 19:00`, `max 23:00` **today**, `count 5`. Tomorrow was missing.

**Cause:** daily weather files were named `run_date=YYYY-MM-DD.json`. A file for today already existed (from an earlier run with the old `forecast_days=1`), so the idempotent extractor **skipped** the new download. Overwriting wouldn't help either: Auto Loader ignores files it has already processed.

**Fix:** one file per run, `run_ts=YYYYMMDDTHHMMZ.json`, never overwritten. The current forecast is the newest run. `forecast_days=3` gives a margin for DST and run time.

**Lesson:** idempotent file names are right for **history**, wrong for **forecasts**, which change during the day.

### P2 — CLI login expired: `invalid_grant … Refresh token is invalid`

**Fix:** `databricks auth login --host <workspace-url> --profile free`, then `databricks current-user me -p free`. Clear leftover SP variables first: `Remove-Item Env:DATABRICKS_* -ErrorAction SilentlyContinue`. Automation isn't affected, because it uses SP secrets.

### P3 — `ModuleNotFoundError: No module named 'holidays'`

**Cause:** each serverless task has its own **environment**. `forecast_accuracy` ran without the packages `forecast.py` imports.
**Fix:** environments per purpose (`contracts`, `forecast`) assigned to every task that needs them, plus a test that checks job environments against notebook imports.

### P4 — `SystemError: spark should be initialized with the first notebook command` (and two wrong guesses)

**Symptom:** first `apply_contracts`, then `score_forecast` failed at the first line that touched `spark`. `spark` was an uninitialised placeholder (*TripWire* in the traceback).

**What I tried:**
1. *Too many packages in one shared environment* → split into lean environments. This fixed `apply_contracts`, but not `score_forecast`.
2. *Environment differs from the working training job* → made them identical. **Still failed.**
3. **Compared a working notebook with the failing one:** both started with
   ```
   # MAGIC %pip install -q -r forecast_requirements.txt
   dbutils.library.restartPython()
   ```
   on top of the job environment.

**Cause:** **two dependency mechanisms**. The `restartPython()` in a task with its own job environment left `spark` uninitialised.
**Fix:** job environments only. Remove `%pip` and `restartPython()` from all job notebooks, and add a test that rejects both.

**Lesson:** the same error message can have different causes. When guesses fail, **compare a working artifact with a failing one**.

### P5 — `ValueError: <class 'numpy.random._pcg64.PCG64'> is not a known BitGenerator module`

**Symptom:** `mlflow.pyfunc.load_model("models:/…@champion")` failed in `cloudpickle.load`.
**Cause:** model v1 was trained while the notebook still `%pip`-installed packages (probably NumPy 2.x). Scoring used the runtime's NumPy 1.x, and a NumPy-2 pickle can't be loaded with NumPy 1.
**Fix:** retrain under the same environment as scoring (v2 became champion), enforce **identical train/score environments** with a test, and log library versions as MLflow tags with a warning on mismatch.
**Lesson:** a pickled model is only as portable as its environment.

### P6 — The champion might not move

If a retrained model doesn't beat the baseline, `champion` stays on the old version. That's correct behaviour, but if the old version can't be loaded (P5), scoring fails. Check the metrics, and only in **dev** move the alias manually: `MlflowClient(registry_uri="databricks-uc").set_registered_model_alias(name, "champion", "2")`.

### P7 — All tasks green, but no forecast rows (silent success)

**Risk:** the scoring notebook skipped targets with a warning and still "succeeded".
**Fix:** `score_forecast` **fails** if a target is skipped or has fewer than 23 rows. Plus a contract rule: the latest `issue_date` has 23–25 rows per target.
**Lesson:** a task that succeeds while producing nothing is a silent failure (the same class as the Lab 3 upload problem).

### P8 — "No rows returned" for `issue_date = current_date()`

**Investigation:**

```sql
SELECT current_date() AS sql_current_date, current_timestamp() AS sql_now;
-- 2026-10-22 | 2026-10-22T23:50:42Z   (it was 01:50 on 23 Oct in Germany)
```

**Cause:** the warehouse evaluates `current_date()` in **UTC**, while `issue_date` is the **German** date.
**Fix:** filter with `max(issue_date)`, or with `to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin'))`.

### P9 — Prod `score_forecast` failed right after the release

**Cause:** prod had **no trained model** yet. The guard (P7) failed loudly, as designed.
**Fix:** deploy → **train** (`[prod] train_forecast_job` → Run now) → score. Champions then show `{'champion': '1'}`.

### P10 — Staging/prod didn't have the new jobs: work was on the wrong branch

**Symptom:** `[staging] train_forecast_job` didn't exist. The newest `deploy-staging` run was still the Lab 6 merge. `gh pr create` said *"head branch master is the same as base branch master"*, and `Test-Path` on the Lab 7 files returned `False False False`.

**Cause:** after a long night, I was on `master`. The Lab 7 work was committed on `feat/forecast-model`, and switching branches had removed those files from the folder (normal Git behaviour).

**Investigation (without changing anything):**

```powershell
git branch -a
git --no-pager ls-tree -r --name-only feat/forecast-model | Select-String "train_forecast_job|11_score_forecast|forecast.py"
git --no-pager log --oneline master..feat/forecast-model
```

**Fix:** `git switch feat/forecast-model` brought the files back. Push, PR, merge. **Don't** run `git reset --hard origin/master` while unmerged work may be on local `master`.

**Safety net:** the last dev deploy holds a full copy:
`databricks workspace export-dir /Workspace/Users/<you>/.bundle/energy-lakehouse-de/dev/files C:\dev\backup -p free`

**Lesson:** dev working ≠ staging has it. Only **merged** code reaches staging and prod. Before merging, run `git status ; git log --oneline origin/master..HEAD`.

### P11 — Two refreshes running at once

**Cause:** `gh workflow run daily-refresh.yml` was triggered twice. The Databricks job allows one run at a time, so the second is wasted compute.
**Fix:** `gh run cancel <newer-id>`, then `gh run watch <older-id>`. Don't query results before the run is ✓.

### P12 — "Experiments" list is empty

**Cause:** the **Recent** filter (only experiments you've opened) and the *Machine learning* tab. Prod runs also live in the **SP's** folder.
**Fix:** tab **All** + filter **All** + search `energy_forecast`, or go from the model's champion version to its run (Step 04 snippet).

### Other problems you may hit

| Problem | Cause | Fix |
| --- | --- | --- |
| Load MAPE < 1 % | Leakage | Check lags ≥ 48 h, time split, and features after issue time |
| Forecast is an exact copy of last week | The model only learned the 168 h lag | Check feature importance, weather features present for D+1 |
| `score_forecast`: missing weather for D+1 | The newest weather run has no future hours (old `run_date` files, or forecast_days too small) | `run_ts` files, `forecast_days=3` (P1) |
| Registering the model in prod fails | The SP can't create models in the schema | The SP must own `energy_prod` (Lab 5 Step 03) |
| Accuracy table still empty after 2–3 days | Join on mismatched keys (UTC vs. local) | Compare `max(hour_utc)` in `gold_energy_hourly` with the forecast's `target_hour_utc` |
| Weekly training "Scheduled" in dev | `mode: development` pauses schedules | Expected. Check *Schedules & Triggers* → Paused |

---

## 8. What you learned

- Framing a forecast: **issue time, horizon, target period, available information**
- **Leakage** and how to prevent it with a minimum lag, time-based splits and a test that fails on purpose
- **Baseline first**, and choosing **metrics** per target (MAPE for load, MAE for price)
- **MLflow** tracking: params, metrics, plots, feature importance, model signature, environment tags
- **Unity Catalog models** with a **champion alias** and safe, metric-gated promotion
- **Batch scoring** instead of serving, and **deploy → train → score** in new environments
- **Serverless environments:** one mechanism, lean and purpose-specific, identical for train and score
- **Model portability:** pickles depend on library versions
- **Time zones** in SQL (UTC warehouse vs. German business dates)
- Guards against **silent success**
- **Sanity-checking** a forecast before actuals arrive, and explaining it with domain knowledge (the merit-order effect)
- Git hygiene under pressure: finding work on other branches without destroying it

**Interview-ready summary:** *"I built a day-ahead load and price forecast on the lakehouse: leakage-safe features with ≥ 48 h lags, German holidays and role-based weather forecasts, a seasonal-naive baseline, and gradient boosting tracked in MLflow and registered in Unity Catalog with a metric-gated champion alias. A weekly job retrains and a daily task scores tomorrow and evaluates yesterday. On a 14-day hold-out the model reduced load error from 3.3 % to 2.4 % MAPE and price MAE from 62 to 43 EUR/MWh; the live forecast for a windy Saturday correctly predicted half of last week's price. Along the way I fixed serverless dependency conflicts, a NumPy pickle incompatibility and UTC-vs-local date bugs, and added guards so a model that's missing or produces no rows fails loudly."*

---

## 9. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-22/23 |
| OS | Windows 11 (local, Smart App Control on), ubuntu-latest (GitHub runners) |
| Databricks | Free Edition (serverless), one workspace for dev/staging/prod |
| Data | SMARD 52 weeks + Open-Meteo archive 365 days + daily forecast (3 days) |
| Model | scikit-learn HistGradientBoostingRegressor, MLflow, Unity Catalog models |
| Results (prod v1, 14-day hold-out) | Load MAPE 2.37 % (baseline 3.31 %), price MAE 43.0 (baseline 62.4) EUR/MWh |
| Release | v0.3.0 |
| Databricks CLI | `<output of databricks -v>` |

**Next:** Lab 8 — ML-assisted data quality: anomaly detection on silver, monitoring of forecast errors from `gold_forecast_accuracy`, alerts, and an optional LLM-written daily data-quality summary.
