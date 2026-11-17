# Lab 8 — ML-Assisted Data Quality: Seasonal Statistics, Isolation Forest, Drift Monitoring and an LLM Daily Report

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 7 — Day-ahead forecast. Next: Lab 9 — AI/BI dashboard and Genie.

## 1. Goal

Your pipeline already rejects **impossible** values: expectations in Lab 2 (`value IS NOT NULL`, energy ≥ 0) and contracts in Lab 6 (`renewables_share BETWEEN 0 AND 1`). But many real data problems are **possible yet suspicious**:

- solar output reported in the middle of the night
- a series that suddenly delivers only half of its hours
- load 30 % below normal for a Tuesday morning
- an unusual **combination**: low price, no wind, high load
- a forecast that's quietly getting worse every day

This lab adds a daily **ML-assisted data-quality** layer that finds such cases, rates them by **severity**, alerts only when it matters, and writes a short **plain-language report with an LLM**.

By the end you will have:

- `anomalies.py`: tested detectors (seasonal robust z-score, Isolation Forest, completeness, physics rule, forecast drift)
- `gold_dq_anomalies`: one row per finding (what, when, expected, score, severity, reason)
- `gold_dq_daily_summary` and `gold_dq_daily_summary_text` (counts and an LLM-written summary)
- a **severity design** that separates *"unusual"* from *"broken"*
- a test-anomaly **injection** to prove the alert path end to end
- job **queueing**, so overlapping runs wait instead of failing
- release `v0.4.1` in prod

**My prod result after the first runs:** `seasonal_robust_z` warning: 83, `isolation_forest` warning: 1, **critical: 0**.

**Time:** about one working day.

---

## 2. Concepts in plain words

### 2.1 Three kinds of checks

| Kind | Question | Example | Added in |
| --- | --- | --- | --- |
| **Rules** | Is the value **possible**? | `price IS NULL OR …`, `share BETWEEN 0 AND 1` | Labs 2, 6 |
| **Seasonal statistics** | Is the value **normal for this hour of the week**? | Load at Tue 09:00 vs. the last 8 Tuesdays at 09:00 | This lab |
| **Multivariate ML** | Is the **combination** normal? | High solar output at night, or low price with no wind and high load | This lab |

### 2.2 Why "seasonal" and "robust"

- **Seasonal:** load at 03:00 is always lower than at 12:00. Comparing with the overall average would flag every night. So each value is compared with the **same hour of the week** in the previous 8 weeks.
- **Robust:** the usual mean and standard deviation are pulled around by a single outlier. The **median** and the **MAD** (median absolute deviation) aren't. The **robust z-score** is:

```
robust_z = 0.6745 × (x − median) / MAD
```

A robust z of ±4 means "far outside the normal range".

### 2.3 Isolation Forest

An **Isolation Forest** is an unsupervised ML model. It learns what *normal combinations* of many features look like (load, price, generation by source, wind speed, solar radiation, hour of day) and scores how easily a new hour can be "isolated" from the rest. Easy to isolate means unusual. It finds anomalies that no single-column rule can see. Here it's trained on the last 365 days and scores the last 7.

### 2.4 Completeness and physics rules

- **Completeness:** per series and German day, are all hours present? The expected count is **23, 24 or 25**, depending on daylight-saving time.
- **Physics:** solar generation > 100 MWh while solar radiation is ~0 is physically impossible, so it must be a data error.

### 2.5 Forecast drift

From Lab 7's `gold_forecast_accuracy`: if the model's MAE is worse than the baseline's on **3 consecutive days**, the forecast is degrading, which is a signal to retrain or investigate.

### 2.6 Severity design (the most important idea of this lab)

**Not every anomaly is an error.** Negative prices on windy Sundays, holidays, DST days and seasonal shifts are **real**. If every unusual value stopped the pipeline, people would soon ignore the alerts (**alert fatigue**). If nothing ever alerted, real problems would go unseen.

