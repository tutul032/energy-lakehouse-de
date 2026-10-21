# energy-lakehouse-de

## Daily refresh

Every day at 05:15 UTC (or on demand via *Run workflow*), GitHub Actions refreshes the lakehouse:

```
GitHub Actions (.github/workflows/daily-refresh.yml)
  1. extract_local --source all       SMARD + Open-Meteo -> data/landing on the runner
  2. scripts/sync_landing.sh          databricks fs cp -r --overwrite -> /Volumes/workspace/energy_raw/landing
                                      + databricks fs ls verification per source
  3. databricks jobs run-now <id>     waits; workflow fails unless result_state == SUCCESS
  4. upload data/landing/logs         extractor log as a workflow artifact
        |
        v  service principal, OAuth M2M (client id + secret)
Databricks Free Edition: daily_refresh_job (serverless, no schedule)
  run_pipeline     energy_pipeline update (incremental, full_refresh: false)
  quality_checks   src/notebooks/03_quality_checks.py
                   - gold_energy_hourly max(hour_utc) at most 72 h old
                   - gold_energy_weather_hourly has weather in the last 7 days
                   - silver_energy_long and silver_weather_hourly not empty
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
2. Set `refresh_sp_app_id` (its application ID) and `alert_email` in the `dev` target of
   `databricks.yml`, then deploy: `databricks bundle deploy -t dev -p free`.
3. Grant the service principal access to the shared landing volume (SQL editor). All targets
   read raw files from this one volume; the CI/CD service principal (which runs the staging/prod
   pipelines) needs `READ VOLUME` on it too:
   ```sql
   GRANT USE CATALOG ON CATALOG workspace TO `<application-id>`;
   GRANT USE SCHEMA ON SCHEMA workspace.energy_raw TO `<application-id>`;
   GRANT READ VOLUME, WRITE VOLUME ON VOLUME workspace.energy_raw.landing TO `<application-id>`;
   ```
4. Find the job ID: `databricks jobs list -p free` (in dev mode the name is prefixed
   `[dev <user>]`).
5. In GitHub (*Settings → Secrets and variables → Actions*) add the secrets `DATABRICKS_HOST`,
   `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` and the variable `DATABRICKS_JOB_ID`.
6. Run the workflow once via *Actions → daily-refresh → Run workflow*.

The job runs the pipeline as its owner; the service principal only needs `CAN_MANAGE_RUN` on the
job (set by the bundle) plus the volume grants above.
