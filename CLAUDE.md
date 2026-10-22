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
  quality.py                #   daily-refresh quality checks (thresholds, CheckResult, raise)
  contracts.py              #   data contracts: pydantic models, YAML loader, SQL renderers,
                            #   schema/quality/freshness checks, product-page markdown
contracts/                  # data contracts, one <gold_table>.yaml each (ODCS-inspired)
src/pipelines/              # Lakeflow Spark Declarative Pipeline sources (energy_pipeline)
  bronze.py                 #   bronze_smard_raw   (streaming table, Auto Loader)
  silver.py                 #   silver_energy_long (MV, exploded + deduped + expectations)
  gold.py                   #   gold_energy_hourly, gold_energy_daily (MVs, wide)
  bronze_weather.py         #   bronze_weather_raw (streaming table, Auto Loader)
  silver_weather.py         #   silver_weather_hourly (MV, arrays_zip + explode + dedupe)
  gold_weather.py           #   gold_energy_weather_hourly (MV, energy LEFT JOIN weather by role)
src/notebooks/              # Databricks notebooks (source format .py)
  03_quality_checks.py      #   quality_checks task of daily_refresh_job (uses quality.py)
  04_apply_contracts.py     #   apply_contracts task: comments/tags on drift + grants
  05_verify_contracts.py    #   verify_contracts task: schema, quality rules, freshness SLA
scripts/sync_landing.ps1    # local landing -> shared UC volume (fs cp per source, $LASTEXITCODE checks)
scripts/render_contract_docs.py # contracts/*.yaml -> docs/data-products/<table>.md (--check)
scripts/sync_landing.sh     # bash: extract all + fs cp -r --overwrite + verify (env-var auth, CI)
tests/                      # pytest tests for src/energy_lakehouse (offline; fixtures/ for samples)
data/                       # local landing output (gitignored)
docs/adr/                   # architecture decision records (NNNN-title.md)
docs/tutorial/              # step-by-step labs (labN-*.md)
docs/img/                   # screenshots: labN-stepNN-name.png
docs/data-products/         # generated data product pages (one per contract; do not hand-edit)
.github/workflows/ci.yml    # PR + push master (+ workflow_call): lint, test, bundle validate staging/prod
.github/workflows/deploy-staging.yml # push master: ci gate -> deploy staging -> run daily_refresh_job
.github/workflows/deploy-prod.yml    # release v*: env production (approval) -> deploy prod -> summary
.github/workflows/daily-refresh.yml  # 05:17 UTC: sync_landing.sh, then jobs run-now prod daily_refresh_job
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
uv run pytest                              # unit tests (or: uv run python -m pytest)
uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 8 --past-days 63  # both
uv run python -m energy_lakehouse.extract_local --source weather --past-days 63               # weather only
.\scripts\sync_landing.ps1                 # upload data/landing/{smard,weather} to the shared volume + verify
.\scripts\sync_landing.ps1 -Sources weather -Overwrite
uv run python scripts/render_contract_docs.py          # regenerate docs/data-products/*.md
uv run python scripts/render_contract_docs.py --check  # exit 1 if a page is out of date

databricks bundle validate -t dev -p free
databricks bundle deploy   -t dev -p free
databricks bundle run hello_job -t dev -p free
databricks bundle run extract_smard_job -t dev -p free
databricks bundle run energy_pipeline -t dev -p free                   # incremental update
databricks bundle run energy_pipeline -t dev -p free --full-refresh-all # rebuild all tables
databricks bundle run daily_refresh_job -t dev -p free                 # pipeline + quality checks
databricks bundle destroy  -t dev -p free  # tear down dev resources
databricks bundle validate -t staging -p free   # validate only; staging/prod deploy via CI
databricks bundle validate -t prod    -p free
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
  (`refresh_sp_app_id`) gets CAN_MANAGE_RUN on `daily_refresh_job` in every target.
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
  `/Volumes/workspace/energy_raw/landing` via `01_extract_smard`, widget/job parameter
  `landing_root`). Existing files are skipped;
  pass `--overwrite` / `overwrite=True` to refresh. Exception: the `refresh_latest` newest chunks
  per series (default 2, `--refresh-latest`) are always re-downloaded so late-published hours get
  filled; bronze sets `cloudFiles.allowOverwrites` so Auto Loader re-ingests them. Tests never hit
  the network.
- **Weather landing:** Open-Meteo `/v1/forecast` (no key), hourly `temperature_2m`,
  `wind_speed_10m`, `wind_speed_100m`, `shortwave_radiation`, `cloud_cover`; UTC (`timezone=GMT`),
  `timeformat=unixtime`, wind in m/s, `past_days` 0–92 (default 63) + `forecast_days=1`. Raw JSON
  written unchanged to `<base>/weather/location=<id>/run_date=<yyyy-mm-dd>.json` (run date in UTC;
  local or via `02_extract_weather` into the shared volume, widget `landing_root`). Existing files
  skipped unless `--overwrite`. Consecutive runs
  overlap; `silver_weather_hourly` dedupes them. Not yet wired into a job (run the notebook).