| Severity | Meaning | Effect | Produced by |
| --- | --- | --- | --- |
| **critical** | Almost certainly a **data problem** | Task fails → email | completeness (> 20 % hours missing), physics rule, forecast drift (load MAPE > 6 % on 2 days) |
| **warning** | **Unusual**, worth a look, often real | Logged, shown in the dashboard and the daily report | seasonal robust z, Isolation Forest, completeness > 5 %, model worse than baseline on 3 days |

### 2.7 The LLM daily report

A foundation model, called via `ai_query`, turns the day's counts and top findings into 3–5 sentences for a data engineer. **The anomaly table stays the source of truth**; the text is a convenience. LLMs can phrase things nicely, but they can also invent causes, so the prompt tells the model to describe, not speculate, and the code never fails the job on an LLM error.

---

## 3. Architecture

```
silver_energy_long ──────┐
gold_energy_weather ─────┼─► detect_anomalies ─► gold_dq_anomalies      (one row per finding)
gold_forecast_accuracy ──┘      │                gold_dq_daily_summary   (counts per check/severity)
                                │
                   critical → task fails → email
                                ▼
                         dq_summary_llm (run_if: ALL_DONE) ─► gold_dq_daily_summary_text (LLM, optional)
```

Daily job after this lab:

```
run_pipeline → quality_checks → apply_contracts → verify_contracts → score_forecast → forecast_accuracy
            → detect_anomalies → dq_summary_llm
```

---

## 4. Prerequisites

- Labs 0–7 completed (forecast and accuracy tables exist in prod)
- Golden rules: branch first, commit before experiments, local checks before push, job dependencies only via job environments, German dates via `from_utc_timestamp(…, 'Europe/Berlin')`, **release only after the PR is merged and staging is green**

---

## 5. Steps

### Step 01 — Generate the detectors

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c feat/ml-data-quality
```

**Claude Code prompt** (already includes the fixes for P1–P4):

```text
Read CLAUDE.md first. Add ML-assisted data quality. Do not deploy.
Lessons from Lab 7 apply: dependencies ONLY via job environments (no %pip/restartPython), German dates via
to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin')), tasks must not "succeed" with no output.

1. src/energy_lakehouse/anomalies.py (pandas, Spark-free, unit-tested). Each detector returns rows with:
   check_name, entity, ts_utc, value, expected, lower, upper, score, severity ('critical'|'warning'), reason.
   a) seasonal_robust_z: per entity and hour-of-week, median and MAD over the previous 8 weeks (excluding the
      current point). MAD floor = max(MAD, 0.05*|median|, 50). Flag only if |robust z| >= 4 AND
      |x - median| >= 25 % of |median|. Severity: WARNING ONLY. Applied to silver_energy_long for the last 7 days,
      excluding price. Reason shows z and relative deviation in %.
   b) multivariate_isolation_forest: sklearn IsolationForest (contamination=0.005, random_state=42) on
      gold_energy_weather_hourly (load, price, generation by source, wind speed, radiation, hour_local sin/cos
      computed from LOCAL time), trained on the last 365 days, scored on the last 7; warning only; reason lists the
      3 most deviating features.
   c) completeness: per series and German day for the last 7 complete days, expected hours 23/24/25 (DST)
      vs present non-null hours; critical > 20 % missing, warning > 5 %.
   d) physics_rules: solar_mwh > 100 while shortwave_radiation_solar_avg_wm2 < 1 → critical.
   e) forecast_drift: model mae > baseline_mae on 3 consecutive issue dates → warning; load mape > 6 % on
      2 consecutive dates → critical. Empty input → no rows, no error.
2. src/notebooks/20_detect_anomalies.py — params catalog, schema, inject_test_anomaly (job-level parameter,
   default "false"): run all detectors, MERGE into gold_dq_anomalies on (check_name, entity, ts_utc) with
   detected_at; write gold_dq_daily_summary. If inject_test_anomaly == "true": halve total_load in-memory for the
   newest complete hour with entity 'TEST:total_load'. Raise with a clear list if any non-TEST critical rows were
   found in this run.
