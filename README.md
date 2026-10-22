# energy-lakehouse-de

[![ci](https://github.com/tutul032/energy-lakehouse-de/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/tutul032/energy-lakehouse-de/actions/workflows/ci.yml)
[![deploy-staging](https://github.com/tutul032/energy-lakehouse-de/actions/workflows/deploy-staging.yml/badge.svg?branch=master)](https://github.com/tutul032/energy-lakehouse-de/actions/workflows/deploy-staging.yml)
[![daily-refresh](https://github.com/tutul032/energy-lakehouse-de/actions/workflows/daily-refresh.yml/badge.svg)](https://github.com/tutul032/energy-lakehouse-de/actions/workflows/daily-refresh.yml)

## CI/CD

```
pull request / push to master
  ci.yml               uv sync, ruff check, ruff format --check, pytest
                       databricks bundle validate -t staging / -t prod      (CI/CD SP)
        |
        v  merge to master (or Run workflow)
deploy-staging.yml     ci.yml (reused as gate) -> databricks bundle deploy -t staging
                       -> databricks bundle run daily_refresh_job -t staging (fails on FAILED run)
        |
        v  GitHub release published, tag v* (or Run workflow with a tag)
deploy-prod.yml        environment `production` (manual approval), checkout the tag
                       -> databricks bundle deploy -t prod -> databricks bundle summary -t prod
                       (no job run: daily-refresh.yml runs the prod job every day)
        |
        v  05:17 UTC daily
daily-refresh.yml      extract -> /Volumes/workspace/energy_raw/landing -> prod daily_refresh_job
```

| Target | Schema | Deployed by | Runs as |
|---|---|---|---|
| `dev` | `energy_dev` | you, locally (`-p free`) | your user |
| `staging` | `energy_staging` | `deploy-staging.yml` | CI/CD service principal |
| `prod` | `energy_prod` | `deploy-prod.yml` | CI/CD service principal |

All targets read raw files from the shared volume `/Volumes/workspace/energy_raw/landing` and
build their own tables in their own schema. Deploy auth is OAuth M2M for the CI/CD service
principal, no profiles: GitHub secrets `DATABRICKS_HOST`, `DATABRICKS_CICD_CLIENT_ID`,
`DATABRICKS_CICD_CLIENT_SECRET` are mapped to `DATABRICKS_HOST` / `DATABRICKS_CLIENT_ID` /
`DATABRICKS_CLIENT_SECRET`.

Setup:
1. Fill `cicd_sp_app_id`, `refresh_sp_app_id` and `alert_email` for every target in
   `databricks.yml` (`tests/test_bundle_config.py` fails while `REPLACE_WITH_...` remains).
2. Add the secrets `DATABRICKS_CICD_CLIENT_ID` and `DATABRICKS_CICD_CLIENT_SECRET` (an OAuth
   secret of the CI/CD service principal).
3. Create the environment `production` (*Settings → Environments*) with *Required reviewers*.

## Daily refresh

Every day at 05:17 UTC (or on demand via *Run workflow*), GitHub Actions refreshes the prod
lakehouse:

```
GitHub Actions (.github/workflows/daily-refresh.yml)
  1. extract_local --source all       SMARD + Open-Meteo -> data/landing on the runner
  2. scripts/sync_landing.sh          databricks fs cp -r --overwrite -> /Volumes/workspace/energy_raw/landing
                                      + databricks fs ls verification per source
  3. databricks jobs run-now <id>     waits; workflow fails unless result_state == SUCCESS
  4. upload data/landing/logs         extractor log as a workflow artifact
        |
        v  service principal, OAuth M2M (client id + secret)
Databricks Free Edition: prod daily_refresh_job (serverless, no schedule)
  run_pipeline     energy_pipeline update (incremental, full_refresh: false)
  quality_checks   src/notebooks/03_quality_checks.py
                   - gold_energy_hourly max(hour_utc) at most 72 h old
                   - gold_energy_weather_hourly has weather in the last 7 days
                   - silver_energy_long and silver_weather_hourly not empty
  apply_contracts  src/notebooks/04_apply_contracts.py: comments, tags, grants from contracts/
  verify_contracts src/notebooks/05_verify_contracts.py: schema, quality rules, freshness SLA
  on failure       email to ${var.alert_email}
```

**Why extract on GitHub?** Databricks Free Edition has no outbound internet access, so notebooks
cannot call SMARD or Open-Meteo. The runner downloads the raw JSON and uploads it to the landing
volume; Auto Loader picks it up from there.

**Why an OAuth secret and not OIDC?** GitHub OIDC workload identity federation needs an
account-level federation policy, and Free Edition exposes no account-level APIs. The workflow
therefore authenticates as a service principal with an OAuth secret (M2M) from GitHub secrets.
OIDC (keyless) auth is used in the AWS chapter, where account-level APIs are available.

### Setup

1. Create a service principal in the workspace (*Settings → Identity and access → Service
   principals*) and generate an OAuth secret for it.
2. Set `refresh_sp_app_id` (its application ID) and `alert_email` in `databricks.yml`; the prod
   job is deployed by `deploy-prod.yml` (the refresh SP gets `CAN_MANAGE_RUN` on it).
3. Grant the service principal access to the shared landing volume (SQL editor). All targets
   read raw files from this one volume; the CI/CD service principal (which runs the staging/prod
   pipelines) needs `READ VOLUME` on it too:
   ```sql
   GRANT USE CATALOG ON CATALOG workspace TO `<application-id>`;
   GRANT USE SCHEMA ON SCHEMA workspace.energy_raw TO `<application-id>`;
   GRANT READ VOLUME, WRITE VOLUME ON VOLUME workspace.energy_raw.landing TO `<application-id>`;
   ```
4. Find the prod job ID: `databricks bundle summary -t prod -p free` (or the summary printed at
   the end of `deploy-prod.yml`).
5. In GitHub (*Settings → Secrets and variables → Actions*) add the secrets `DATABRICKS_HOST`,
   `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` and the variable `DATABRICKS_JOB_ID`.
6. Run the workflow once via *Actions → daily-refresh → Run workflow*.

The job runs the pipeline as its owner (the CI/CD service principal in prod); the service principal only needs `CAN_MANAGE_RUN` on the
job (set by the bundle) plus the volume grants above.

## Data products

Each gold table is a data product with a contract in [`contracts/`](contracts/) (schema, grain,
owner, freshness SLA, quality rules, tags, consumer groups). The product pages are generated from
the contracts:

| Data product | Table | Contract |
|---|---|---|
| [Energy market, hourly](docs/data-products/gold_energy_hourly.md) | `gold_energy_hourly` | [`gold_energy_hourly.yaml`](contracts/gold_energy_hourly.yaml) |
| [Energy market, daily](docs/data-products/gold_energy_daily.md) | `gold_energy_daily` | [`gold_energy_daily.yaml`](contracts/gold_energy_daily.yaml) |
| [Energy + weather, hourly](docs/data-products/gold_energy_weather_hourly.md) | `gold_energy_weather_hourly` | [`gold_energy_weather_hourly.yaml`](contracts/gold_energy_weather_hourly.yaml) |

After every refresh, `daily_refresh_job` applies each contract (`apply_contracts`: comments,
tags, `GRANT SELECT` to the consumer groups) and verifies it (`verify_contracts`: columns and
types via `information_schema`, quality rules, freshness); a violation fails the job.

**Changing a contract:** edit `contracts/<table>.yaml` in a pull request, bump `version`
(semver) and regenerate the pages with `uv run python scripts/render_contract_docs.py`.

| Change | Version bump |
|---|---|
| Remove or rename a column, change a column type | MAJOR (`1.4.2` → `2.0.0`) |
| Add a column | MINOR (`1.4.2` → `1.5.0`) |
| Descriptions, quality rules, tags, SLA wording | PATCH (`1.4.2` → `1.4.3`) |

The gold column lists live in `src/energy_lakehouse/transforms.py`; `tests/test_contracts.py`
fails if a contract and the pipeline disagree on columns, types or descriptions, so a pipeline
change cannot silently break consumers.
