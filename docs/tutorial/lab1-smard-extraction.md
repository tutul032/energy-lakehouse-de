# Lab 1 — Extract German Energy Data (SMARD) into a Unity Catalog Volume

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 0 — Environment Setup. Next: Lab 2 — Auto Loader and Declarative Pipelines (bronze/silver/gold).

## 1. Goal

By the end of this lab you will have:

- A tested Python extractor that downloads hourly German electricity data from **SMARD.de** (Bundesnetzagentur): load, day-ahead price and generation by source
- A **landing zone** in a Unity Catalog **Volume**: `/Volumes/workspace/energy_dev/landing/smard/...`
- Raw JSON files stored unchanged, one file per series and weekly chunk, ready for Auto Loader in Lab 2
- A serverless **Databricks job**, deployed with the bundle, that runs the extraction
- An **idempotent** extractor: running it twice doesn't download or duplicate anything

**Architecture**

```
SMARD API ──(extract job / local CLI)──► UC Volume: landing/smard/filter=<id>/resolution=hour/<ts>.json
                                                │
                                                └──► Lab 2: Auto Loader → bronze → silver → gold
```

**Time:** about 60–90 minutes.

## 2. Prerequisites

- Lab 0 completed: tools installed, CLI profile `free` working, repo `energy-lakehouse-de` scaffolded and CI green
- VS Code open in `C:\dev\energy-lakehouse-de` with the integrated terminal (**Ctrl+`**)
- Optional: Claude Code (Step 02, Option A)

### About the data

SMARD is the electricity market data platform of the Bundesnetzagentur. Its public chart API needs no API key. Data is licensed **CC BY 4.0** (source: Bundesnetzagentur | SMARD.de). Credit the source in your README.

The API works in two calls:

| Call | URL pattern | Returns |
| --- | --- | --- |
| Index | `https://www.smard.de/app/chart_data/{filter}/{region}/index_{resolution}.json` | `{"timestamps": [ms, ...]}`, one timestamp per weekly chunk |
| Chunk | `https://www.smard.de/app/chart_data/{filter}/{region}/{filter}_{region}_{resolution}_{timestamp}.json` | `{"series": [[ts_ms, value], ...]}` |

Series used in this project (resolution `hour`):

| Filter ID | Name | Region | Unit |
| --- | --- | --- | --- |
| 410 | total_load | DE | MWh |
| 4169 | day_ahead_price | DE-LU | EUR/MWh |
| 4068 | solar | DE | MWh |
| 4067 | wind_onshore | DE | MWh |
| 1225 | wind_offshore | DE | MWh |
| 1223 | lignite | DE | MWh |
| 4069 | hard_coal | DE | MWh |
| 4071 | natural_gas | DE | MWh |

---

## 3. Steps

### Step 01 — Test outbound internet access from Databricks

Databricks Free Edition restricts outbound internet access to a limited set of trusted domains. Check whether your workspace can reach SMARD before building anything.

**Action:** In the workspace, click **+ New → Notebook**. Select **Serverless** compute and run:

```python
import requests
r = requests.get("https://www.smard.de/app/chart_data/410/DE/index_hour.json", timeout=20)
print(r.status_code, r.json()["timestamps"][-3:])
```

**Expected result:** `200 [1759701600000, 1760306400000, 1760911200000]`. Your numbers will differ: they are Unix timestamps in milliseconds.

**If you get a connection or timeout error**, outbound access is blocked. Choose one:
- **(a)** Verify your identity with LinkedIn in the workspace settings. This unlocks outbound internet access on Free Edition. Wait a few minutes, then rerun the cell.
- **(b)** Keep going. You will run the extractor on your laptop and upload the files (Step 06b).

Delete the test notebook afterwards.

📸 `lab1-step01-outbound-test.png`

---

### Step 02 — Add the extractor code

Two options, as in Lab 0.

- **Option A (with Claude Code):** generate the code with the prompt below. The output can differ slightly between runs, but the structure, tests and checkpoints are the same.
- **Option B (exact copy):** copy the files from my repository at tag `lab1-complete`:
  `https://github.com/<my-username>/energy-lakehouse-de/tree/lab1-complete`

#### Option A — Claude Code prompt

```text
Read CLAUDE.md first. Add a SMARD extractor to this repo. Do not run deploy commands.

SMARD API (public, no auth), base https://www.smard.de/app/chart_data:
- Index: /{filter}/{region}/index_{resolution}.json -> {"timestamps": [ms, ...]} (one per weekly chunk)
- Chunk: /{filter}/{region}/{filter}_{region}_{resolution}_{timestamp}.json -> {"series": [[ts_ms, value_or_null], ...]}

Series to extract (resolution "hour"):
  410 total_load (DE), 4169 day_ahead_price (DE-LU), 4068 solar (DE), 4067 wind_onshore (DE),
  1225 wind_offshore (DE), 1223 lignite (DE), 4069 hard_coal (DE), 4071 natural_gas (DE)
Put this mapping in src/energy_lakehouse/smard_config.py as a list of dataclasses (filter_id, name, region, unit).

1. src/energy_lakehouse/smard.py — pure, Spark-free code using requests:
   - index_url(), chunk_url() builders
   - fetch_index(session, series, resolution) -> list[int]
   - select_timestamps(timestamps, n_latest) -> last n chunks
   - output_path(base_dir, series, resolution, ts) -> f"{base_dir}/smard/filter={id}/resolution={res}/{ts}.json"
   - extract(base_dir, series_list, resolution, n_latest, overwrite=False): downloads each chunk,
     writes the raw JSON unchanged plus nothing else, SKIPS files that already exist (idempotent),
     retries 3x with backoff, 0.2 s pause between requests, logs one line per file, returns a summary dict
     {written, skipped, failed}.
   Only plain Python file I/O (works locally and on /Volumes paths in serverless).
2. src/energy_lakehouse/extract_local.py — CLI: `uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 8`.
3. src/notebooks/01_extract_smard.py — Databricks notebook with widgets catalog, schema, weeks (default 8).
   Runs CREATE SCHEMA IF NOT EXISTS and CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.landing,
   then calls extract() with base_dir /Volumes/{catalog}/{schema}/landing and prints the summary.
   Make src/ importable (sys.path) so the notebook can import energy_lakehouse.
4. resources/extract_smard_job.yml — serverless job "extract_smard_job" running that notebook with
   catalog/schema from bundle variables and weeks=8. Declare requests in the job environment if needed.
5. tests/test_smard.py — offline tests only (mock HTTP): URL builders, select_timestamps,
   output_path, idempotent skip, retry on failure. Put a small sample chunk JSON in tests/fixtures/.
6. Add requests to pyproject dependencies; add data/ to .gitignore.
7. Update CLAUDE.md with the new module and commands.

At the end, list files changed and checkpoint commands.
```

**Expected result:** these files are new or changed:

```
energy-lakehouse-de/
├── resources/
│   └── extract_smard_job.yml          (new)
├── src/
│   ├── energy_lakehouse/
│   │   ├── smard.py                   (new)
│   │   ├── smard_config.py            (new)
│   │   └── extract_local.py           (new)
│   └── notebooks/
│       └── 01_extract_smard.py        (new)
├── tests/
│   ├── fixtures/                      (new, sample chunk JSON)
│   └── test_smard.py                  (new)
├── .gitignore                         (data/ added)
├── CLAUDE.md                          (updated)
└── pyproject.toml                     (requests added)
```

**Design choices worth noting (and explaining in interviews):**
- **Raw data stays raw.** The extractor writes the API response unchanged. All parsing happens later in the pipeline, so it can be replayed if the logic changes.
- **Spark-free extractor.** Plain Python is easy to unit-test offline and runs both on your laptop and on Databricks, because `/Volumes/...` paths behave like normal folders on serverless.
- **Idempotency.** Existing files are skipped, so the job can be retried or scheduled safely.
- **Hive-style folders** (`filter=410/resolution=hour/`) let Auto Loader turn folder names into columns in Lab 2.

📸 `lab1-step02-new-files.png`

---

### Step 03 — Run local quality checks

```powershell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

**Expected result:**

| Command | Expected output |
| --- | --- |
| `uv sync` | installs `requests`, no errors |
| `ruff check` | `All checks passed!` |
| `ruff format --check` | `N files already formatted` |
| `pytest` | all tests passed, including the new `test_smard.py` tests. These tests don't need internet. |

📸 `lab1-step03-pytest.png`

---

### Step 04 — Smoke test against the real API (1 week, local)

```powershell
uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 1
Get-ChildItem data\landing\smard -Recurse -File | Measure-Object
```

**Expected result:** the summary shows `written: 8, skipped: 0, failed: 0`, and `Count : 8` (one file per series).

Open one file in VS Code, for example `data\landing\smard\filter=410\resolution=hour\<ts>.json`. It contains `{"meta_data": ..., "series": [[1760306400000, 52345.25], ...]}`, about 168 rows per week. The last values can be `null` when SMARD hasn't published them yet. That's normal.

📸 `lab1-step04-local-files.png`

---

### Step 05 — Prove idempotency

Run the same command again:

```powershell
uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 1
```

**Expected result:** `written: 0, skipped: 8, failed: 0`. Nothing was downloaded twice.

---

### Step 06a — Deploy and run on Databricks (outbound access works)

```powershell
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run extract_smard_job -t dev -p free
```

**Expected result:**
- `validate`: `Validation OK!`
- `deploy`: `Deployment complete!`
- `run`: prints a run URL and ends with `TERMINATED SUCCESS`. The notebook output shows a summary like `written: 64, skipped: 0, failed: 0` (8 series × 8 weeks).

📸 `lab1-step06-job-run.png`

### Step 06b — Fallback: extract locally and upload (outbound access blocked)

First create the volume in **SQL Editor**:

```sql
CREATE SCHEMA IF NOT EXISTS workspace.energy_dev;
CREATE VOLUME IF NOT EXISTS workspace.energy_dev.landing;
```

Then in PowerShell:

```powershell
uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 8
databricks fs cp -r data\landing\smard dbfs:/Volumes/workspace/energy_dev/landing/smard -p free
```

**Expected result:** the copy finishes without errors and uploads 64 files.

---

### Step 07 — Check the landing zone

```powershell
databricks fs ls dbfs:/Volumes/workspace/energy_dev/landing/smard -p free
```

**Expected result:** eight folders:

```
filter=1223
filter=1225
filter=4067
filter=4068
filter=4069
filter=4071
filter=410
filter=4169
```

In the UI: **Catalog → workspace → energy_dev → Volumes → landing → smard**.

📸 `lab1-step07-volume-browser.png`

---

### Step 08 — Commit, push, tag

```powershell
git add .
git commit -m "feat: SMARD extractor to UC volume landing zone"
git push
git tag lab1-complete
git push --tags
```

**Expected result:** CI is green in the GitHub **Actions** tab. `data/` is **not** in the commit, because it's in `.gitignore`.

📸 `lab1-step08-ci-green.png`

---

## 4. Checkpoint

Run this in **SQL Editor**. It reads the raw files directly, before any pipeline exists:

```sql
SELECT
  regexp_extract(_metadata.file_path, 'filter=([0-9]+)', 1) AS filter_id,
  count(*)                                                  AS files
FROM read_files(
  '/Volumes/workspace/energy_dev/landing/smard/',
  format => 'json'
)
GROUP BY filter_id
ORDER BY filter_id;
```

**Expected result:** 8 rows, one per filter ID, with `files = 8` each (if you extracted 8 weeks).

All of the following must be true:
1. The checkpoint query returns 8 filter IDs.
2. Running the extractor a second time reports `written: 0`.
3. `uv run pytest` passes and CI is green.

📸 `lab1-checkpoint-read-files.png`

---

## 5. Troubleshooting

| Problem | Cause | Fix |
| --- | --- | --- |
| Step 01: `ConnectionError` / timeout in the notebook | Free Edition blocks outbound internet by default | Verify with LinkedIn in workspace settings, or use Step 06b |
| `day_ahead_price` (4169) fails or is empty at `hour` | SMARD may publish this series only at quarter-hour resolution | Set `resolution="quarterhour"` for filter 4169 in `smard_config.py` and rerun |
| HTTP 404 for one series | Wrong filter/region combination, e.g. price with `DE` instead of `DE-LU` | Check the region in `smard_config.py` against the table above |
| Last values in a file are `null` | SMARD hasn't published the latest hours yet | Expected. Nulls are handled in the silver layer (Lab 2) |
| `ModuleNotFoundError: energy_lakehouse` in the job | Notebook can't see `src/` | The notebook must add the bundle's `src` folder to `sys.path` before importing |
| `fs cp` error: volume does not exist | Volume not created yet | Run the `CREATE VOLUME` SQL in Step 06b first |
| Job run shows `[dev <name>] extract_smard_job` | `mode: development` prefixes resource names | Expected behavior |
| CI fails on `ruff format --check` | Generated files not formatted | `uv run ruff format .`, commit, push |

---

## 6. What you learned

- How to read a real public **REST API** with a two-step index/chunk pattern
- Designing a **landing zone** in a Unity Catalog **Volume** and why raw data stays unchanged
- Writing an **idempotent, retrying extractor** as plain, unit-testable Python
- **Offline tests** with mocked HTTP and fixtures, so CI never depends on an external API
- Deploying and running a second **serverless job** with the bundle
- Inspecting raw files with `read_files` and the `_metadata` column, before any pipeline exists
- Working around **Free Edition network limits** with a local-extract-and-upload fallback

---

## 7. Tested on

| Item | Version |
| --- | --- |
| Date | `<date you ran it>` |
| OS | Windows 11 |
| Databricks | Free Edition (serverless) |
| Outbound access | `<direct from Databricks / local + upload>` |
| Databricks CLI | `<output of databricks -v>` |
| uv | `<output of uv --version>` |
| Python | 3.12.x |
| requests | `<uv pip show requests>` |

**Data attribution:** Bundesnetzagentur | SMARD.de, licensed CC BY 4.0.

**Next:** Lab 2 — Auto Loader and Lakeflow Spark Declarative Pipelines: bronze, silver with expectations, gold.
