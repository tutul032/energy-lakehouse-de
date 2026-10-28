# 2. Extract source data in GitHub Actions, not in Databricks jobs

- **Status:** Accepted
- **Date:** 2026-10-20 (recorded 2026-10-27)

## Context
SMARD.de and Open-Meteo are public HTTP APIs. The natural place to call them would be a
Databricks notebook task. Databricks Free Edition has no outbound internet access from serverless
compute, so a notebook cannot reach either API. Free Edition also has no account-level APIs,
so GitHub OIDC workload identity federation (which needs an account-level federation policy) is
not available.

## Decision
- A scheduled GitHub Actions workflow (`.github/workflows/daily-refresh.yml`, 05:17 UTC) runs the
  extractor (`energy_lakehouse.extract_local`) on the runner, writes the raw JSON unchanged to
  `data/landing`, and uploads it with `scripts/sync_landing.sh` (`databricks fs cp`, then a
  listing per source to verify the upload) to the Unity Catalog volume
  `/Volumes/workspace/energy_raw/landing`.
- The workflow then triggers the prod `daily_refresh_job` with `databricks jobs run-now` and
  checks `result_state`, because `run-now` exits 0 even when the run failed.
- Authentication: a dedicated refresh service principal with an OAuth secret (M2M) stored in
  GitHub secrets. It has `CAN_MANAGE_RUN` on the job and `WRITE VOLUME` on the landing volume.
- The extractor is plain Python (`requests`), unit-tested offline, so the same code runs locally,
  in CI and in any workspace that does have internet access.
- *Update 2026-10-29 (Lab 11):* the workflow now runs the extractor as the released container
  image `ghcr.io/<owner>/energy-extractor:prod` (published and promoted by `deploy-prod.yml`)
  into `$RUNNER_TEMP/landing`; upload and job trigger are unchanged. The container gets no
  Databricks credentials.

## Consequences
- Ingestion depends on GitHub Actions scheduling (cron runs can start late) and on a long-lived
  OAuth secret that must be rotated (see `docs/runbook.md`).
- Auto Loader only sees files; the API boundary stays outside Databricks. Raw files are
  immutable inputs, which makes reprocessing and debugging straightforward.
- On a workspace with internet access, a notebook task could call the same extractor; the
  pipeline would not change.