3. src/notebooks/21_dq_summary_llm.py — param endpoint (empty = skip): short prompt from today's summary +
   top 10 anomalies; ask for 3–5 plain sentences; explain that warning = unusual-but-possibly-real and
   critical = likely data error; describe, don't speculate. Store text, model name, prompt length in
   gold_dq_daily_summary_text. On any error: warn and continue.
4. Contracts for the new gold tables (Lab 6 pattern).
5. resources/daily_refresh_job.yml: job-level parameter inject_test_anomaly (default "false") passed as
   {{job.parameters.inject_test_anomaly}}; task detect_anomalies after forecast_accuracy (environment "forecast"),
   max_retries 0; task dq_summary_llm after it with run_if ALL_DONE; job max_concurrent_runs 1, queue enabled.
6. tests/test_anomalies.py: a spike is flagged; a normal weekly pattern gives no rows; a gradual seasonal shift
   gives at most warnings; near-constant history with a small change is not flagged; DST days expect 23/25;
   completeness thresholds; solar-at-night critical; forecast_drift on 3 bad days, none on 2;
   IsolationForest flags an injected impossible combination; TEST: rows never cause a failure.
7. Update CLAUDE.md and the product docs.
List changed files and checkpoint commands.
```

---

### Step 02 — Local checks and "test the test"

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
git add -A ; git commit -m "wip: ML-assisted data quality"
```

Prove a test can fail: temporarily raise a threshold so that nothing can ever be flagged (e.g. `warn_z=1000`), **save**, then:

```powershell
git diff --stat
uv run pytest tests/test_anomalies.py -q     # expected: the spike test FAILS
git checkout -- src/energy_lakehouse/anomalies.py
uv run pytest -q                              # expected: all pass
```

📸 `lab8-step02-test-the-test.png`

---

### Step 03 — Run in dev

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run daily_refresh_job -t dev -p free
```

**Expected:** all eight tasks ✓, unless the detector finds critical issues. If `detect_anomalies` fails, **read its message before changing anything**: it lists the findings (Troubleshooting P1).

**Understand the job's result states:**

| State | Meaning |
| --- | --- |
| `SUCCESS` | All tasks succeeded |
| `SUCCESS_WITH_FAILURES` | At least one task failed, but a task with `run_if: ALL_DONE` (here `dq_summary_llm`) still ran. **Treat it as a failure.** |
| `UPSTREAM_FAILED` (per task) | Skipped because an earlier task failed. Look for the **first** failed task. |

**List all task states of a run** (use this whenever a run fails):

```powershell
$runId = (databricks jobs list-runs --job-id <job-id> --limit 1 -p free -o json | ConvertFrom-Json)[0].run_id
$run = databricks jobs get-run $runId -p free -o json | ConvertFrom-Json
$run.tasks | Select-Object task_key, @{n="result";e={$_.state.result_state}}, @{n="message";e={$_.state.state_message}} | Format-Table -AutoSize -Wrap
# error of one task:
$taskRunId = ($run.tasks | Where-Object task_key -eq "<task_key>" | Select-Object -Last 1).run_id
$out = databricks jobs get-run-output $taskRunId -p free -o json | ConvertFrom-Json
$out.error ; $out.error_trace | Select-Object -Last 1
```

---

### Step 04 — Look at the findings and explain them

```sql
SELECT check_name, severity, count(*) AS n
FROM workspace.energy_dev.gold_dq_anomalies
WHERE detected_at >= current_timestamp() - INTERVAL 2 HOURS
GROUP BY 1, 2 ORDER BY 2, 3 DESC;

SELECT check_name, entity, ts_utc, round(value) AS value, round(expected) AS expected,
       round(score, 1) AS score, severity, reason
