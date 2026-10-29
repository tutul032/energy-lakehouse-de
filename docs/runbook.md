# Runbook

Operational fixes for energy-lakehouse-de. Commands are PowerShell-compatible.

- [Daily refresh failed](#daily-refresh-failed)
- [Rerun prod safely](#rerun-prod-safely)
- [DST change days](#dst-change-days)
- [Secret rotation](#secret-rotation)
- [Dashboard modified remotely](#dashboard-modified-remotely)
- [Release checklist](#release-checklist)
- [Run the extractor in Docker](#run-the-extractor-in-docker)
- [Extractor image in prod](#extractor-image-in-prod)

## Daily refresh failed

The `daily-refresh` workflow (05:17 UTC) extracts (extractor image
`ghcr.io/<owner>/energy-extractor:prod`), uploads and then runs the prod `daily_refresh_job`. A failure email goes to `alert_email`, and the workflow run turns red.

**1. Find the failed step in GitHub.** *Actions → daily-refresh → the red run*:

| Failed step | Meaning |
|---|---|
| `Log in to GHCR` / `Pull extractor image and log its digest` | `:prod` does not exist yet (no release promoted), or the repository lost read access to the package. See [Extractor image in prod](#extractor-image-in-prod). |
| `Extract landing files (container)` | The extractor exited 1: an API call failed after 3 tries (`FAILED` lines in the log). Rerun; existing files are skipped. |
| `Upload landing files` | `scripts/sync_landing.sh` (`SKIP_EXTRACT=1`) stopped: a source folder is empty, or `databricks fs cp` / the listing check failed. The log names the step (`FAILED: ...`). |
| `Run prod daily_refresh_job and wait` | The Databricks job failed (or `DATABRICKS_JOB_ID` is not set). The log prints `run: <run_page_url>` and `result: <state>`. |

**2. Find the first failed task.** Open the `run_page_url` (graph view), or use the CLI. The
first task with result `FAILED` is the cause; later tasks show `UPSTREAM_FAILED`
(`dq_summary_llm` runs anyway, `run_if: ALL_DONE`).

```powershell
$run = databricks jobs get-run <run-id> -p free -o json | ConvertFrom-Json
$run.tasks | Select-Object task_key, run_id, @{n='result'; e={$_.state.result_state}}
databricks jobs get-run-output <task-run-id> -p free    # error message and trace of one task
```

**3. Common causes and fixes**

| Task | Typical cause | Fix |
|---|---|---|
| `run_pipeline` | Expectation `valid_timestamp` failed (malformed landing file); missing `READ VOLUME` for the job's run-as identity on the landing volume. | Find the file in the pipeline event log, move it out of the landing volume, rerun. Re-grant `READ VOLUME`. |
| `quality_checks` | `gold_energy_hourly` older than 72 h (extraction stopped, or SMARD published late); no weather in the last 7 days; an empty silver table. | Check the extract step and the `extract-logs-<run id>` workflow artifact. If SMARD is late, wait for the next run. |
| `apply_contracts` | A consumer group in a contract (e.g. `energy-consumers`) does not exist, so `GRANT` fails. | Create the group in the workspace, or remove it from the contract (PATCH bump). |
| `verify_contracts` | Schema drift, a quality rule above its allowed violation %, or freshness SLA missed. The task lists every failure. | Schema: fix the pipeline or the contract (ADR 0005). Freshness: same as `quality_checks`. |
| `score_forecast` | Fewer than 23 forecast rows for a target with a champion (e.g. no weather forecast for D+1). Targets without a champion are skipped with a warning, not failed. | Check `gold_weather_forecast_hourly` for D+1 and the weather upload. |
| `detect_anomalies` | A **critical** finding (completeness, physics rule, forecast MAPE drift) or an empty input window. Tables are written before it raises. | Read the listed critical rows (dashboard page *Data quality*, `gold_dq_anomalies`). Fix the source data, then rerun. |
| any notebook task | Serverless environment install failed (wheel or pinned packages). | Check the task's environment log; redeploy (`bundle deploy` rebuilds the wheel). |

## Rerun prod safely

Never `databricks bundle run/deploy -t prod` locally; prod is deployed and run by CI and the
refresh service principal.

- **Everything again (extract, upload, job):** *Actions → daily-refresh → Run workflow*, or
  `gh workflow run daily-refresh.yml`.
- **Only the Databricks part failed:** in the workspace, *Jobs → `[prod] daily_refresh_job` →
  failed run → Repair run*. It reruns the failed task and everything downstream.

Why a rerun is safe:

- Extraction skips existing SMARD files except the newest 2 chunks per series; weather writes new
  files that silver deduplicates.
- The pipeline update is incremental (`full_refresh: false`).
- `score_forecast` MERGEs on (`target_hour_utc`, `target`, `issue_date`); `forecast_accuracy` uses
  `INSERT OVERWRITE`; `detect_anomalies` MERGEs and replaces the summary for the detection date.
- `max_concurrent_runs: 1` with queueing: a second trigger waits instead of running in parallel.

Caveats:

- `issue_date` is the Europe/Berlin date at run time. A rerun after local midnight issues the
  forecast for the new day; the missed issue date stays empty (no backfill).
- Do not run a full refresh of the prod pipeline to fix a single bad file; it reprocesses every
  landing file.

## DST change days

All tables are computed in UTC; local (`Europe/Berlin`) days have 23 hours on the last Sunday of
March and 25 hours on the last Sunday of October.

| Date | Local day length | Forecast issued on |
|---|---|---|
| 2026-10-25 | 25 h | Sat 2026-10-24 |
| 2027-03-28 | 23 h | Sat 2027-03-27 |
| 2027-10-31 | 25 h | Sat 2027-10-30 |

Expected behaviour, not a bug:

- `gold_energy_daily.hour_count` is 23 or 25; `completeness` expects 23/25 hours for that day.
- The forecast issued the Saturday before has 23 or 25 rows per target; `forecast_accuracy`
  `n_hours` matches.
- In local-time charts, two UTC hours share local 02:00 in October, and local 02:00 does not exist
  in March.
- The cron is UTC (05:17), so the local run time moves from 07:17 (CEST) to 06:17 (CET).

Check on the Monday after a change day:

```sql
SELECT date_local, hour_count, is_complete
FROM workspace.energy_prod.gold_energy_daily
WHERE date_local BETWEEN '2027-03-27' AND '2027-03-29';

SELECT target, issue_date, count(*) AS n_hours
FROM workspace.energy_prod.gold_forecast_hourly
WHERE issue_date = '2027-03-27'
GROUP BY ALL;
```

## Secret rotation

OAuth secrets of service principals expire. Keep this table current (placeholders until filled
in).

| GitHub secret | Service principal | Used by | Created | Expires | Rotate by |
|---|---|---|---|---|---|
| `DATABRICKS_CLIENT_SECRET` | refresh SP (`refresh_sp_app_id`) | `daily-refresh.yml` | `YYYY-MM-DD` | `YYYY-MM-DD` | `YYYY-MM-DD` (expiry − 14 days) |
| `DATABRICKS_CICD_CLIENT_SECRET` | CI/CD SP (`cicd_sp_app_id`) | `ci.yml`, `deploy-staging.yml`, `deploy-prod.yml` | `YYYY-MM-DD` | `YYYY-MM-DD` | `YYYY-MM-DD` (expiry − 14 days) |

`DATABRICKS_HOST` and the `*_CLIENT_ID` secrets do not expire.

Rotation (workspace admin; one secret at a time):

```powershell
# 1. Find the service principal's workspace ID (not the application ID)
databricks service-principals list -p free -o json | ConvertFrom-Json |
  Select-Object id, applicationId, displayName

# 2. Create a new secret (here: 365 days); copy the secret value from the output
databricks service-principal-secrets-proxy create <sp-id> --lifetime 31536000s -p free

# 3. Store it in GitHub (prompts for the value)
gh secret set DATABRICKS_CLIENT_SECRET

# 4. Verify with a real run
gh workflow run daily-refresh.yml          # refresh SP
gh workflow run deploy-staging.yml         # CI/CD SP

# 5. Delete the old secret once the run is green
databricks service-principal-secrets-proxy list <sp-id> -p free
databricks service-principal-secrets-proxy delete <sp-id> <old-secret-id> -p free
```

Update the table (created, expiry, rotate-by) in the same PR as any related change, or in a
separate docs PR.

## Dashboard modified remotely

**Symptom.** `deploy-prod` (or `deploy-staging`) fails in `databricks bundle deploy` with:

```
Error: dashboard "energy_market_de" has been modified remotely
This dashboard has been modified remotely since the last bundle deployment.
```

**Cause.** Someone edited the deployed dashboard in the workspace UI (e.g. `[prod] Energy Market
DE`), so its remote state no longer matches what the last deploy wrote. The bundle refuses to
overwrite those edits without `--force`.

**Decision rule: the repo wins.** The deployed dashboard is a build output of
`src/energy_market_de.lvdash.json`; edits in the staging/prod UI are never kept.

1. If the remote change is **not wanted** (an experiment or an accident): redeploy with `--force`
   (below). It overwrites the remote edits.
2. If the change **is wanted**: do not export from prod. Port it through the normal dashboard flow
   (CLAUDE.md, "AI/BI dashboard"): make the same change in the **dev** UI, export the source JSON,
   run `uv run python scripts/build_dashboard.py` and `uv run python -m pytest`, then open a PR, merge and
   cut a release. Then redeploy prod with `--force`, because the remote edit is still there and
   blocks the deploy either way.

**Force deploy (prod).** GitHub → Actions → `deploy-prod` → *Run workflow*: `tag` = the release
to deploy (e.g. `v1.2.0`), check `force`. The `production` environment approval still applies.
Release-triggered runs never use `--force`. The workflow runs:

```powershell
databricks bundle deploy -t prod --force
```

Never run it locally: staging/prod are deployed only by CI as the CI/CD service principal.

**Prevent it.** Consumers (`energy-consumers`) get only `CAN_READ` on the dashboard. `CAN_MANAGE`
is limited to the CI/CD SP and the `alert_email` owner (target permissions in `databricks.yml`);
edit in dev only.

## Release checklist

1. **PR:** CI green: job `ci` (`ruff check`, `ruff format --check`, `pytest`, `bundle validate`
   staging/prod) and job `docker` (hadolint, image build, smoke extract, Trivy).
   If a table changed: contract YAML + `transforms.py` in the same PR, `version` bumped,
   `uv run python scripts/render_contract_docs.py` run.
2. **Merge to master:** `deploy-staging` deploys staging and runs `daily_refresh_job` end to end;
   wait for green.
3. **Check staging:** dashboard `[staging] Energy Market DE` loads; no unexpected critical rows in
   `workspace.energy_staging.gold_dq_anomalies`.
4. **CHANGELOG:** add the release section to `CHANGELOG.md` (merged PRs, notable changes).
5. **Release:** create the GitHub release with tag `vX.Y.Z` (semver) from master. `deploy-prod`
   job `publish-image` builds, scans and pushes `energy-extractor:vX.Y.Z` and `:sha-<short sha>`
   before the approval; a fixable CRITICAL fails the release here.
6. **Approve:** `deploy-prod` waits for the `production` environment approval; approve, then check
   the bundle summary in the run log. `promote-image` then moves `:prod` to the release digest
   (see the run summary). A "modified remotely" failure: see
   [Dashboard modified remotely](#dashboard-modified-remotely).
7. **After deploy:** the next `daily-refresh` run (or *Run workflow*) is green; the prod dashboard
   shows the new day.
8. **Draft contracts:** if a new table now has data, set its contract to `status: active` (PATCH
   bump) in a follow-up PR.

## Run the extractor in Docker

The image (`Dockerfile`) runs only the extractor (`energy_lakehouse.extract_local`) and writes
raw JSON to `/data`. It holds no Databricks credentials and uploads nothing; uploading stays in
`scripts/sync_landing.ps1` / `sync_landing.sh`. Run from the repo root in PowerShell with Docker
Desktop running.

**1. Build.**

```powershell
docker build -t energy-extractor:dev --build-arg REVISION=$(git rev-parse HEAD) .
docker image ls energy-extractor:dev     # about 200 MB
```

**2. Check the CLI.** The ENTRYPOINT is the extractor, so options go straight after the image.

```powershell
docker run --rm energy-extractor:dev --help
```

**3. Extract into the local landing folder**, then upload as usual. The default command is
`--source all`; every option of `extract_local` works (`--weeks`, `--past-days`, `--mode
archive --start --end`, `--overwrite`, ...). `--out` defaults to `/data` in the container.

```powershell
New-Item -ItemType Directory -Force data/landing | Out-Null
docker run --rm -v "${PWD}/data/landing:/data" energy-extractor:dev --source all --weeks 8
"exit code: $LASTEXITCODE"               # 1 if any file failed (see the FAILED log lines)
.\scripts\sync_landing.ps1
```

Small test run into a throwaway folder:

```powershell
$out = Join-Path $env:TEMP "extract_test"
New-Item -ItemType Directory -Force $out | Out-Null
docker run --rm -v "${out}:/data" energy-extractor:dev --weeks 1 --refresh-latest 1 --past-days 1
(Get-ChildItem $out -Recurse -Filter *.json).Count   # > 0
```

**4. Lint and scan** (the same checks as the CI `docker` job):

```powershell
docker run --rm -v "${PWD}:/work:ro" -w /work ghcr.io/hadolint/hadolint:v2.15.0 hadolint Dockerfile
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock ghcr.io/aquasecurity/trivy:0.73.0 image --scanners vuln --severity CRITICAL --ignore-unfixed --exit-code 1 energy-extractor:dev
```

**Troubleshooting**

| Symptom | Fix |
|---|---|
| `429 Too Many Requests` from `registry-1.docker.io` during build | Docker Hub rate limit. Build with `--build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12.16-slim-trixie` (same tag as the Dockerfile), or `docker login`. |
| `Permission denied` writing `/data/...` (Linux host) | The container runs as uid 10001. Make the bind-mounted folder writable for it (`chmod 0777 <dir>`) or pass `--user "$(id -u):$(id -g)"`. Docker Desktop on Windows is not affected. |
| Trivy fails on a CRITICAL in a Python package | `uv lock --upgrade-package <name>`, rebuild, rescan, commit `uv.lock`. In the base OS: bump the patch tag of `PYTHON_IMAGE`. |
| Exit code 1, `FAILED` lines in the log | An API call failed after 3 tries. Rerun; existing files are skipped. |

## Extractor image in prod

`deploy-prod` publishes `ghcr.io/<owner>/energy-extractor:<tag>` (plus `:sha-<short sha>`) and,
only after a successful prod bundle deploy, points `:prod` at the same digest. `daily-refresh`
pulls `:prod`, logs the digest and labels, and runs that digest. Version tags are never
overwritten: rerunning `deploy-prod` for a published tag reuses its digest.

**Which image did prod run?** Open the `daily-refresh` run: the summary (and the step *Pull
extractor image and log its digest*) shows `ghcr.io/...@sha256:...`, the version label (= the
release tag) and the git revision. From the CLI:

```powershell
gh run list --workflow daily-refresh.yml --limit 5
gh run view <run-id> --log | Select-String "extractor:"
```

**Which version is `:prod` now?** Compare digests (any authenticated `docker login ghcr.io`,
see below):

```powershell
$image = "ghcr.io/tutul032/energy-extractor"
docker buildx imagetools inspect "${image}:prod" --format '{{.Manifest.Digest}}'
docker buildx imagetools inspect "${image}:v0.7.0" --format '{{.Manifest.Digest}}'
```

Or *GitHub → your profile → Packages → energy-extractor*: each version lists its tags.

**Roll back the extractor only** (bundle stays as deployed): retag `:prod` to the digest of an
older version tag. No rebuild; the next `daily-refresh` run uses it.

```powershell
gh auth refresh -s write:packages                  # once: token scope for pushing tags
gh auth token | docker login ghcr.io -u tutul032 --password-stdin
$image = "ghcr.io/tutul032/energy-extractor"
$digest = docker buildx imagetools inspect "${image}:v0.7.0" --format '{{.Manifest.Digest}}'
if ($LASTEXITCODE -ne 0) { throw "tag v0.7.0 not found" }
docker buildx imagetools create --tag "${image}:prod" "${image}@${digest}"
docker buildx imagetools inspect "${image}:prod" --format '{{.Manifest.Digest}}'   # = $digest
docker logout ghcr.io
```

**Roll back everything** (bundle + image): *Actions → deploy-prod → Run workflow* with the older
tag. `publish-image` reuses that tag's digest, `deploy` redeploys that code after approval, and
`promote-image` moves `:prod` back. A later release moves `:prod` forward again.

**Package access.** The first publish creates the package (private). Actions publishing with
`GITHUB_TOKEN` gives this repository access; check *Package settings → Manage Actions access*
lists `energy-lakehouse-de` (Read is enough for `daily-refresh`, Write for `deploy-prod`). A
`denied` / `unauthorized` on pull means this entry is missing.
