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
databricks.yml              # bundle root: variables, whl artifact (energy_lakehouse), targets
Dockerfile                  # extractor image (multi-stage: uv builds wheel; python slim, user app)
.dockerignore               # build context = pyproject.toml, uv.lock, README.md, package, contracts
.gitattributes              # *.sh eol=lf (scripts run on Linux: CI, dev container bind mount)
.claude/settings.json       # shared Claude Code settings: deny terraform apply/destroy/state/import
.devcontainer/              # VS Code dev container (see .devcontainer/README.md)
  devcontainer.json         #   build Dockerfile + features (major tags), remoteUser vscode, venv outside workspace
  Dockerfile                #   FROM python base image (major tag), removes the broken Yarn apt source
  post-create.sh            #   pinned uv (pipx), uv sync --locked, safe.directory (no hook install)
  features/databricks-cli/  #   local feature: official setup-cli install script at a pinned version
terraform/                  # UC platform (ADR 0010): schemas, landing volume, SPs, group, grants
  versions.tf               #   Terraform ~> 1.16.6, databricks provider 1.139.1 (+ .terraform.lock.hcl)
  providers.tf              #   host = var.databricks_host; auth from the environment only
  variables.tf, schemas.tf, volumes.tf, identities.tf, grants.tf
  imports.tf                #   import blocks for the existing objects (zero-diff adoption)
  terraform.tfvars.example  #   copy to terraform.tfvars (gitignored)