FROM workspace.energy_dev.gold_dq_anomalies
WHERE detected_at >= current_timestamp() - INTERVAL 2 HOURS
ORDER BY severity, abs(score) DESC
LIMIT 20;
```

**My top findings (after the severity redesign):**

| Check | Entity | Time (UTC) | Value | Expected | Score | Severity |
| --- | --- | --- | --- | --- | --- | --- |
| seasonal_robust_z | natural_gas | Wed 21 Oct 11:00 | 11,004 | 2,487 | 43.8 (+342 %) | warning |
| seasonal_robust_z | natural_gas | Sun 18 Oct 08:00 | 6,500 | 1,482 | 36.2 (+339 %) | warning |
| seasonal_robust_z | natural_gas | Wed 21 Oct 10:00 | 11,104 | 2,596 | 35.8 (+328 %) | warning |
| seasonal_robust_z | natural_gas | Wed 21 Oct 12:00 | 10,638 | 2,538 | 24.3 (+319 %) | warning |
| seasonal_robust_z | natural_gas | Sat 17 Oct 12:00 | 3,745 | 1,413 | 22.3 (+165 %) | warning |

**Interpretation, the part that matters:** midday gas generation is 3–4× the late-summer level. The 8-week window reaches back to late August, when sunny middays were covered by solar and gas plants barely ran at noon. In late October, days are shorter and often cloudy, solar drops, and gas fills the gap, especially on a calm weekday like 21 October. **Real seasonal shift, not a data error.** The detector correctly says *"very unusual vs. recent weeks"*, and the severity correctly says *"not broken"*.

**Prove the reading with data:**

```sql
SELECT to_date(from_utc_timestamp(timestamp_utc, 'Europe/Berlin')) AS day,
       round(max(CASE WHEN name = 'natural_gas' THEN value END)) AS gas_noon,
       round(max(CASE WHEN name = 'solar'       THEN value END)) AS solar_noon
FROM workspace.energy_dev.silver_energy_long
WHERE hour(from_utc_timestamp(timestamp_utc, 'Europe/Berlin')) = 13
  AND timestamp_utc >= current_timestamp() - INTERVAL 70 DAYS
GROUP BY 1 ORDER BY 1;
```

**Expected:** solar at noon falls gradually over the weeks, and gas at noon rises gradually. Make it a line chart.

📸 `lab8-step04-seasonal-shift.png`

For your own top 5, write one sentence each: **real event or data problem?**

---

### Step 05 — Prove the alert path with an injected anomaly

**Make sure no other run of the job is active** (Jobs & Pipelines → Runs: nothing *Running* or *Pending*), then:

```powershell
databricks bundle run daily_refresh_job -t dev -p free --params inject_test_anomaly=true
```

```sql
SELECT entity, ts_utc, round(value) AS value, round(expected) AS expected, severity, reason
FROM workspace.energy_dev.gold_dq_anomalies
WHERE entity LIKE 'TEST:%' ORDER BY detected_at DESC LIMIT 1;
```

**Expected:** `TEST:total_load`, value ≈ half of `expected`, `warning` (seasonal z is warning-only), and the job ✓, because TEST rows never fail a run.

📸 `lab8-step05-injected.png`

---

### Step 06 — Forecast drift

```sql
SELECT * FROM workspace.energy_dev.gold_dq_anomalies WHERE check_name = 'forecast_drift';
```

**Expected right after Lab 7:** no rows, because the accuracy table is only starting to fill. A green task proves the detector handles empty input. From then on it watches every day.

---

### Step 07 — The LLM daily report

**Find an endpoint:** left sidebar → **Serving**. In late 2026, Databricks moved foundation-model serving to **Unity Gateway**; the main page may show *"No endpoints found"* with a banner. Click **"here"** in the banner (the legacy experience) to see the Databricks-hosted chat endpoints (e.g. GPT OSS 20B/120B, Qwen, DeepSeek). Get the **exact** name by clicking the row, or with:

```powershell
databricks serving-endpoints list -p free -o json | ConvertFrom-Json | Select-Object name
```

**Test it** (use the real name, not a placeholder):

```sql
SELECT ai_query('<exact-endpoint-name>', 'Reply with the single word: OK');
```

A smaller model (e.g. GPT OSS 20B) is enough for a short summary and uses less quota. Set the name as the default `endpoint` of `dq_summary_llm`, deploy, run once, then:

```sql
SELECT detection_date, model, summary_text
FROM workspace.energy_dev.gold_dq_daily_summary_text
ORDER BY detection_date DESC LIMIT 1;
```

**Expected:** 3–5 sentences summarising today's findings.

**My summary:**

> `<paste your real summary text here>`

**Read it critically.** If the text claims a cause that isn't in the data, that's the LLM inventing. The anomaly table is the source of truth.

📸 `lab8-step07-llm-summary.png`

---

### Step 08 — PR → staging → prod (in the right order!)

```powershell
git branch --show-current            # feat/ml-data-quality, NOT master
git status ; git log --oneline origin/master..HEAD
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
git add -A ; git commit -m "feat: ML-assisted data quality (severity design, injection test, LLM summary, job queueing)"
git push -u origin feat/ml-data-quality
gh pr create --fill --base master
```

Wait ~10 s → `gh pr checks --watch` → ✓ → `gh pr merge --squash --delete-branch`.

**Before releasing, prove the feature is on master and staging is green** (Troubleshooting P6):

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
gh pr view <pr-number> --json state,mergedAt                                     # MERGED
gh run list --workflow deploy-staging.yml --limit 1                              # ✓ for the merge
git --no-pager ls-tree -r --name-only origin/master | Select-String "anomalies"  # files present
gh release create v0.4.1 --target master --generate-notes --title "v0.4.1 - ML-assisted data quality"
```

