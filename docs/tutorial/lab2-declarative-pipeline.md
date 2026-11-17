# Lab 2 — From Raw Files to Analytics Tables: Auto Loader and Lakeflow Declarative Pipelines (Bronze → Silver → Gold)

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 1 — Extract SMARD data into a Unity Catalog Volume. Next: Lab 3 — Weather data and scheduled Lakeflow Jobs.

## 1. Goal

In Lab 1 you put raw JSON files from SMARD into a **landing zone** (a Unity Catalog Volume). Files alone can't be queried well. In this lab you build a **pipeline** that turns those files into clean, tested tables that analysts and dashboards can use.

By the end you will have:

- A **bronze** table filled by **Auto Loader**, which reads only new files on every run
- A **silver** table with one clean row per (series, hour), including **data quality rules** (expectations)
- Two **gold** tables with business metrics: hourly and daily load, price, generation mix and renewables share
- The whole pipeline defined as code, deployed with the bundle and run from your laptop
- Proof that the pipeline is **incremental**: it doesn't reprocess old files

**Time:** about 2–3 hours, including troubleshooting.

---

## 2. Concepts in plain words (read this first)

You don't need to master these before starting. Read them once, then come back when a step mentions them.

### The medallion architecture

A common way to organize a lakehouse into three layers:

| Layer | What it holds | Why |
| --- | --- | --- |
| **Bronze** | Raw data, as close to the source as possible, plus where it came from (file name, load time) | You can always replay later layers from here. Nothing is lost or changed. |
| **Silver** | Cleaned, typed, deduplicated data with quality rules applied | One trustworthy version of the facts |
| **Gold** | Aggregated, business-ready tables (e.g. "per hour", "per day") | What dashboards, analysts and ML models read |

### Auto Loader

A Databricks feature that watches a folder and reads **only files it hasn't seen before**. It remembers processed files in a **checkpoint**. You never write "which files are new?" logic yourself. In code it appears as `spark.readStream.format("cloudFiles")`.

### Lakeflow Spark Declarative Pipelines (SDP)

Formerly called Delta Live Tables (DLT). You **declare** which tables should exist and how each is computed from the previous one. Databricks works out the order, runs it, retries, tracks lineage and records quality metrics. You write a Python function per table and mark it with a decorator.

| Table type | Decorator | Behaviour | Used here for |
| --- | --- | --- | --- |
| **Streaming table** | `@dp.table` with a streaming read | Processes only new input since the last run | bronze |
| **Materialized view** | `@dp.materialized_view` | Result is kept up to date with its inputs. Databricks decides whether to recompute fully or incrementally. | silver, gold |

### Expectations

Data quality rules attached to a table. Each rule is a SQL condition plus an action:

| Decorator | When a row fails the rule |
| --- | --- |
| `@dp.expect` | Row is kept and counted as a warning |
| `@dp.expect_or_drop` | Row is removed and counted |
| `@dp.expect_or_fail` | The whole update stops with an error |

The pipeline UI shows how many rows passed or failed each rule on every run.

### Unity Catalog names

Every table has a three-part name: `catalog.schema.table`, e.g. `workspace.energy_dev.silver_energy_long`. The bundle sets catalog `workspace` and schema `energy_dev` for the dev target.

---

## 3. What we build

```
/Volumes/workspace/energy_dev/landing/smard/filter=<id>/resolution=hour/<ts>.json
   │
   │  Auto Loader (incremental, checkpointed)
   ▼
bronze_smard_raw         streaming table   1 row per file: raw series + file metadata
   │
   │  explode series → typed timestamps → join series names → deduplicate → expectations
   ▼
silver_energy_long       materialized view  1 row per (series, hour)
   │
   │  pivot series into columns + business metrics
   ▼
gold_energy_hourly       materialized view  1 row per hour
gold_energy_daily        materialized view  1 row per local day (Europe/Berlin)
```

**Why "long" in silver and "wide" in gold?**
- **Long format** (`series_name, timestamp, value`) is easy to clean and extend. Adding a new SMARD series needs no schema change.
- **Wide format** (`hour, total_load_mwh, price_eur_mwh, solar_mwh, ...`) is easy to query and chart. That's what dashboards want.