- **Pipeline (`resources/energy_pipeline.yml`):** serverless, channel CURRENT, catalog/schema
  from bundle vars, sources globbed from `src/pipelines/**`. Use `from pyspark import pipelines
  as dp`. `root_path: ../src` puts `energy_lakehouse` on `sys.path` (fallback: config key
  `bundle.sourcePath`). Landing paths come from config keys `landing_path` (SMARD,
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
- **Weather tables:** `bronze_weather_raw` (one row per file; `location_id`, `run_date` from the
  path; explicit schema from `transforms.weather_raw_schema()`) → `silver_weather_hourly` (one row
  per `location_id`, `timestamp_utc`; hourly arrays zipped + exploded; newest
  `file_modification_time` wins; forecast hours > now dropped; expectations `valid_timestamp`
  fail, `has_measurements` drop, `plausible_temperature` / `plausible_wind` warn) →
  `gold_energy_weather_hourly` (`gold_energy_hourly` LEFT JOIN weather per `hour_utc`; columns
  from `transforms.WEATHER_ROLE_AGGS`: role → variable → avg). Gold column lists with comments
  live in `transforms.py` (`GOLD_ENERGY_*_COLUMNS`, `gold_weather_columns()`); TIMESTAMP_NTZ
  columns need `table_properties={"delta.feature.timestampNtz": "supported"}`.
- **Daily refresh:** Free Edition has no outbound internet and no account-level APIs (no OIDC
  federation policy), so `.github/workflows/daily-refresh.yml` extracts on the runner, uploads with
  `scripts/sync_landing.sh` and runs `databricks jobs run-now <vars.DATABRICKS_JOB_ID>`
  (checks `result_state`, since run-now exits 0 on a FAILED run). Auth: service principal OAuth
  secret (M2M) via GitHub secrets `DATABRICKS_HOST` / `DATABRICKS_CLIENT_ID` /
  `DATABRICKS_CLIENT_SECRET`; no `-p` flag in CI. `daily_refresh_job` (no schedule): `run_pipeline`
  (energy_pipeline, `full_refresh: false`) → `quality_checks` (`03_quality_checks.py`, gets
  `catalog`/`schema` from the job parameters via `base_parameters`); fails on
  gold_energy_hourly older than 72 h, no weather in gold_energy_weather_hourly in the last 7 days,
  or empty silver tables. Bundle vars `refresh_sp_app_id` (job CAN_MANAGE_RUN) and `alert_email`
  (on_failure) are set per target. The SP needs UC grants on the shared landing volume.
  The workflow triggers the prod job (`vars.DATABRICKS_JOB_ID` = prod job id). CI/CD deploys
  use secrets `DATABRICKS_CICD_CLIENT_ID` / `DATABRICKS_CICD_CLIENT_SECRET` (+ `DATABRICKS_HOST`).
  OIDC is used in the AWS chapter.
- **Data contracts (`contracts/<table>.yaml`, one per gold table):** ODCS-inspired YAML parsed
  by `energy_lakehouse.contracts` (pydantic v2, `extra="forbid"`): id, semver `version`,
  status, owner, description, table, `object_type: materialized_view`, grain, primary_key, tags,
  sla (`freshness_hours`, `freshness_column`, schedule), consumers (group + SELECT), columns
  (name, type, nullable, description), quality rules (`unique` / `not_null` / `row_condition`
  with `sql`, optional `where`, `max_violation_pct`; `{table}` in sql/where = the quoted table).
  Columns with `nullable: false` get an implicit `required_columns_not_null` rule.
  - Single source: gold column lists (name, type, comment) and table comments live in
    `transforms.py` (`GOLD_ENERGY_HOURLY_COLUMNS`, `GOLD_ENERGY_DAILY_COLUMNS`,
    `GOLD_ENERGY_WEATHER_HOURLY_COLUMNS`, `GOLD_TABLE_COLUMNS`, `GOLD_TABLE_COMMENTS`) and are
    used by the pipeline. `tests/test_contracts.py` fails unless each contract's column names,
    types and descriptions and its table description match them (breaking-change guard), and
    unless `docs/data-products/*.md` match the rendered contracts.
  - `daily_refresh_job`: `quality_checks` → `apply_contracts` (`04_apply_contracts.py`: table/
    column comments and tags only where they differ, plus `GRANT USE SCHEMA` / `GRANT SELECT` per
    consumer group; removed tags/consumers are not revoked) → `verify_contracts`
    (`05_verify_contracts.py`: `information_schema.columns` names + `full_data_type`, quality
    rules, freshness; one result table, raises listing all failures). Consumer groups (e.g.
    `energy-consumers`) must exist in the workspace, else the GRANT fails the job.
  - **How to change a contract:** change `transforms.py` (if the table changes) and the YAML
    together in one PR, bump `version`, run `uv run python scripts/render_contract_docs.py`,
    `uv run pytest`. Semver: removing/renaming a column or changing its type = MAJOR; new column
    = MINOR; descriptions, rules, tags, SLA wording = PATCH. Never edit `docs/data-products/` by
    hand.
- Ruff line length 100; CI must pass `ruff check`, `ruff format --check`, `pytest`.
- Do not run `databricks bundle deploy/run/destroy` on the user's behalf — the user runs
  checkpoints.
- Tutorial labs follow the template: Goal, Prerequisites, Steps (action → command → expected
  result → screenshot `labN-stepNN-name.png`), Checkpoint, Troubleshooting, What you learned,
  Tested on.