Approve → `deploy-prod` ✓. Check **`[prod] daily_refresh_job` → Tasks** ends with `detect_anomalies → dq_summary_llm`. Then run the refresh **once**:

```powershell
gh workflow run daily-refresh.yml
```

Wait ~10 s, then `gh run watch` (it takes ~10–15 minutes now; the new tables appear only at the end).

```sql
SELECT check_name, severity, count(*) FROM workspace.energy_prod.gold_dq_anomalies GROUP BY 1, 2;
```

**My prod result:**

| check_name | severity | count |
| --- | --- | --- |
| seasonal_robust_z | warning | 83 |
| isolation_forest | warning | 1 |

**0 critical**, so the job runs green. 83 warnings over 7 days × 7 generation series is noticeable. In Lab 9 the dashboard shows only the **top findings per day**, and the thresholds can be tuned once a few weeks of history show which warnings people actually look at.

### Step 09 — Tag

```powershell
git fetch origin ; git tag lab8-complete origin/master ; git push origin lab8-complete
```

---

## 6. Checkpoint

1. `uv run pytest` passes, and you've seen a detector test fail on purpose.
2. Dev: findings in `gold_dq_anomalies`, top 5 explained (real event vs. data problem).
3. The seasonal-shift chart confirms the gas/solar interpretation.
4. The injected `TEST:total_load` row appears, and the job stays green.
5. The LLM summary exists (or the step is cleanly disabled, with the reason documented).
6. Prod: anomaly rows with **0 critical**; the job ends with `detect_anomalies → dq_summary_llm`.
7. The daily job has `max_concurrent_runs: 1` and queueing enabled.

---

## 7. Troubleshooting — problems I actually hit

### P1 — First run: 28 "critical" anomalies that were real

**Symptom:**

```
RuntimeError: 28 critical data-quality anomalies:
- seasonal_robust_z natural_gas 2026-10-21 11:00 UTC: robust z +43.8 vs same hour-of-week median 2487.09 (8 weeks)
- seasonal_robust_z natural_gas 2026-10-17 12:00 UTC: robust z +36.2 vs same hour-of-week median 1413.39 (8 weeks)
- ...
- seasonal_robust_z solar 2026-10-17 12:00 UTC: robust z -11.5 vs same hour-of-week median 34550.2 (8 weeks)
- seasonal_robust_z wind_onshore 2026-10-22 02:00 UTC: robust z +14.0 vs same hour-of-week median 12085 (8 weeks)
```

