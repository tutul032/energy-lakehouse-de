# CLAUDE.md — energy-lakehouse-de

Portfolio project: German energy data engineering on Databricks.

## Context
- **Data (planned):** SMARD.de (load, generation, day-ahead prices) + Open-Meteo weather.
- **Components (planned):** Auto Loader → Lakeflow Spark Declarative Pipelines
  (bronze/silver/gold with expectations) → Lakeflow Jobs → AI/BI dashboard + Genie.
- **Environment:** Databricks **Free Edition** — serverless only, single workspace.
  - CLI profile: `free`
  - Unity Catalog catalog: `workspace`
- **Dev machine:** Windows 11, PowerShell, uv, Python 3.12.

## Folder layout
```
databricks.yml              # bundle root: variables + targets (dev/staging/prod)
resources/*.yml             # bundle resources (jobs, pipelines, dashboards)
src/energy_lakehouse/       # pure, unit-tested Python package
  utils.py                  #   table_name() helper
  smard_config.py           #   SMARD series list (filter_id, name, region, unit)
  http_utils.py             #   shared get_with_retry() (3 tries, backoff) + write_bytes()
  smard.py                  #   SMARD extractor: URL builders, fetch_index, extract() (requests only)
  weather_config.py         #   Open-Meteo locations (location_id, name, lat, lon, role) + variables
  weather.py                #   Open-Meteo extractor: build_url, fetch, output_path, extract()
  extract_local.py          #   CLI for local runs: --source {smard,weather,all}
  transforms.py             #   pipeline helpers: path regexes, agg rules, gold columns,
                            #   weather raw schema + role aggregation mapping
src/pipelines/              # Lakeflow Spark Declarative Pipeline sources (energy_pipeline)
  bronze.py                 #   bronze_smard_raw   (streaming table, Auto Loader)
  silver.py                 #   silver_energy_long (MV, exploded + deduped + expectations)
  gold.py                   #   gold_energy_hourly, gold_energy_daily (MVs, wide)
  bronze_weather.py         #   bronze_weather_raw (streaming table, Auto Loader)
  silver_weather.py         #   silver_weather_hourly (MV, arrays_zip + explode + dedupe)
  gold_weather.py           #   gold_energy_weather_hourly (MV, energy LEFT JOIN weather by role)
src/notebooks/              # Databricks notebooks (source format .py)
scripts/sync_landing.ps1    # local landing -> UC volume (fs cp per source, $LASTEXITCODE checks)
tests/                      # pytest tests for src/energy_lakehouse (offline; fixtures/ for samples)
data/                       # local landing output (gitignored)
docs/adr/                   # architecture decision records (NNNN-title.md)
docs/tutorial/              # step-by-step labs (labN-*.md)
docs/img/                   # screenshots: labN-stepNN-name.png
.github/workflows/ci.yml    # lint + test (no deploy yet)
```

## Commands (PowerShell)
```powershell
uv sync                                    # create .venv, install dev deps
uv run ruff check .                        # lint
uv run ruff format .                       # format (CI runs --check)
uv run pytest                              # unit tests (or: uv run python -m pytest)
uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 8 --past-days 63  # both
uv run python -m energy_lakehouse.extract_local --source weather --past-days 63               # weather only
.\scripts\sync_landing.ps1                 # upload data/landing/{smard,weather} to the volume + verify
.\scripts\sync_landing.ps1 -Schema energy_staging -Sources weather -Overwrite

databricks bundle validate -t dev -p free
databricks bundle deploy   -t dev -p free
databricks bundle run hello_job -t dev -p free
databricks bundle run extract_smard_job -t dev -p free
databricks bundle run energy_pipeline -t dev -p free                   # incremental update
databricks bundle run energy_pipeline -t dev -p free --full-refresh-all # rebuild all tables
databricks bundle destroy  -t dev -p free  # tear down dev resources
```
Targets: `dev` (default, development mode, schema `energy_dev`), `staging` (`energy_staging`),
`prod` (production mode, schema `energy_prod`, root `/Workspace/Shared/.bundle/<name>/<target>`).

## Conventions
- **Serverless only.** Never add `new_cluster`, `job_clusters`, `existing_cluster_id` or cluster
  policies. Jobs omit compute config; pipelines use `serverless: true`.
- **All tables live in catalog `workspace`**, schema per target via `${var.catalog}` /
  `${var.schema}`. Never hardcode a schema in notebooks/SQL; pass it as a job/pipeline parameter.