resources/*.yml             # bundle resources (jobs, pipelines, dashboards)
  energy_market_de.dashboard.yml # AI/BI dashboard resource (file_path overridden per target)
src/energy_lakehouse/       # pure, unit-tested Python package (built as a wheel, see "Wheel")
  utils.py                  #   table_name() helper
  smard_config.py           #   SMARD series list (filter_id, name, region, unit)
  http_utils.py             #   shared get_with_retry() (3 tries, backoff) + write_bytes()
  smard.py                  #   SMARD extractor: URL builders, fetch_index, extract() (requests only)
  weather_config.py         #   Open-Meteo locations (location_id, name, lat, lon, role) + variables
  weather.py                #   Open-Meteo extractor: daily (build_url, output_path, extract) and
                            #   archive (build_archive_url, archive_output_path, extract_archive)
  extract_local.py          #   CLI for local runs: --source {smard,weather,all},
                            #   weather --mode {daily,archive} --start/--end
  transforms.py             #   pipeline helpers: path regexes + parse, agg rules, gold columns,
                            #   weather raw schema + role aggregation mapping, forecast columns,
                            #   CONTRACT_TABLE_COLUMNS / CONTRACT_TABLE_COMMENTS
  forecast.py               #   day-ahead forecast (pandas): features (no leakage), seasonal naive,
                            #   metrics, time_split, forecast/accuracy SQL
  quality.py                #   daily-refresh quality checks (thresholds, CheckResult, raise)
  anomalies.py              #   ML-assisted DQ (pandas): seasonal robust z, IsolationForest,
                            #   completeness, physics rules, forecast drift; summary/MERGE SQL, LLM prompt
  contracts.py              #   data contracts: pydantic models, YAML loader, SQL renderers,
                            #   schema/quality/freshness checks, product-page markdown
  dashboard.py              #   per-target dashboard build: TARGET_SCHEMAS, build_dashboard (stdlib)
src/energy_market_de.lvdash.json # dashboard source, exported from dev (workspace.energy_dev.<table>)
contracts/                  # data contracts, one <table>.yaml each (gold incl. gold_dq_* + silver_weather_forecast_hourly)
src/pipelines/              # Lakeflow Spark Declarative Pipeline sources (energy_pipeline)
  bronze.py                 #   bronze_smard_raw   (streaming table, Auto Loader)
  silver.py                 #   silver_energy_long (MV, exploded + deduped + expectations)
  gold.py                   #   gold_energy_hourly, gold_energy_daily (MVs, wide)
  bronze_weather.py         #   bronze_weather_raw (streaming table, Auto Loader)
  silver_weather.py         #   silver_weather_hourly (MV, arrays_zip + explode + dedupe)
  gold_weather.py           #   gold_energy_weather_hourly (MV, energy LEFT JOIN weather by role)
  silver_weather_forecast.py #  silver_weather_forecast_hourly (MV, newest run per location, future hours)
  gold_weather_forecast.py  #   gold_weather_forecast_hourly (MV, forecast by role, one row per hour)
src/notebooks/              # Databricks notebooks (source format .py)
  03_quality_checks.py      #   quality_checks task of daily_refresh_job (uses quality.py)
  04_apply_contracts.py     #   apply_contracts task: comments/tags on drift + grants
  05_verify_contracts.py    #   verify_contracts task: schema, quality rules, freshness SLA
  10_train_forecast.py      #   train_forecast_job task per target: HGB model vs baseline, MLflow, UC
  11_score_forecast.py      #   score_forecast task: champion models -> gold_forecast_hourly (MERGE)
  12_forecast_accuracy.py   #   forecast_accuracy task: gold_forecast_accuracy (INSERT OVERWRITE)
  20_detect_anomalies.py    #   detect_anomalies task: gold_dq_anomalies (MERGE) + gold_dq_daily_summary
  21_dq_summary_llm.py      #   dq_summary_llm task: optional ai_query summary -> gold_dq_daily_summary_text
scripts/sync_landing.ps1    # local landing -> shared UC volume (fs cp per source, $LASTEXITCODE checks)
scripts/render_contract_docs.py # contracts/*.yaml -> docs/data-products/<table>.md (--check)
scripts/build_dashboard.py  # src dashboard -> build/energy_market_de.<target>.lvdash.json (--target)
build/                      # built dashboard files (gitignored)
dist/                       # built energy_lakehouse wheel (uv build --wheel; gitignored)
scripts/sync_landing.sh     # bash: extract all (SKIP_EXTRACT=1: skip) + fs cp -r --overwrite + verify
tests/                      # pytest tests for src/energy_lakehouse (offline; fixtures/ for samples)
data/                       # local landing output (gitignored)
docs/adr/                   # architecture decision records (NNNN-title.md)
docs/tutorial/              # step-by-step labs (labN-*.md)
docs/img/                   # screenshots: labN-stepNN-name.png
docs/data-products/         # generated data product pages (one per contract; do not hand-edit)
.github/workflows/ci.yml    # PR + push master (+ workflow_call): job ci (lint, test, bundle validate
                            #   staging/prod) + job docker (hadolint, build, smoke extract, Trivy)
                            #   + job devcontainer (devcontainers/ci: build, uv run pytest -q inside)
                            #   + job terraform (fmt -check, init -backend=false, validate, tflint)
.github/workflows/deploy-staging.yml # push master: ci gate -> deploy staging -> run daily_refresh_job
.github/workflows/deploy-prod.yml    # release v*: publish-image (build, Trivy, push :<tag>/:sha-<sha>)
                                     #   -> deploy (env production approval) -> promote-image (:prod)
.github/workflows/daily-refresh.yml  # 05:17 UTC: docker run energy-extractor:prod, sync_landing.sh
                                     #   (SKIP_EXTRACT=1), then jobs run-now prod daily_refresh_job
tests/test_workflows.py              # deploy-prod job order/permissions, daily-refresh image + no secrets,
                                     #   pinned actions (no @main/@master, no checkout/upload-artifact
                                     #   @v4), runs-on ubuntu-24.04 (never ubuntu-latest)
tests/test_terraform.py              # singular grants only, prevent_destroy, no secrets, pinned +
                                     #   locked provider, versions = CI = dev container, all imported
```

## Local setup
- On Windows with Smart App Control, put `python-preference = "only-system"` in
  `%APPDATA%\uv\uv.toml` (user config, not the repo), because the setting would break CI (Linux
  runners need uv-managed Python).

## Commands (PowerShell)
```powershell
uv sync                                    # create .venv, install dev deps
uv run ruff check .                        # lint
uv run ruff format .                       # format (CI runs --check)
uv run python -m pytest                    # unit tests
uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 8  # both (weather: daily mode)
uv run python -m energy_lakehouse.extract_local --source weather                # weather only, past 7 days + today + 2 days
# One-off weather history backfill (archive API), then upload it:
uv run python -m energy_lakehouse.extract_local --source weather --mode archive --start 2026-08-01 --end 2026-10-15
.\scripts\sync_landing.ps1 -Sources weather
.\scripts\sync_landing.ps1                 # upload data/landing/{smard,weather} to the shared volume + verify
.\scripts\sync_landing.ps1 -Sources weather -Overwrite
uv run python scripts/render_contract_docs.py          # regenerate docs/data-products/*.md
uv run python scripts/render_contract_docs.py --check  # exit 1 if a page is out of date

uv build --wheel --clear                               # dist/*.whl (bundle deploy builds it too)
uv run python scripts/build_dashboard.py               # build/energy_market_de.<target>.lvdash.json
databricks bundle validate -t dev -p free              # needs the build first (dashboard file_path)
databricks bundle deploy   -t dev -p free
databricks bundle run energy_pipeline -t dev -p free                   # incremental update
databricks bundle run energy_pipeline -t dev -p free --full-refresh-all # rebuild all tables
databricks bundle run daily_refresh_job -t dev -p free                 # pipeline, checks, contracts, forecast, DQ
databricks bundle run daily_refresh_job -t dev -p free --params inject_test_anomaly=true  # + TEST:total_load anomaly
databricks bundle run train_forecast_job -t dev -p free                # train both forecasters (weekly in staging/prod)
databricks bundle destroy  -t dev -p free  # tear down dev resources
databricks bundle validate -t staging -p free   # validate only; staging/prod deploy via CI
databricks bundle validate -t prod    -p free

# Extractor image (extract only, no Databricks credentials; upload stays in the workflow)
docker build -t energy-extractor:dev --build-arg REVISION=$(git rev-parse HEAD) .
docker run --rm energy-extractor:dev --help
docker run --rm -v "${PWD}/data/landing:/data" energy-extractor:dev --source all --weeks 8
docker run --rm -v "${PWD}:/work:ro" -w /work ghcr.io/hadolint/hadolint:v2.15.0 hadolint Dockerfile
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock ghcr.io/aquasecurity/trivy:0.73.0 image --scanners vuln --severity CRITICAL --ignore-unfixed --exit-code 1 energy-extractor:dev
```

## Environments
One Free Edition workspace. Raw files live once in the shared volume
`/Volumes/workspace/energy_raw/landing/{smard,weather}` (bundle var `landing_root`); each target
builds its own tables in its own schema.

| Target | Mode | Schema | Identity (run_as / owner) | Who deploys | Root path | Name prefix |
|---|---|---|---|---|---|---|
| `dev` (default) | development | `energy_dev` | your user | you, locally (`-p free`) | `~/.bundle/<name>/dev` | `[dev <user>] ` (development mode) |
| `staging` | production | `energy_staging` | CI/CD SP (`cicd_sp_app_id`) | CI only, as the CI/CD SP | `/Workspace/Users/<deployer>/.bundle/<name>/staging` | `[staging] ` (`presets.name_prefix`) |
| `prod` | production | `energy_prod` | CI/CD SP (`cicd_sp_app_id`) | CI only, as the CI/CD SP | `/Workspace/Users/<deployer>/.bundle/<name>/prod` | `[prod] ` (`presets.name_prefix`) |

- **staging/prod are deployed only by CI as the CI/CD SP.** Never `bundle deploy/run/destroy`
  `-t staging` / `-t prod` locally; `bundle validate` is fine. Schemas `energy_staging` /
  `energy_prod` exist already and are owned by the CI/CD SP; the bundle never creates them.
- staging/prod root path is `/Workspace/Users/${workspace.current_user.userName}/...`; `<deployer>`
  is the CI/CD SP's application ID in CI, so the folder is private to it.
- Resource names are prefixed per target because all targets share one workspace (unprefixed
  staging/prod names collide, 409). Not needed with one workspace per target.
- staging/prod permissions: CI/CD SP CAN_MANAGE, `alert_email` user CAN_MANAGE. The refresh SP
  (`refresh_sp_app_id`) gets CAN_MANAGE_RUN on `daily_refresh_job` and `train_forecast_job` in
  every target.
- Variables `cicd_sp_app_id`, `refresh_sp_app_id`, `alert_email` are set per target in
  `databricks.yml` (placeholders `REPLACE_WITH_...` until filled in).
- The landing volume is not managed by the bundle and not created by notebooks/scripts. Readers
  (your user, CI/CD SP) need `READ VOLUME`, writers (refresh SP) `WRITE VOLUME` on
  `workspace.energy_raw.landing`, plus `USE SCHEMA` on `workspace.energy_raw`.

## Conventions
- **Serverless only.** Never add `new_cluster`, `job_clusters`, `existing_cluster_id` or cluster
  policies. Jobs omit compute config; pipelines use `serverless: true`.
- **All tables live in catalog `workspace`**, schema per target via `${var.catalog}` /
  `${var.schema}`. Never hardcode a schema in notebooks/SQL; pass it as a job/pipeline parameter.
- Build table names with `energy_lakehouse.utils.table_name()` (backtick-quoted names).
- **No tokens or secrets in the repo.** The workspace host is set once in `databricks.yml`
  (`workspace.host`, all targets); never set `profile` there (it breaks service-principal env-var
  auth and CI). Locally pass `-p free`. Secrets go in Databricks secret scopes or GitHub
  secrets. `.env` is gitignored.
- **PowerShell-compatible commands** in docs and instructions: no bash-only syntax
  (`&&` in PS 5.1, `export`, `\` line continuations).
- **Small, pure, testable functions in `src/energy_lakehouse/`**; notebooks stay thin and call
  them. Every new function gets a pytest test.
- **SMARD landing:** raw chunk JSON is written unchanged to
  `<base>/smard/filter=<id>/resolution=<res>/<ts>.json` (local `data/landing`, or the shared
  `/Volumes/workspace/energy_raw/landing` via `01_extract_smard`, widget
  `landing_root`). Existing files are skipped;
  pass `--overwrite` / `overwrite=True` to refresh. Exception: the `refresh_latest` newest chunks
  per series (default 2, `--refresh-latest`) are always re-downloaded so late-published hours get
  filled; bronze sets `cloudFiles.allowOverwrites` so Auto Loader re-ingests them. Tests never hit
  the network.
- **Weather landing:** Open-Meteo (no key), hourly `temperature_2m`, `wind_speed_10m`,
  `wind_speed_100m`, `shortwave_radiation`, `cloud_cover`; UTC (`timezone=GMT`),
  `timeformat=unixtime`, wind in m/s (`wind_speed_unit=ms`). Raw JSON written unchanged. Two modes
  (`extract_local --mode`):
  - `daily` (default): `/v1/forecast`, `past_days` 0–92 (default 7) + `forecast_days=3` (today + 2:
    margin so the full local D+1 is covered whatever the run time and DST) →
    `<base>/weather/location=<id>/run_ts=<yyyymmddThhmmZ>.json` (UTC run timestamp, minute
    resolution; local, CI, or via `02_extract_weather` into the shared volume, widget
    `landing_root`). Every run writes new files, so a later run on the same day fetches a fresh
    forecast; daily files are never overwritten (`--overwrite` does not apply). Consecutive runs
    overlap; `silver_weather_hourly` dedupes them. Older files named
    `run_date=<yyyy-mm-dd>.json` are still read (run time 00:00 UTC).
  - `archive`: `https://archive-api.open-meteo.com/v1/archive` with `--start` / `--end`
    (YYYY-MM-DD, inclusive, UTC; `--source weather` only; existing files skipped unless
    `--overwrite`) → `<base>/weather/location=<id>/run_date=archive_<start>_<end>.json`. One-off
    history backfill, run locally, then `sync_landing.ps1 -Sources weather`. The archive lags real
    time by a few days; recent days may be NULL (dropped by `has_measurements`).
- **Pipeline (`resources/energy_pipeline.yml`):** serverless, channel CURRENT, catalog/schema
  from bundle vars, sources globbed from `src/pipelines/**`. Use `from pyspark import pipelines
  as dp`. `energy_lakehouse` comes from the wheel in the pipeline `environment.dependencies`
  (`../dist/*.whl`); `root_path` is `../src/pipelines`, never `../src` (root_path is on
  `sys.path` and would shadow the wheel). Landing paths come from config keys `landing_path` (SMARD,
  `${var.landing_root}/smard`) and `weather_landing_path` (`${var.landing_root}/weather`), the same
  for all targets; session time zone is
  UTC, local time is derived explicitly with `Europe/Berlin`. Keep Spark-free logic in
  `transforms.py`; pipeline files only turn it into Spark expressions.
- **Tables** (unqualified in pipeline code, resolved to `${var.catalog}.${var.schema}`):
  `bronze_smard_raw` (one row per file) → `silver_energy_long` (one row per `filter_id`,
  `timestamp_utc`; newest `file_modification_time` wins; expectations `value_published` drop,
  `valid_timestamp` fail, `non_negative_energy` warn) → `gold_energy_hourly` (MWh summed,
  EUR/MWh averaged per UTC hour) → `gold_energy_daily` (per Berlin calendar day, `hour_count`
  23/24/25). Assumes one resolution per filter in the landing zone.
- **Weather tables:** `bronze_weather_raw` (one row per file; `location_id` and `run_ts_utc` from
  the path: `run_ts=` → that UTC timestamp, old `run_date=` → 00:00 UTC of that day, archive →
  00:00 UTC of the day after its end date; same rules in `transforms.parse_weather_landing_path`;
  `run_date` = UTC date of `run_ts_utc`, NULL for archive files; explicit schema from
  `transforms.weather_raw_schema()`) → `silver_weather_hourly` (observed hours: one row per
  `location_id`, `timestamp_utc`; daily and archive files; hourly arrays zipped + exploded; newest
  file wins, ordered by `run_ts_utc`, then `file_modification_time`; forecast hours > now dropped;
  expectations `valid_timestamp` fail, `has_measurements` drop, `plausible_temperature` /
  `plausible_wind` warn) → `gold_energy_weather_hourly` (`gold_energy_hourly` LEFT JOIN weather
  per `hour_utc`; columns from `transforms.WEATHER_ROLE_AGGS`: role → variable → avg).
  - Forecast branch: `bronze_weather_raw` → `silver_weather_forecast_hourly` (only the newest
    daily file per location: max `run_ts_utc`, then newest `file_modification_time`; archive
    files excluded; only hours `> run_ts_utc` = the forecast available at issue time; same
    expectations) → `gold_weather_forecast_hourly` (same `WEATHER_ROLE_AGGS` per `hour_utc`, plus
    `hour_local` and `issued_at` = max `run_ts_utc`).
  - Column lists with comments live in `transforms.py` (`GOLD_ENERGY_*_COLUMNS`,
    `gold_weather_columns()`, `gold_weather_forecast_columns()`,
    `SILVER_WEATHER_FORECAST_HOURLY_COLUMNS`); TIMESTAMP_NTZ columns need
    `table_properties={"delta.feature.timestampNtz": "supported"}`.
- **Day-ahead forecast (`forecast.py`, notebooks 10–12):** targets `total_load_mwh`,
  `price_eur_mwh`. Issued daily ~05:17 UTC on local day D for every hour of the `Europe/Berlin`
  day D+1 (23/24/25 h). Only data available at issue time: history hours that ended by the issue
  time, lags 48/72/168/336 h (`MIN_LAG_HOURS = 48`), same-hour mean of the last 7 available days
  ≥ 48 h back; `build_features` raises `LeakageError` otherwise. Calendar: local hour, dow, month,
  weekend, German public holiday (`holidays`, DE, nationwide). Weather by role: observed
  (`gold_energy_weather_hourly`) for training, as a stand-in for historical forecasts; the issued
  forecast (`gold_weather_forecast_hourly`, `issued_at` ≤ issue time) for scoring.
  - `train_forecast_job` (Mondays 06:37 UTC; paused in dev): tasks `train_load` /
    `train_price` run `10_train_forecast.py` (`HistGradientBoostingRegressor`, `MODEL_PARAMS`,
    random_state 42; last 14 days test vs seasonal naive 168 h). MLflow experiment
    `/Users/<current user>/energy_forecast_<target>`: params, `model_/baseline_mae` (+ `_mape`
    for load only; price gets MAE only), forecast-vs-actual plot, permutation importance, model
    with signature + input example. Registered as `<catalog>.<schema>.forecaster_<target>`; alias
    `champion` moves only if model MAE < baseline MAE.
  - `daily_refresh_job`: … → `verify_contracts` → `score_forecast` (`11_score_forecast.py`:
    champions → `gold_forecast_hourly`, MERGE on `target_hour_utc, target, issue_date`; targets
    without a champion are skipped with a warning) → `forecast_accuracy`
    (`12_forecast_accuracy.py`: `gold_forecast_accuracy` per target and issue_date — n_hours, MAE,
    MAPE, baseline MAE/MAPE — via `INSERT OVERWRITE`). Both tables are plain Delta tables created
    `IF NOT EXISTS` from `GOLD_FORECAST_*_COLUMNS`.
  - Libraries: only via job `environments` (see **Wheel**): environment `energy_lakehouse` =
    the wheel plus exact pins of holidays/scikit-learn/mlflow (equal to `uv.lock`), identical in
    `train_forecast_job` and `daily_refresh_job` so scoring unpickles the model with the training
    versions.
- **ML-assisted data quality (`anomalies.py`, notebooks 20–21):** pandas only, Spark-free. Every
  detector returns `ANOMALY_COLUMNS` (check_name, entity, ts_utc, value, expected, lower, upper,
  score ≥ 0, severity `critical`|`warning`, reason) for the last 7 days:
  - `seasonal_robust_z` on `silver_energy_long` (entity = series name, `day_ahead_price`
    excluded: negative/spiky prices are often real): per entity and local hour-of-week, median
    and MAD of the previous 8 weeks (current point excluded), z = 0.6745·(x − median)/MAD;
    skipped if < 5 history points; MAD floored at max(MAD, 5 % of |median|, 50 MWh); flagged
    only if |z| ≥ 4 **and** |x − median| ≥ 25 % of |median|. **Warning only** (seasonal effects
    such as less autumn solar → more gas are often real); critical is reserved for completeness
    of gated series, physics rules and forecast MAPE drift.
  - `multivariate_isolation_forest` on `gold_energy_weather_hourly` (load, price, solar, wind
    on/off, lignite, gas, on/offshore wind speed, radiation, local-hour sin/cos), contamination
    0.005, random_state 42; warning only. Trained on the last 365 days **including** the scored
    week and `max_samples=1.0` (IF cannot isolate points it has not seen; with 256 samples a
    single outlier is in ~3 % of trees). Reason: top 3 features by robust z vs the training
    median of the same local hour. scikit-learn is imported inside the function (Smart App
    Control can block scipy DLLs locally; its tests skip there but never in CI).
  - `completeness` per series and local day for the 7 complete days before today: expected
    23/24/25 h vs distinct non-null hours; > 5 % missing warning; > 20 % critical only for
    `COMPLETENESS_CRITICAL_SERIES` (`total_load`, `day_ahead_price`: forecast inputs and
    `is_complete`), a warning with the same message for every other (generation) series.
  - `physics_rules`: `solar_mwh > 100` while `shortwave_radiation_solar_avg_wm2 < 1` at both H
    and H+1 (Open-Meteo radiation is the mean of the *preceding* hour) → critical.
  - `forecast_drift` on `gold_forecast_accuracy`: MAE > baseline MAE on 3 consecutive issue
    dates → warning (`forecast_drift_mae`); MAPE > 6 % on 2 consecutive dates → critical
    (`forecast_drift_mape`, load only). Empty input → no rows.
  - `daily_refresh_job`: … → `forecast_accuracy` → `detect_anomalies`
    (`20_detect_anomalies.py`, environment `energy_lakehouse`): fails if a scored input window is empty
    ("no silent success"), MERGEs into `gold_dq_anomalies` on (check_name, entity, ts_utc)
    (`detected_at` = latest run), writes `gold_dq_daily_summary` (every check × severity, n = 0
    included) with `INSERT INTO … REPLACE WHERE detection_date = …`, prints the summary, then
    raises listing every critical row (entities `TEST:` excluded). German dates come from
    `anomalies.LOCAL_TODAY_SQL` = `to_date(from_utc_timestamp(current_timestamp(), 'Europe/Berlin'))`.
    Job parameter `inject_test_anomaly` (default `"false"`; the notebook refuses schemas not
    ending in `_dev`) adds `TEST:total_load`, a copy of total_load with the most recent complete
    hour × 0.5. → `dq_summary_llm` (`21_dq_summary_llm.py`, `run_if: ALL_DONE`): job parameter
    `dq_llm_endpoint` (default `databricks-gpt-oss-120b`, all targets; empty = skip):
    `ai_query(endpoint, prompt)` with a short prompt (counts + top 10 anomalies, 3–5 sentences for
    a data engineer; warning = unusual but possibly real, critical = likely data error) →
    `gold_dq_daily_summary_text` (model, prompt, prompt_chars; no contract); any error is a
    warning, never a failure.
- **Daily refresh:** Free Edition has no outbound internet and no account-level APIs (no OIDC
  federation policy), so `.github/workflows/daily-refresh.yml` extracts on the runner with the
  extractor image `ghcr.io/<owner>/energy-extractor:prod` (pulled, digest + version label logged,
  run by digest as the runner user, `$RUNNER_TEMP/landing` mounted at `/data`, `--source all
  --weeks 8 --past-days 7`), uploads with `SKIP_EXTRACT=1 scripts/sync_landing.sh` and runs
  `databricks jobs run-now <vars.DATABRICKS_JOB_ID>`
  (checks `result_state`, since run-now exits 0 on a FAILED run). Auth: service principal OAuth
  secret (M2M) via GitHub secrets `DATABRICKS_HOST` / `DATABRICKS_CLIENT_ID` /
  `DATABRICKS_CLIENT_SECRET`, set only on the upload and job-run steps (never job-level, never
  passed to the container); permissions `contents: read`, `packages: read`; no `-p` flag in CI.
  `daily_refresh_job` (no schedule): `run_pipeline`
  (energy_pipeline, `full_refresh: false`) → `quality_checks` (`03_quality_checks.py`, gets
  `catalog`/`schema` from the job parameters via `base_parameters`); fails on
  gold_energy_hourly older than 72 h, no weather in gold_energy_weather_hourly in the last 7 days,
  or empty silver tables. Bundle vars `refresh_sp_app_id` (job CAN_MANAGE_RUN) and `alert_email`
  (on_failure) are set per target. The SP needs UC grants on the shared landing volume.
  The workflow triggers the prod job (`vars.DATABRICKS_JOB_ID` = prod job id). CI/CD deploys
  use secrets `DATABRICKS_CICD_CLIENT_ID` / `DATABRICKS_CICD_CLIENT_SECRET` (+ `DATABRICKS_HOST`).
  OIDC is used in the AWS chapter.
- **Data contracts (`contracts/<table>.yaml`, one per gold table + `silver_weather_forecast_hourly`):**
  ODCS-inspired YAML parsed
  by `energy_lakehouse.contracts` (pydantic v2, `extra="forbid"`): id, semver `version`,
  status, owner, description, table, `object_type` (`materialized_view` for pipeline tables,
  `table` for `gold_forecast_hourly` / `gold_forecast_accuracy` / `gold_dq_*`), grain, primary_key, tags,
  sla (`freshness_hours`, `freshness_column`, schedule), consumers (group + SELECT), columns
  (name, type, nullable, description), quality rules (`unique` / `not_null` / `row_condition`
  with `sql`, optional `where`, `max_violation_pct`; `{table}` in sql/where = the quoted table).
  Columns with `nullable: false` get an implicit `required_columns_not_null` rule.
  - Single source: column lists (name, type, comment) and table comments live in
    `transforms.py` (`GOLD_ENERGY_HOURLY_COLUMNS`, `GOLD_ENERGY_DAILY_COLUMNS`,
    `GOLD_ENERGY_WEATHER_HOURLY_COLUMNS`, `GOLD_WEATHER_FORECAST_HOURLY_COLUMNS`,
    `GOLD_FORECAST_HOURLY_COLUMNS`, `GOLD_FORECAST_ACCURACY_COLUMNS`,
    `GOLD_DQ_ANOMALIES_COLUMNS`, `GOLD_DQ_DAILY_SUMMARY_COLUMNS`,
    `SILVER_WEATHER_FORECAST_HOURLY_COLUMNS`; collected in `GOLD_TABLE_COLUMNS` /
    `GOLD_TABLE_COMMENTS` and, for all contracted tables, `CONTRACT_TABLE_COLUMNS` /
    `CONTRACT_TABLE_COMMENTS`) and are used by the pipeline and the forecast notebooks. `tests/test_contracts.py` fails unless each contract's column names,
    types and descriptions and its table description match them (breaking-change guard), and
    unless `docs/data-products/*.md` match the rendered contracts.
  - `daily_refresh_job`: `quality_checks` → `apply_contracts` (`04_apply_contracts.py`: table/
    column comments and tags only where they differ, plus `GRANT USE SCHEMA` / `GRANT SELECT` per
    consumer group; removed tags/consumers are not revoked) → `verify_contracts`
    (`05_verify_contracts.py`: `information_schema.columns` names + `full_data_type`, quality
    rules, freshness; one result table, raises listing all failures). Consumer groups (e.g.
    `energy-consumers`) must exist in the workspace, else the GRANT fails the job.
  - Status: 04/05 act only on `active` / `deprecated` contracts (`contracts.is_enforced`);
    `draft` and `retired` are skipped. The forecast contracts start as `draft` (v0.1.0): their
    tables are written after `verify_contracts` and stay empty until a champion exists. Once
    forecasts flow, set `status: active` (PATCH bump) so they are enforced. Same for
    `gold_dq_anomalies` / `gold_dq_daily_summary` (draft v0.1.0, created by `detect_anomalies`
    after `verify_contracts`): activate after the first daily runs.
  - **How to change a contract:** change `transforms.py` (if the table changes) and the YAML
    together in one PR, bump `version`, run `uv run python scripts/render_contract_docs.py`,
    `uv run python -m pytest`. Semver: removing/renaming a column or changing its type = MAJOR; new column
    = MINOR; descriptions, rules, tags, SLA wording = PATCH. Never edit `docs/data-products/` by
    hand.
- **AI/BI dashboard (edit → export → build):** the dev UI needs fully qualified dataset SQL, so
  `src/energy_market_de.lvdash.json` is the dev source with `workspace.energy_dev.<table>`.
  1. **Edit** the dashboard in the dev workspace UI (tables as `workspace.energy_dev.<table>`, no
     backticks; every dataset used by a widget).
  2. **Export** only the JSON, so `resources/energy_market_de.dashboard.yml` is never overwritten:
     `databricks bundle generate dashboard --existing-id <dashboard-id> --key energy_market_de
     -s src -d $env:TEMP\dashboard_export --force -t dev -p free` (one line; the YAML written to
     `$env:TEMP` is discarded; `<dashboard-id>` from `databricks lakeview list -p free`).
  3. **Build** `uv run python scripts/build_dashboard.py`: writes
     `build/energy_market_de.<target>.lvdash.json` with `workspace.energy_dev.` replaced by
     `workspace.<schema>.` (`energy_lakehouse.dashboard.TARGET_SCHEMAS`, must match
     `databricks.yml`). Each target overrides the dashboard `file_path` with its built file, so
     build before every `bundle validate/deploy`; `build/` is gitignored. CI builds before
     validate (`ci.yml`) and before deploy (`deploy-staging.yml` / `deploy-prod.yml`, stdlib only:
     `PYTHONPATH=src python3 scripts/build_dashboard.py --target <target>`).
  4. `uv run python -m pytest`, commit the source JSON. `tests/test_dashboard.py`: the source SQL names
     only `workspace.energy_dev.` (never energy_staging/energy_prod, no other energy_dev form),
     each built file's SQL names only its own schema, every dataset is used by a widget.
- **Wheel (`energy_lakehouse`):** `pyproject.toml` (hatchling) packages only
  `src/energy_lakehouse` (no notebooks/pipelines). `[project].dependencies` are the base runtime
  dependencies (requests, pydantic, pyyaml: extractors, contracts, quality); the extra `ml`
  (`[project.optional-dependencies]`) holds holidays, scikit-learn, mlflow. Never list
  runtime-provided packages (pyspark, databricks-connect, delta-spark, numpy, pandas, pyarrow).
  Dev-only tools (pytest, ruff, pandas for local tests) go in `[dependency-groups].dev`, which
  also pulls `energy-lakehouse-de[ml]`, so `uv sync` installs the extra.
  - Job environments never install `[ml]` (`../dist/*.whl[ml]` is no valid bundle glob); they pin
    exactly the `ml` packages to the `uv.lock` versions next to the wheel (enforced by
    `tests/test_job_environments.py`). Adding an ML library = add it to `ml` and pin it in both
    the train and the daily refresh environments.
  - Build: `databricks.yml` `artifacts.energy_lakehouse` (`type: whl`, `path: .`,
    `build: uv build --wheel --clear`, `dynamic_version: true`, files `dist/*.whl`). Every
    `bundle deploy` builds it, uploads it and replaces `../dist/*.whl` in the resources with the
    uploaded path; `--clear` keeps exactly one wheel in `dist/`, `dynamic_version` adds a
    timestamp to the version so serverless never reuses a cached older wheel. Deploys need `uv`
    (CI: `setup-uv` in `deploy-staging.yml` / `deploy-prod.yml`); `bundle validate` does not.
  - Installed in: every job environment (`- ../dist/*.whl`; every notebook task that imports
    `energy_lakehouse` has an `environment_key`) and the pipeline (`environment.dependencies`).
    Job environments add only what the wheel cannot provide (exact ML pins).
  - Notebooks and pipeline files import `energy_lakehouse` directly: no `sys.path` /
    `os.path.abspath` manipulation, no `%pip` / `restartPython`. `tests/test_job_environments.py`
    enforces all of this (wheel in every importing task and the pipeline, pins = `uv.lock`,
    no runtime-provided packages, no sys.path markers).
  - Package changes reach Databricks only through `bundle deploy` (a rebuilt wheel); editing
    `src/energy_lakehouse/` in the workspace has no effect on jobs or the pipeline.
- **Extractor image release (`deploy-prod.yml`):** `publish-image` (before the approval,
  `packages: write`) checks out the tag, builds with `VERSION=<tag>`, `REVISION=<sha>`,
  `SOURCE=<repo URL>` (OCI labels; links the GHCR package to the repo), Trivy (CRITICAL with fix
  fails), pushes `:<tag>` and `:sha-<short sha of the tag commit>`, outputs the digest. Version
  tags are immutable: an existing `:<tag>` is reused, never rebuilt/overwritten. `deploy` needs
  it; `promote-image` (after deploy, `packages: write` only) checks `:<tag>` = recorded digest,
  then `docker buildx imagetools create --tag :prod <image>@<digest>` (no rebuild) and verifies.
  No other job/workflow gets `packages: write` (`tests/test_workflows.py`). Rollback = retag
  `:prod` to an older version tag's digest (docs/runbook.md "Extractor image in prod").
- **Extractor image (`Dockerfile`):** extraction only (`energy_lakehouse.extract_local` as
  ENTRYPOINT, default `--source all`). Builder stage (`ghcr.io/astral-sh/uv`, pinned) builds the
  wheel and `uv export --frozen --no-dev --no-emit-project` (base deps with hashes); runtime
  `python:3.12.<patch>-slim-trixie` installs them `--require-hashes`, then the wheel `--no-deps`
  (no ml extra; ~200 MB unpacked, target < 300 MB). `USER 10001:10001` ("app"), `WORKDIR /app`,
  `VOLUME /data`, `ENV EXTRACT_OUT=/data` (`--out` default: `$EXTRACT_OUT`, else
  `data/landing`); OCI labels source/version/revision via `--build-arg VERSION/REVISION`. Exit
  code 1 if any file failed. Never COPY the repo root, `.env` or `.databricks`; never put
  Databricks credentials in the image. Bind mounts on Linux: the folder must be writable by uid
  10001 (CI: `chmod 0777` on the temp dir). Docker Hub rate limit (429): build with
  `--build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:<same tag>`. `tests/test_docker.py`
  checks non-root USER, pinned FROMs, no `ADD http`, no repo-wide COPY, `.dockerignore`.
  Trivy CRITICAL with a fix in a Python dependency → `uv lock --upgrade-package <name>`.
- **Dev container (`.devcontainer/`):** `build.dockerfile` = `.devcontainer/Dockerfile`, `FROM
  mcr.microsoft.com/devcontainers/python:1-3.12-bookworm` (major or exact `x.y.z` tag, never
  `latest`; Python minor = `.python-version` = CI) plus `rm -f /etc/apt/sources.list.d/yarn.list`
  (the base image's Yarn source has a missing key, NO_PUBKEY, so feature `apt-get update` fails), features
  `github-cli:1` and `docker-outside-of-docker:1` (major tags, no options), local
  `features/databricks-cli` pinned to the CI `setup-cli` version (1.23.0), uv in `post-create.sh` = the
  Dockerfile's uv. `remoteUser: vscode`; venv at `$UV_PROJECT_ENVIRONMENT`
  (`/home/vscode/.venvs/energy-lakehouse-de`, never the host `.venv`). No mounts of credentials and
  no TOKEN/SECRET env: log in inside with `databricks auth login --host <workspace.host> --profile
  free` (callback port 8020 forwarded) and `gh auth login`. When bumping a version, keep these
  equalities (`tests/test_devcontainer.py`). Inside: `docker run` without `-it` loses output
  (socat proxy), `-v` paths are host paths.
- **Terraform (`terraform/`, ADR 0010):** platform only: schemas, the landing volume, the two
  SPs and `energy-consumers` (entitlements, no membership), and their catalog/schema/volume
  grants. Only `databricks_grant` (singular, one principal), never `databricks_grants` or
  `databricks_permissions`; never the `_workspace_users` catalog grant, consumer-group
  `USE SCHEMA` or table grants (`apply_contracts`), or bundle resources. `prevent_destroy` on
  schemas, the volume and identities; no OAuth secrets/tokens; provider auth from the environment
  (`DATABRICKS_CONFIG_PROFILE=free`), host from `var.databricks_host`. Local state (gitignored).
  The existing objects are adopted with `import` blocks; the plan must stay zero-diff unless a
  PR changes the platform on purpose. Terraform/tflint versions are equal in `versions.tf`,
  ci.yml job `terraform` (fmt, `init -backend=false`, validate, tflint; no credentials, never
  plan/apply) and the dev container feature (`tests/test_terraform.py`). Plan/apply only in the
  dev container, by the user; never run `terraform apply` on the user's behalf.
- Ruff line length 100; CI must pass `ruff check`, `ruff format --check`, `pytest`.
- Do not run `databricks bundle deploy/run/destroy` on the user's behalf — the user runs
  checkpoints.
- Tutorial labs follow the template: Goal, Prerequisites, Steps (action → command → expected
  result → screenshot `labN-stepNN-name.png`), Checkpoint, Troubleshooting, What you learned,
  Tested on.