**Investigation:** 24 of 28 were natural gas at **midday**, plus solar much **lower** at midday, and wind much higher on one night. That's one story, the **autumn shift from solar to gas**, plus a real windy night (13 m/s). The seasonal-shift query (Step 04) confirmed a gradual trend, not a broken day. The extreme z-scores came from summer midday gas being almost constant, so the MAD was tiny.

**Cause:** not the data, the **design**: statistical "unusual" had been mapped to "critical".

**Fix:**
- seasonal z is **warning-only**
- a MAD floor (`max(MAD, 5 % of median, 50 MWh)`)
- flag only if the deviation is also **≥ 25 % relative**
- critical is reserved for completeness, physics and forecast drift

**Lesson:** statistical anomalies need context. Severity must distinguish *unusual* from *broken*, or alerts become noise.

### P2 — The detector ran twice ("2 attempts")

**Cause:** the task had a retry. A deliberate "critical findings" failure is a signal, not a transient error, so a retry only doubles compute.
**Fix:** `max_retries: 0` for `detect_anomalies`. Keep retries for tasks that call external services.

### P3 — "Run now with different parameters" didn't inject anything

**Symptom:** I typed `true` for `inject_test_anomaly` and ran; no `TEST:` row appeared.
**Cause:** the parameter was a **task parameter** (a notebook widget). The dialog and `--params` only override **job-level** parameters.
**Fix:** define `inject_test_anomaly` as a job parameter and pass it as `{{job.parameters.inject_test_anomaly}}`. Run with `databricks bundle run daily_refresh_job -t dev -p free --params inject_test_anomaly=true`.

### P4 — 7 failed tasks in one minute

**Symptom:**

```
"[dev rezaul_tutul] daily_refresh_job" TERMINATED SUCCESS_WITH_FAILURES The job run succeeded with 7 failed tasks
```

**Cause:** another run of the same job was **still running**. The pipeline allows one update at a time, so the second run's `run_pipeline` was refused, and everything after it was marked failed (upstream).
**Fix:** wait for active runs to finish. Permanently: `max_concurrent_runs: 1` plus `queue: {enabled: true}`, so a second run **waits** instead of failing.

### P5 — `ai_query`: `'<endpoint-name>' does not exist or is not accessible`

**Causes:**
1. I used the **placeholder** literally.
2. The Serving page showed *"No endpoints found"*, because foundation-model serving had moved to **Unity Gateway**.

**Fix:** open the **legacy view** (the "here" link in the banner), copy the exact endpoint name, test with `SELECT ai_query('<name>', 'Reply with OK')`.

### P6 — Released `v0.4.0` before the PR was merged

**Symptom:** `deploy-prod v0.4.0` ✓ and the daily refresh ✓, but:

```
[TABLE_OR_VIEW_NOT_FOUND] The table or view `workspace`.`energy_prod`.`gold_dq_anomalies` cannot be found.
```