- Build table names with `energy_lakehouse.utils.table_name()` (backtick-quoted names).
- **No hardcoded hosts, tokens or secrets.** The host comes from the CLI profile; secrets go in
  Databricks secret scopes (later: GitHub OIDC for CI deploys). `.env` is gitignored.
- **PowerShell-compatible commands** in docs and instructions: no bash-only syntax
  (`&&` in PS 5.1, `export`, `\` line continuations).
- **Small, pure, testable functions in `src/energy_lakehouse/`**; notebooks stay thin and call
  them. Every new function gets a pytest test.
- **SMARD landing:** raw chunk JSON is written unchanged to
  `<base>/smard/filter=<id>/resolution=<res>/<ts>.json` (local `data/landing`, or
  `/Volumes/<catalog>/<schema>/landing` via `01_extract_smard`). Existing files are skipped;
  pass `--overwrite` / `overwrite=True` to refresh. Exception: the `refresh_latest` newest chunks
  per series (default 2, `--refresh-latest`) are always re-downloaded so late-published hours get
  filled; bronze sets `cloudFiles.allowOverwrites` so Auto Loader re-ingests them. Tests never hit
  the network.
- **Weather landing:** Open-Meteo `/v1/forecast` (no key), hourly `temperature_2m`,
  `wind_speed_10m`, `wind_speed_100m`, `shortwave_radiation`, `cloud_cover`; UTC (`timezone=GMT`),
  `timeformat=unixtime`, wind in m/s, `past_days` 0–92 (default 63) + `forecast_days=1`. Raw JSON
  written unchanged to `<base>/weather/location=<id>/run_date=<yyyy-mm-dd>.json` (run date in UTC;
  local or via `02_extract_weather`). Existing files skipped unless `--overwrite`. Consecutive runs
  overlap; `silver_weather_hourly` dedupes them. Not yet wired into a job (run the notebook).
- **Pipeline (`resources/energy_pipeline.yml`):** serverless, channel CURRENT, catalog/schema
  from bundle vars, sources globbed from `src/pipelines/**`. Use `from pyspark import pipelines
  as dp`. `root_path: ../src` puts `energy_lakehouse` on `sys.path` (fallback: config key
  `bundle.sourcePath`). Landing paths come from config keys `landing_path` (SMARD) and
  `weather_landing_path`; session time zone is
  UTC, local time is derived explicitly with `Europe/Berlin`. Keep Spark-free logic in
  `transforms.py`; pipeline files only turn it into Spark expressions.
- **Tables** (unqualified in pipeline code, resolved to `${var.catalog}.${var.schema}`):
  `bronze_smard_raw` (one row per file) → `silver_energy_long` (one row per `filter_id`,
  `timestamp_utc`; newest `file_modification_time` wins; expectations `value_published` drop,
  `valid_timestamp` fail, `non_negative_energy` warn) → `gold_energy_hourly` (MWh summed,
  EUR/MWh averaged per UTC hour) → `gold_energy_daily` (per Berlin calendar day, `hour_count`
  23/24/25). Assumes one resolution per filter in the landing zone.
- **Weather tables:** `bronze_weather_raw` (one row per file; `location_id`, `run_date` from the
  path; explicit schema from `transforms.weather_raw_schema()`) → `silver_weather_hourly` (one row
  per `location_id`, `timestamp_utc`; hourly arrays zipped + exploded; newest
  `file_modification_time` wins; forecast hours > now dropped; expectations `valid_timestamp`
  fail, `has_measurements` drop, `plausible_temperature` / `plausible_wind` warn) →
  `gold_energy_weather_hourly` (`gold_energy_hourly` LEFT JOIN weather per `hour_utc`; columns
  from `transforms.WEATHER_ROLE_AGGS`: role → variable → avg). Gold column lists with comments
  live in `transforms.py` (`GOLD_HOURLY_COLUMNS`, `gold_weather_columns()`); TIMESTAMP_NTZ
  columns need `table_properties={"delta.feature.timestampNtz": "supported"}`.
- Ruff line length 100; CI must pass `ruff check`, `ruff format --check`, `pytest`.
- Do not run `databricks bundle deploy/run/destroy` on the user's behalf — the user runs
  checkpoints.
- Tutorial labs follow the template: Goal, Prerequisites, Steps (action → command → expected
  result → screenshot `labN-stepNN-name.png`), Checkpoint, Troubleshooting, What you learned,
  Tested on.