---

## 4. Prerequisites

- Lab 0 and Lab 1 completed
- Raw files in `/Volumes/workspace/energy_dev/landing/smard/` (64 files = 8 series × 8 weeks)
- VS Code open in `C:\dev\energy-lakehouse-de`, terminal open (**Ctrl+`**)

**Check before you start** (SQL Editor):

```sql
LIST '/Volumes/workspace/energy_dev/landing/smard/';
```

**Expected:** 8 folders named `filter=410`, `filter=4169`, and so on.

---

## 5. Steps

### Step 01 — Generate the pipeline code

Two options, as in earlier labs:

- **Option A (with Claude Code):** use the prompt below. It already includes the fixes for two problems we hit on the first attempt (see Troubleshooting T2 and T4), so you shouldn't run into them.
- **Option B (exact copy):** copy the files from my repository at tag `lab2-complete`:
  `https://github.com/<my-username>/energy-lakehouse-de/tree/lab2-complete`

#### Option A — Claude Code prompt

```text
Read CLAUDE.md first. Add a Lakeflow Spark Declarative Pipeline to this repo. Do not run deploy commands.

Input: raw SMARD JSON files in /Volumes/{catalog}/{schema}/landing/smard/filter={id}/resolution={res}/{ts}.json
Each file is ONE JSON object: {"meta_data": {...}, "series": [[ts_ms, value_or_null], ...]}.
Series config already exists in src/energy_lakehouse/smard_config.py.

Use the Python API `from pyspark import pipelines as dp` (fall back to `import dlt` only if needed).
Pipeline catalog/schema come from the pipeline settings; read the landing path from a pipeline
configuration key `landing_path`.

1. src/pipelines/bronze.py — streaming table bronze_smard_raw using Auto Loader
   (cloudFiles, format json, multiLine true, explicit schema: series ARRAY<ARRAY<DOUBLE>>, meta_data as STRING or struct,
   schema location managed by the pipeline). Add columns: filter_id (int, regex from _metadata.file_path),
   resolution (regex from path), chunk_ts_ms (from file name), source_file, file_modification_time, ingested_at.
2. src/pipelines/silver.py — materialized view silver_energy_long:
   explode series -> ts_ms, value; timestamp_utc = timestamp_millis(ts_ms);
   join series metadata (name, unit) from smard_config as a small static DataFrame;
   deduplicate on (filter_id, timestamp_utc) keeping the row from the newest file_modification_time.
   Expectations (each rule checks ONE thing and must be null-safe):
     expect_or_drop "value_published": value IS NOT NULL
     expect_or_fail "valid_timestamp": timestamp_utc IS NOT NULL
     expect "non_negative_energy": value IS NULL OR unit <> 'MWh' OR value >= 0   (warn only; prices may be negative)
3. src/pipelines/gold.py —
   gold_energy_hourly: hour_utc = date_trunc('hour', timestamp_utc); aggregate MWh series with SUM and
   EUR/MWh with AVG (so quarter-hour series also work); pivot to columns total_load_mwh, price_eur_mwh,
   solar_mwh, wind_onshore_mwh, wind_offshore_mwh, lignite_mwh, hard_coal_mwh, natural_gas_mwh;
   add hour_local (Europe/Berlin, TIMESTAMP_NTZ), renewables_mwh = solar + wind_onshore + wind_offshore,
   renewables_share = renewables_mwh / total_load_mwh (null-safe).
   gold_energy_daily: by local date (Europe/Berlin): sums of MWh columns, avg/min/max price,
   avg renewables_share, count of hours (should be 24, 23 or 25 on DST days).
   Every table with a TIMESTAMP_NTZ column needs
   table_properties={"delta.feature.timestampNtz": "supported"}.
   Add table comments and column comments.
4. resources/energy_pipeline.yml — pipeline "energy_pipeline": serverless: true, catalog ${var.catalog},
   schema ${var.schema}, libraries from src/pipelines/, configuration landing_path =
   /Volumes/${var.catalog}/${var.schema}/landing/smard, channel CURRENT, no clusters.
   Make sure src/energy_lakehouse is importable from the pipeline code.
5. Put pure helpers (path regexes, aggregation-rule-by-unit, pivot column names) in
   src/energy_lakehouse/transforms.py and unit-test them in tests/test_transforms.py without Spark.
6. Update CLAUDE.md (pipeline layout, table names, run commands).

At the end, list files changed and checkpoint commands.
```

**Expected result — new or changed files:**

```
energy-lakehouse-de/
├── resources/
│   └── energy_pipeline.yml            (new)  pipeline definition for the bundle
├── src/
│   ├── energy_lakehouse/
│   │   └── transforms.py              (new)  pure helper functions, unit-tested
│   └── pipelines/
│       ├── bronze.py                  (new)  Auto Loader → bronze_smard_raw
│       ├── silver.py                  (new)  silver_energy_long + expectations
│       └── gold.py                    (new)  gold_energy_hourly, gold_energy_daily
├── tests/
│   └── test_transforms.py             (new)
└── CLAUDE.md                          (updated)
```

#### What the code does and why (read the files alongside this)

**`bronze.py`**
- `spark.readStream.format("cloudFiles")` is Auto Loader. `cloudFiles.format = json` tells it the file type.
- `multiLine = true`: each SMARD file is one JSON object, possibly spread over several lines. Without this option Spark expects one JSON record per line and produces corrupt records.
- **Explicit schema** instead of schema inference. We know the structure, and a fixed schema makes the pipeline predictable. If SMARD ever changes its format, we want an error, not silently different columns.
- `_metadata.file_path` is a hidden column available on every file read. We extract `filter_id`, `resolution` and the chunk timestamp from the folder and file names, so every row knows exactly where it came from (**lineage**).
- `ingested_at = current_timestamp()` records when we loaded the row. That's useful for debugging and deduplication.

**`silver.py`**
- `explode(series)` turns one row with an array of ~168 `[ts, value]` pairs into ~168 rows.
- `timestamp_millis(ts_ms)`: SMARD sends milliseconds since 1970 (UTC). This converts them into a real timestamp type.
- Join with `smard_config` adds readable names (`solar`, `total_load`) and units (`MWh`, `EUR/MWh`).
- **Deduplication** keeps, per (series, timestamp), the row from the newest file. If a weekly chunk is downloaded again later with more hours filled in, the newer version wins.
- **Expectations:**
  - `value_published` (drop): SMARD's newest hours are `null` until published. They aren't errors, but they're not facts yet, so we drop them.
  - `valid_timestamp` (fail): a missing timestamp would mean our parsing is broken. Stopping the pipeline is safer than writing wrong data.
  - `non_negative_energy` (warn): generation and load in MWh shouldn't be negative. We only warn, because we want to see it, not lose data. Prices are excluded: negative electricity prices are real in Germany on sunny, windy days.

**`gold.py`**
- `date_trunc('hour', ...)` groups data into hours.
- **SUM for MWh, AVG for EUR/MWh:** if a series is quarter-hourly, four 15-minute energy values add up to one hour, but four 15-minute prices must be averaged. This rule makes gold correct for both resolutions.
- **Pivot:** turns rows per series into one column per series.
- `hour_local`: Germany's local time (Europe/Berlin), stored as `TIMESTAMP_NTZ` (no time zone). A wall-clock time like "14:00 in Berlin" has no time zone attached, and NTZ expresses exactly that.
- `renewables_share = (solar + wind_onshore + wind_offshore) / total_load`: a simple, explainable KPI.
- `gold_energy_daily` groups by **local** date, so a "day" means a German calendar day. On daylight-saving change days a day has 23 or 25 hours, and `hours_count` makes that visible.

**`energy_pipeline.yml`**
- `serverless: true`: required on Free Edition, and it means no cluster setup.
- `catalog` / `schema` come from bundle variables, so dev, staging and prod write to different schemas with the same code.
- `configuration.landing_path`: the input folder is a parameter, not hardcoded in Python.

📸 `lab2-step01-new-files.png`

---

### Step 02 — Run local quality checks

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

**Expected result:** all green.

**Why only helpers are tested locally:** pipeline code needs Spark and Databricks to run. That's why pure logic (regexes, aggregation rules, column names) lives in `transforms.py`, where plain `pytest` can test it in seconds without Spark. The pipeline itself is tested by running it (Steps 03–06).

---

### Step 03 — Deploy and run the pipeline

```powershell
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run energy_pipeline -t dev -p free
```

**What each command does:**
- `validate` checks the YAML and paths locally and against the workspace. Nothing is created yet.
- `deploy` uploads the code and creates or updates the pipeline definition in the workspace.
- `run` starts a pipeline **update** and streams its event log to your terminal.

**Expected result:** the terminal shows progress lines and ends with:

```
... flow_progress   INFO "Flow 'workspace.energy_dev.gold_energy_daily' has COMPLETED."
... update_progress INFO "Update 588b31 is COMPLETED."
Update ID: 588b31c4-...
```

The first run takes a few minutes, because serverless compute has to start.

Note: the CLI doesn't print a link to the pipeline. Open it with:

```powershell
databricks bundle open energy_pipeline -t dev -p free
```

Or in the workspace: **Jobs & Pipelines** → `[dev <your-name>] energy_pipeline`.

**Expected result in the UI:** a graph with the landing-folder source and four green nodes:
`bronze_smard_raw → silver_energy_long → gold_energy_hourly` and `gold_energy_daily`.

📸 `lab2-step03-pipeline-graph.png`

---

### Step 04 — Check data quality (expectations)

**Click path:** in the pipeline graph, click **silver_energy_long**. In the bottom panel, open the **Table metrics** tab. Look at the **Expectations** column.

(Older documentation calls this a "Data quality" tab. In the current UI the information lives in the Expectations column.)

**Expected result:**

| Field | Expected |
| --- | --- |
| Output records | ~10K |
| Expectations | `2 met \| 1 unmet` |
| Dropped records | a few hundred (e.g. 374) |

Click the **unmet** link to see the details:

| Name | Action | Failed records |
| --- | --- | --- |
| value_published | DROP | e.g. 374 (≈3.5%) |

The panel also shows the split, e.g. **Written 96.5% (10,378)** and **Dropped 3.5% (374)**.

**Why some rows are dropped:** SMARD publishes the newest hours with a delay. The latest week's file contains `null` for hours not yet available. Dropping them is intended: silver contains only published facts.

**About "Full recompute"** in the *Incrementalization* column: materialized views that deduplicate and join may be recomputed fully on each update. With ~10K rows that takes seconds, so it's fine. The layer that must be incremental is bronze, and Step 06 proves it is.

📸 `lab2-step04-expectations.png`

---

### Step 05 — Verify row counts and sanity-check the numbers

In **SQL Editor**:

```sql
SELECT 'bronze' AS layer, count(*) AS n FROM workspace.energy_dev.bronze_smard_raw
UNION ALL SELECT 'silver', count(*) FROM workspace.energy_dev.silver_energy_long
UNION ALL SELECT 'gold_hourly', count(*) FROM workspace.energy_dev.gold_energy_hourly
UNION ALL SELECT 'gold_daily', count(*) FROM workspace.energy_dev.gold_energy_daily;
```

**Expected result (8 weeks of data):**

| layer | n | Why |
| --- | --- | --- |
| bronze | 64 | 8 series × 8 weekly files, 1 row per file |
| silver | ~10,400 | 8 series × ~1,340 hours, minus dropped nulls |
| gold_hourly | ~1,340 | 1 row per hour across all series |
| gold_daily | ~56 | 1 row per day |

Exact numbers depend on the date you extracted. If price (4169) is quarter-hourly, silver is higher.

Now check that the numbers make physical sense:

```sql
SELECT hour_local, total_load_mwh, price_eur_mwh, round(renewables_share, 2) AS ren_share
FROM workspace.energy_dev.gold_energy_hourly
ORDER BY hour_local DESC
LIMIT 24;
```

**Expected result:**
- `total_load_mwh` roughly **40,000–75,000** per hour (Germany's electricity demand)
- `price_eur_mwh` typically **0–250**, occasionally **negative** around sunny midday
- `ren_share` between **0 and 1**, higher at midday (solar) and on windy days

**Red flags:** load around 160,000–300,000 (about 4× too high) means quarter-hour values were not summed or averaged correctly. A share above 1 means a unit or pivot mistake.

```sql
SELECT local_date, hours_count, round(avg_price_eur_mwh, 1) AS avg_price, round(avg_renewables_share, 2) AS ren
FROM workspace.energy_dev.gold_energy_daily
ORDER BY local_date DESC
LIMIT 10;
```

**Expected:** `hours_count = 24` for normal days, 23 or 25 on daylight-saving change days, and less than 24 for today or yesterday (not yet published).

(Column names can differ slightly if your generated code named them differently. Check with `DESCRIBE workspace.energy_dev.gold_energy_daily;`.)

📸 `lab2-step05-gold-query.png`

---

### Step 06 — Prove the pipeline is incremental

This step demonstrates the main reason for using Auto Loader.

#### 6a — Run again with no new files

```powershell
databricks bundle run energy_pipeline -t dev -p free
```

**Expected result:** the update completes. In the UI, for this update, `bronze_smard_raw` shows **0 output records**, and the bronze count from Step 05 is still **64**. Auto Loader's checkpoint knew all files were already processed.

#### 6b — Add new files and run again

Extract one more week (the extractor skips the 64 existing files and adds 8 new ones), then upload:

```powershell
uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 9
Get-ChildItem data\landing\smard -Recurse -File | Measure-Object
databricks fs cp -r data\landing\smard dbfs:/Volumes/workspace/energy_dev/landing/smard --overwrite -p free
```

**Expected result:**
- the extractor summary shows `written: 8, skipped: 64, failed: 0`
- `Count : 72`
- the copy finishes without errors

**Verify the upload** before running the pipeline:

```sql
LIST '/Volumes/workspace/energy_dev/landing/smard/filter=410/resolution=hour/';
```

**Expected:** 9 files. Also run `LIST '/Volumes/workspace/energy_dev/landing/smard/';` and confirm there is **no** nested `smard` folder inside.

Now run the pipeline:

```powershell
databricks bundle run energy_pipeline -t dev -p free
```

Verify:

```sql
SELECT count(*) AS rows,
       count(DISTINCT source_file) AS files,
       count(DISTINCT chunk_ts_ms) AS weeks
FROM workspace.energy_dev.bronze_smard_raw;
```

**Expected:** `rows = 72, files = 72, weeks = 9`. In the UI, this update's bronze output shows **8** records: only the new files were read.

**Why `--overwrite` doesn't cause duplicates:** overwriting the 64 existing files with identical content gives them a new modification time. Auto Loader by default ignores changes to files it has already processed, so they aren't loaded again. If you see 136 rows instead of 72, see Troubleshooting T6.

If the numbers don't match, use Troubleshooting T5 to find out where the files got lost.

📸 `lab2-step06-incremental.png`

---

### Step 07 — Commit, push, tag

```powershell
git add .
git commit -m "feat: Lakeflow declarative pipeline bronze/silver/gold with expectations"
git push
git tag lab2-complete
git push --tags
```

**Expected result:** CI is green in the GitHub **Actions** tab.

📸 `lab2-step07-ci-green.png`

---

## 6. Checkpoint

All of the following must be true:

1. The pipeline graph shows 4 green tables.
2. `silver_energy_long` expectations show `value_published` dropping only unpublished (null) rows and **0 failures** for `non_negative_energy`.
3. Gold sanity query: load in a realistic range, renewables share between 0 and 1.
4. A rerun without new files writes **0** rows to bronze.
5. After adding a week: bronze has `files = 72, weeks = 9`.
6. CI is green.

---

## 7. Troubleshooting

These are real problems from building this lab, with how we found the cause. The method matters more than the fix: **first find out why, then change code.**

### T1 — "No URL printed after `bundle run`"

**Symptom:** the terminal shows `Update ... is COMPLETED.` but no link.
**Cause:** for pipelines, the CLI prints the event log and update ID, not a URL.
**Fix:** `databricks bundle open energy_pipeline -t dev -p free`, or **Jobs & Pipelines** in the workspace.

### T2 — `DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT ... timestampNtz`

**Symptom:**
```
Update ... has failed while trying to update table 'workspace.energy_dev.gold_energy_hourly'.
[DELTA_FEATURES_REQUIRE_MANUAL_ENABLEMENT] Your table schema requires manually enablement
of the following table feature(s): timestampNtz.
```
**Cause:** the column `hour_local` has type `TIMESTAMP_NTZ` (timestamp without time zone). Functions like `convert_timezone()` or `to_timestamp_ntz()` produce this type. Delta Lake only allows it when the table explicitly enables the `timestampNtz` feature. Bronze and silver succeeded because they have no NTZ column.
**How to confirm:** open `src/pipelines/gold.py` and search for `ntz` or `convert_timezone`.
**Fix:** keep the type (it's semantically correct for local wall-clock time) and enable the feature on that table:
```python
@dp.materialized_view(
    name="gold_energy_hourly",
    table_properties={"delta.feature.timestampNtz": "supported"},  # needed for TIMESTAMP_NTZ columns
)
```
Do the same for any other table with an NTZ column. Then redeploy and run. If the table is still stuck, rebuild just that table:
```powershell
databricks bundle run energy_pipeline -t dev -p free --full-refresh gold_energy_hourly
```

### T3 — "I can't find the Data quality tab"

**Cause:** the UI changed. Quality results are in the **Table metrics** tab → **Expectations** column (e.g. `2 met | 1 unmet`). Click the unmet link for details per rule.

### T4 — A warning rule fails on exactly the rows that were already dropped

**Symptom:** silver showed `1 met | 2 unmet`:

| Name | Action | Failed records |
| --- | --- | --- |
| value_published | DROP | 374 |
| non_negative_energy | ALLOW | 350 |

**Investigation:** 374 − 350 = 24, exactly one day of hourly values. That suggested the 350 "negative" rows were actually the **null** rows of the 7 MWh series, while the 24 null price rows passed the rule.

**Cause:** all expectations are evaluated **independently on the same input rows**, before anything is dropped. For a null value, `unit <> 'MWh' OR value >= 0` becomes `false OR NULL` = `NULL`, which counts as a failure. For price rows, `unit <> 'MWh'` is `true`, so the rule passes. The rule was double-counting nulls.

**How to confirm (no code change yet):**
```sql
-- Real negative energy values in silver? Expected: no rows
SELECT name, count(*) AS n, min(value) AS min_value
FROM workspace.energy_dev.silver_energy_long
WHERE unit = 'MWh' AND value < 0
GROUP BY name;

-- Where are the nulls? Expected: MWh series sum to 350, price (4169) has 24
SELECT filter_id, count(*) AS null_values
FROM (SELECT filter_id, explode(series) AS p FROM workspace.energy_dev.bronze_smard_raw)
WHERE p[1] IS NULL
GROUP BY filter_id
ORDER BY filter_id;
```
**Fix:** make the rule null-safe, so each rule checks one thing only:
```python
@dp.expect("non_negative_energy", "value IS NULL OR unit <> 'MWh' OR value >= 0")
```
Redeploy and run. The result should be `2 met | 1 unmet` with 0 failures for `non_negative_energy`.

**Lesson:** write expectations so they're null-safe, and let one dedicated rule handle nulls.

### T5 — Incremental test: pipeline "COMPLETED" but bronze didn't grow

**Symptom:** after Step 06b the pipeline completes, but bronze still has 64 rows.
**Cause:** the files were lost at one of three points. Check each:

```powershell
# 1. Did the extractor write new files? Expected: Count 72
Get-ChildItem data\landing\smard -Recurse -File | Measure-Object
```
```sql
-- 2. Did the upload land in the right folder? Expected: 9 files, and no nested smard/ folder
LIST '/Volumes/workspace/energy_dev/landing/smard/filter=410/resolution=hour/';
LIST '/Volumes/workspace/energy_dev/landing/smard/';

-- 3. What's in bronze?
SELECT count(*) AS rows, count(DISTINCT source_file) AS files, count(DISTINCT chunk_ts_ms) AS weeks
FROM workspace.energy_dev.bronze_smard_raw;
```

| Local count | Volume (filter=410) | Bronze | Cause → fix |
| --- | --- | --- | --- |
| 64 | 8 | 64 | Extractor didn't add a week. Check the `--weeks` value and the extractor summary. |
| 72 | 8 | 64 | Upload skipped the new files. Rerun `fs cp` with `--overwrite` and check for errors. |
| 72 | 9, or nested `smard/smard` | 64 | Files are outside the path Auto Loader reads. Move them to `landing/smard/filter=.../resolution=hour/`. |
| 72 | 9 | 72 / 72 / 9 | It worked. The UI showed totals, not per-update counts. |

### T6 — Bronze has duplicates (e.g. 136 rows after adding a week)

**Cause:** the same files were loaded twice under different paths, typically after an accidental nested upload (`landing/smard/smard/...`). Auto Loader treats a different path as a new file.
**Fix:** delete the nested folder from the volume, then rebuild bronze and everything after it:
```powershell
databricks fs rm -r dbfs:/Volumes/workspace/energy_dev/landing/smard/smard -p free
databricks bundle run energy_pipeline -t dev -p free --full-refresh-all
```
`--full-refresh-all` clears the tables and the Auto Loader checkpoint and reprocesses all files. Only use it in dev.

### T7 — Gold values about 4× too high

**Cause:** a quarter-hour series was treated as hourly (values not summed per hour, or summed twice), or prices were summed instead of averaged.
**Fix:** check the SUM-for-MWh / AVG-for-EUR/MWh rule in `transforms.py` and its unit test.

### T8 — `ModuleNotFoundError: energy_lakehouse` in the pipeline

**Cause:** pipeline code can't see `src/`.
**Fix:** the pipeline code must add the bundle's `src` folder to `sys.path` before importing, or the package must be installed in the pipeline environment. Ask Claude Code to fix the import and redeploy.

### T9 — Corrupt records / all columns null in bronze

**Cause:** `multiLine` is missing, so Spark expects one JSON record per line.
**Fix:** set `.option("multiLine", "true")` in `bronze.py`, then rebuild with `--full-refresh-all` (dev only).

---

## 8. What you learned

- The **medallion architecture** and why each layer exists
- **Auto Loader**: incremental file ingestion with checkpoints, and how to prove it works
- **Lakeflow Spark Declarative Pipelines**: streaming tables vs. materialized views, defined as Python code
- **Expectations** for data quality, the difference between warn, drop and fail, and why rules must be **null-safe**
- **Lineage columns** (`source_file`, `ingested_at`) and **deduplication** with "newest file wins"
- **Long vs. wide** table design, and correct aggregation by unit (SUM energy, AVG price)
- Time zones: UTC storage, **local-time** reporting, `TIMESTAMP_NTZ`, and Delta table features
- A **debugging method**: compare numbers between stages before changing code

**Interview-ready summary:** *"I built an incremental medallion pipeline on Databricks with Auto Loader and Lakeflow Declarative Pipelines, deployed as code via Asset Bundles. Silver has null-safe expectations. I found and fixed a rule that double-counted nulls because expectations evaluate independently. Gold aggregates by unit so it handles mixed hourly and quarter-hourly series."*

---

## 9. Tested on

| Item | Version |
| --- | --- |
| Date | `<date you ran it>` |
| OS | Windows 11 |
| Databricks | Free Edition (serverless), pipeline channel CURRENT |
| Databricks CLI | `<output of databricks -v>` |
| uv | `<output of uv --version>` |
| Python | 3.12.x |
| Data | SMARD, 8 series, 8–9 weeks, hourly |

**Data attribution:** Bundesnetzagentur | SMARD.de, licensed CC BY 4.0.

**Next:** Lab 3 — Weather data (Open-Meteo) and a scheduled Lakeflow Job that runs extract → pipeline end to end.