**Investigation:** PR #7 had been created **16 minutes** before; `v0.4.0` was created **15 minutes** before. `--target master` tagged the **old** master, so prod deployed **Lab 7 again**: green, but without the new tasks.
**Fix:** merge first, wait for staging ✓, check the files are on master, then release `v0.4.1` (fix forward; don't move tags). Check:

```powershell
git --no-pager ls-tree -r --name-only v0.4.1 | Select-String "anomalies"
```

**Lesson:** before every release, check that the feature is **in master**, not just in a PR.

### P7 — "Table not found" while the run was still running

**Cause:** the new tables are created by `detect_anomalies` near the **end** of a ~10–15 minute job. `gh run watch` showed `* Run prod daily_refresh_job and wait`, still running.
**Fix:** wait for ✓, or follow the task graph in Databricks. **Ctrl+C** only stops *watching*; the run continues. Resume with `gh run watch <id>`.

### P8 — `LeakageError` in prod just after midnight before the DST change

**Symptom (first prod run with Lab 8, started 00:17 German time on Sat 24 Oct):**

```
LeakageError: lag 48 h reaches past the issue time 2026-10-23 22:17:00+00:00
```

`score_forecast` failed; `forecast_accuracy` and `detect_anomalies` were `UPSTREAM_FAILED`.

**My first guess (wrong):** a DST bug in the time handling (`AmbiguousTimeError`). **Reading the actual error** showed otherwise.

**Cause:** the German date was already 24 Oct, so the task forecast **Sun 25 Oct**, the **25-hour** clock-change day. Its last hour (23:00 local = 22:00 UTC) needs a 48 h lag reaching to **23 Oct 23:00 UTC**, which was *after* the issue time (22:17 UTC). The **leakage guard from Lab 7** correctly refused.
**How narrow this is:** on normal days, 48 h lags work for any run during the German day. Only a run in the **first hour after German midnight before the October DST change** hits it. The scheduled 05:17 UTC run is unaffected.
**Decision:** **no code change.** Weakening the guard (or using ≥ 49 h lags all year) would trade correctness for convenience. The issue window is documented: *"Scoring is designed for the daily 05:17 UTC run; manual runs between 00:00 and 01:00 German time before the October DST change are rejected by the leakage guard."* The next scheduled run produced the 25-hour Sunday forecast.

**Lesson:** a guard that blocks is not automatically a bug. Read the message, understand it, then decide.

### Other problems you may hit

| Problem | Cause | Fix |
| --- | --- | --- |
| Hundreds of warnings | Thresholds too sensitive for this data | Check counts per `check_name`, raise the relative-deviation minimum, or show only the top N in the dashboard |
| Isolation Forest flags every DST hour | Hour features computed in UTC | Use local time for `hour_local` sin/cos |
| Completeness critical for the newest day | SMARD hasn't published everything yet | Evaluate only **complete** days (exclude today and yesterday) |
| `forecast_drift` never fires | Accuracy table empty or join mismatch (UTC vs. local) | Check `gold_forecast_accuracy` rows and keys |
| Prod LLM summary missing | The SP can't query the endpoint | Read the `dq_summary_llm` output, grant the SP access to the endpoint |
| `SUCCESS_WITH_FAILURES` treated as success | The `run_if: ALL_DONE` task masks failures | The refresh workflow checks `result_state == SUCCESS` (Lab 4); always look for the first failed task |

---

## 8. What you learned

- The difference between **rules**, **seasonal statistics** and **multivariate ML** for data quality
- **Robust** statistics (median/MAD), and why seasonality must be part of "normal"
- **Isolation Forest** for unusual combinations
- **Completeness** with DST-aware expectations, and **physics rules**
- **Forecast-drift** monitoring as part of data quality
- **Severity design** and **alert fatigue**: unusual ≠ broken
- Using an **LLM** for reporting, safely (never as the source of truth, never failing the job)
- Job mechanics: retries, job- vs. task-level parameters, `run_if`, result states, concurrency and queueing
- **Release discipline:** merged + staging green before tagging
- Reading a guard's message before "fixing" it

**Interview-ready summary:** *"I added an ML-assisted data-quality layer to the energy lakehouse: seasonal robust z-scores per hour of week, an Isolation Forest over the multivariate generation/price/weather profile, DST-aware completeness checks, physics rules and forecast-drift monitoring, with a severity design that separates 'unusual' from 'broken'. The first run flagged 28 'critical' anomalies, which turned out to be the real autumn shift from solar to gas at midday; I redesigned severities instead of tuning them away. A foundation model writes a daily plain-language summary via ai_query. In prod the layer runs daily with zero critical findings and a ranked list of warnings."*

---

## 9. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-23/24 |
| OS | Windows 11 (local), ubuntu-latest (GitHub runners) |
| Databricks | Free Edition (serverless), one workspace for dev/staging/prod |
| LLM | `<endpoint name you used>` via `ai_query` |
| ML | scikit-learn IsolationForest |
| Prod result | seasonal_robust_z warning 83, isolation_forest warning 1, critical 0 |
| Releases | v0.4.0 (released before merge, Lab 7 content), v0.4.1 (Lab 8) |

**Next:** Lab 9 — AI/BI dashboard as code (market overview, merit order, forecast vs. actual, data-quality panel with the LLM summary) and a Genie space for natural-language questions.
