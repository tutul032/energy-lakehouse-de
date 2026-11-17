# Lab 5 — CI/CD from Dev to Staging to Prod: Pull Requests, Automatic Staging, Approved Production Releases

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 4 — Automated daily refresh. Next: Lab 6 — Data products and data contracts.

## 1. Goal

So far you deployed everything from your laptop into **dev**. In a real team, nobody deploys production from a laptop. Every change follows a fixed path, and machines check it on the way.

By the end of this lab:

- Every change goes through a **pull request (PR)**. CI runs lint, tests and a bundle validation for staging **and** prod before you can merge.
- Merging to the default branch **deploys staging automatically** and runs the job (pipeline + quality checks) as a test.
- **Prod** is deployed only from a **GitHub release**, and only after **your approval**.
- Staging and prod are deployed and owned by a **CI/CD service principal**. No human account owns production.
- The **daily refresh** from Lab 4 now feeds and runs **prod**.
- **Branch protection** makes it impossible to push to the default branch directly.
- **Guard tests** make it impossible to commit placeholders, non-UUID IDs or secrets into the bundle.

**Architecture**

```
feature branch ──PR──► ci.yml: ruff + pytest + bundle validate (staging, prod)
                              │ (merge only if green; branch is protected)
                              ▼
                        default branch (master/main)
                              │
                 deploy-staging.yml (automatic)
                              ▼
          staging: deploy as sp-energy-cicd → run job (pipeline + checks)
                              │
   gh release create vX.Y.Z ──► deploy-prod.yml ──► ⏸ approval ──► prod: deploy as sp-energy-cicd
                              │
             daily-refresh.yml (Lab 4) now uploads to energy_raw and runs the PROD job
```

**Time:** plan for a full day (6–8 hours). This lab has the most moving parts of Project 1. The troubleshooting section documents nine real problems from building it.

---

## 2. Concepts in plain words

### Environments

| Environment | Purpose | Who deploys | Where |
| --- | --- | --- | --- |
| **dev** | Your playground. Break things freely. | You, from the laptop (`-p free`) | Schema `energy_dev`, resources prefixed `[dev <your-name>]` |
| **staging** | A production-like copy. Every merged change is tested here first. | CI, automatically after merge | Schema `energy_staging`, prefix `[staging]` |
| **prod** | The "real" data that dashboards and users rely on | CI, only after a release + your approval | Schema `energy_prod`, prefix `[prod]` |

On Databricks Free Edition all three live in **one workspace** and are separated by **schema** and **name prefix**. In a company they'd usually be **separate workspaces**. The bundle code stays the same; only `host` changes per target.

### CI and CD

- **CI (Continuous Integration):** every proposed change is checked automatically (lint, tests, config validation) *before* it's merged.
- **CD (Continuous Delivery/Deployment):** merged changes are deployed automatically: to staging always, to prod after approval.

### Shared raw zone

Raw files are the same facts for every environment, so they live in **one neutral place**: `workspace.energy_raw.landing`. Each environment builds its **own** bronze/silver/gold from it. Without this, prod would read from a dev folder, which is bad architecture.

### Two service principals, two jobs

| Service principal | May do | May not do |
| --- | --- | --- |
| `sp-energy-refresh` (Lab 4) | Write files into the raw zone; start the prod job | Read tables, deploy anything |
| `sp-energy-cicd` (new) | Deploy staging and prod; own and run their jobs and pipelines; read the raw zone | Write to the raw zone |

Each identity can do exactly one job. If one secret leaks, the damage is limited.

### Releases and approvals

A **GitHub release** is a named version (e.g. `v0.1.1`) attached to a Git tag. Publishing a release triggers the prod workflow. That workflow uses a GitHub **environment** called `production` with a **required reviewer** (you), so nothing reaches prod until you click **Approve and deploy**.

### Branch protection

A **ruleset** on the default branch blocks direct pushes, requires a PR, and requires the `ci` check to pass before merging.

### Application ID vs. secret (read this twice!)

The service principal page shows two long strings:

| | Looks like | Is it secret? | Where it goes |
| --- | --- | --- | --- |
| **Application ID** | `7d0f1e32-4a86-42e2-b352-461dae1b3634` (UUID with dashes) | No | `databricks.yml`, GitHub secret `..._CLIENT_ID` |
| **OAuth secret** | `dose` + 32 hex characters, no dashes | **Yes** | **Only** GitHub secret `..._CLIENT_SECRET` and your password manager |

Mixing these up caused the longest debugging session of this lab (Troubleshooting P4).

---

## 3. Prerequisites

- Labs 0–4 completed. The daily refresh works in dev.
- `sp-energy-refresh` exists (Lab 4).
- Repo default branch: `master` or `main`. Check with:
  ```powershell
  gh repo view --json defaultBranchRef --jq .defaultBranchRef.name
  ```
  This tutorial writes `master`. Replace it with `main` if that's yours.

### Golden rules for this lab

1. **Never work on the default branch.** Before committing, run `git branch --show-current`. If it says `master`, create a branch first.
2. **Never deploy staging or prod from your laptop.** Only CI does that.
3. **Run local checks before every push:**
   ```powershell
   uv run ruff check . ; uv run ruff format --check . ; uv run pytest
   ```
4. **Read the log before changing code.** Use `gh run view <id> --log-failed`.
5. **Never paste a secret** into a file, a chat or a screenshot.

---

## 4. Part A — Environments

### Step 01 — Create the shared raw zone and copy the files

**What and why:** a neutral landing zone that every environment reads from.

SQL Editor:

```sql
CREATE SCHEMA IF NOT EXISTS workspace.energy_raw COMMENT 'Raw landing zone shared by all environments';
CREATE VOLUME IF NOT EXISTS workspace.energy_raw.landing;
```

PowerShell:

```powershell
databricks fs cp -r dbfs:/Volumes/workspace/energy_dev/landing/smard   dbfs:/Volumes/workspace/energy_raw/landing/smard   -p free
databricks fs cp -r dbfs:/Volumes/workspace/energy_dev/landing/weather dbfs:/Volumes/workspace/energy_raw/landing/weather -p free
```

Check:

```sql
LIST '/Volumes/workspace/energy_raw/landing/';
LIST '/Volumes/workspace/energy_raw/landing/smard/filter=410/resolution=hour/';
```

**Expected result:** `smard/` and `weather/` with no nested folders, and the same number of `filter=410` files as in the old dev landing.

---

### Step 02 — Let the refresh SP write to the raw zone

```sql
GRANT USE SCHEMA ON SCHEMA workspace.energy_raw TO `<refresh-sp-application-id>`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME workspace.energy_raw.landing TO `<refresh-sp-application-id>`;
SHOW GRANTS `<refresh-sp-application-id>` ON VOLUME workspace.energy_raw.landing;
```

**Expected result:** `READ VOLUME` and `WRITE VOLUME` are listed.

---

### Step 03 — Create the CI/CD service principal

**What and why:** a dedicated identity that deploys and owns staging and prod.

1. **Settings → Identity and access → Service principals → Manage → Add service principal → Add new**. Name: `sp-energy-cicd`.
2. Open it and copy the **Application ID** (UUID **with dashes**). Write it down as `<cicd-sp-application-id>`.
3. **Secrets** tab → **Generate secret** → Lifetime `729` → Scope **Other APIs**, then **All APIs** (or no scope) → **Generate**. Copy the secret (`dose…`) **into your password manager only**.

📸 `lab5-step03-cicd-sp.png` (blur the secret)

Now create the staging and prod schemas, and make the CI/CD SP their **owner**, so it can create pipeline tables there:

```sql
CREATE SCHEMA IF NOT EXISTS workspace.energy_staging;
CREATE SCHEMA IF NOT EXISTS workspace.energy_prod;
ALTER SCHEMA workspace.energy_staging OWNER TO `<cicd-sp-application-id>`;
ALTER SCHEMA workspace.energy_prod    OWNER TO `<cicd-sp-application-id>`;

GRANT USE CATALOG ON CATALOG workspace TO `<cicd-sp-application-id>`;
GRANT USE SCHEMA ON SCHEMA workspace.energy_raw TO `<cicd-sp-application-id>`;
GRANT READ VOLUME ON VOLUME workspace.energy_raw.landing TO `<cicd-sp-application-id>`;

-- you still want to read staging/prod results
GRANT USE SCHEMA, SELECT ON SCHEMA workspace.energy_staging TO `<your-email>`;
GRANT USE SCHEMA, SELECT ON SCHEMA workspace.energy_prod    TO `<your-email>`;

DESCRIBE SCHEMA EXTENDED workspace.energy_prod;
```

**Expected result:** `Owner` = the CI/CD Application ID.

---

### Step 04 — Test the CI/CD SP from your laptop (outside the repo)

**What and why:** prove the credentials work, and record **which identity** they belong to. This check would have prevented Troubleshooting P4.

```powershell
Remove-Item Env:DATABRICKS_* -ErrorAction SilentlyContinue
cd $HOME
$env:DATABRICKS_HOST = "https://dbc-xxxxxxxx-xxxx.cloud.databricks.com"
$env:DATABRICKS_CLIENT_ID = "<cicd-sp-application-id>"
$env:DATABRICKS_CLIENT_SECRET = "<cicd-secret>"
databricks current-user me -o json | ConvertFrom-Json | Select-Object userName, displayName
databricks fs ls dbfs:/Volumes/workspace/energy_raw/landing
Remove-Item Env:DATABRICKS_* -ErrorAction SilentlyContinue
cd C:\dev\energy-lakehouse-de
```

**Expected result:**

```
userName                             displayName
--------                             -----------
7d0f1e32-4a86-42e2-b352-461dae1b3634 sp-energy-cicd
```

followed by `smard` and `weather`. The `userName` is the value that must appear as `cicd_sp_app_id` in `databricks.yml`. It's a UUID with dashes, never a `dose…` string.

---

### Step 05 — Update the bundle

**What and why:** staging and prod targets, the shared raw zone, private deployment folders, and unique resource names. This prompt already contains every fix we needed after the first attempt (see Troubleshooting P1, P3, P7 and the Shared-folder warning).

Create a branch first (golden rule 1):

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c feat/environments
```

**Option A — Claude Code prompt:**

```text
Read CLAUDE.md first. Implement staging/prod environments. Do not deploy.

Context (Free Edition, one workspace): raw files live in a shared volume
/Volumes/workspace/energy_raw/landing/{smard,weather}. Each target builds its own tables in its schema
(energy_dev, energy_staging, energy_prod). Schemas energy_staging/energy_prod already exist and are owned by
the CI/CD service principal. CI deploys staging and prod AS the CI/CD service principal.

1. databricks.yml:
   - top-level variables with real defaults (no placeholders):
       landing_root       = /Volumes/workspace/energy_raw/landing
       cicd_sp_app_id     = <cicd-sp-application-id>      (UUID with dashes)
       refresh_sp_app_id  = <refresh-sp-application-id>   (UUID with dashes)
       alert_email        = <your-email>
   - pipeline configuration landing_path = ${var.landing_root}/smard,
     weather_landing_path = ${var.landing_root}/weather (all targets).
   - workspace: host only, never profile.
   - target dev: unchanged (mode development, my user).
   - targets staging and prod: mode production, schema energy_staging / energy_prod,
     root_path /Workspace/Users/${workspace.current_user.userName}/.bundle/${bundle.name}/${bundle.target}
     (private to the deploying identity; NOT /Workspace/Shared),
     presets.name_prefix "[staging] " / "[prod] " (all environments share one workspace, so names must differ),
     NO run_as (CI deploys as the cicd SP, which then owns and runs the resources; add a comment explaining why),
     top-level permissions: service_principal_name ${var.cicd_sp_app_id} CAN_MANAGE,
     user_name ${var.alert_email} CAN_MANAGE.
2. resources/daily_refresh_job.yml: the refresh SP gets CAN_MANAGE_RUN in all targets.
   Quality checks use the target's catalog/schema.
3. src/pipelines/bronze.py and bronze_weather.py: add .option("cloudFiles.partitionColumns", "") —
   partition columns are already derived from _metadata.file_path, and implicit inference breaks when the
   landing path changes.
4. Notebooks/scripts that write to the landing zone (01/02 extract notebooks, sync_landing.ps1, sync_landing.sh):
   default target /Volumes/workspace/energy_raw/landing. Remove per-schema CREATE VOLUME for landing.
5. tests/test_bundle_config.py — fail if databricks.yml or resources/*.yml:
   a) contain "REPLACE_WITH" or empty SP ids,
   b) have cicd_sp_app_id / refresh_sp_app_id values that are not UUIDs
      (^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$),
   and fail if any tracked file (git ls-files) contains r"\bdose[0-9a-f]{32}\b" (Databricks OAuth secret format).
   Print only file name and line number, never the matched value.
6. Update CLAUDE.md: environment table (target → schema → name prefix → identity → root path → who deploys),
   and the rule "staging/prod are deployed only by CI as the cicd SP".
List changed files and checkpoint commands.
```

**Option B:** copy from my repo at tag `lab5-complete`.

**Check that no placeholder or secret slipped in:**

```powershell
Select-String -Path databricks.yml, resources\*.yml -Pattern "REPLACE_WITH|dose"
Select-String -Path databricks.yml -Pattern "cicd_sp_app_id|refresh_sp_app_id"
```

**Expected:** the first command prints **nothing**. The second shows UUIDs with dashes.

---

### Step 06 — Validate locally and rebuild dev once

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run pytest
databricks bundle validate -t dev -p free
databricks bundle validate -t staging -p free
databricks bundle validate -t prod -p free
databricks bundle deploy -t dev -p free
databricks bundle run energy_pipeline -t dev -p free --full-refresh-all
databricks bundle run daily_refresh_job -t dev -p free
```

**Expected result:**
- all tests pass, including the guard tests
- `Validation OK!` three times, **without** the warning *"permissions section should explicitly include the current deployment identity"* (in CI that warning means the configured SP ID doesn't match the deployer; see P4)
- dev pipeline `COMPLETED`, job tasks `SUCCESS`

**Why `--full-refresh-all` in dev?** Dev's bronze streaming tables were built from the old landing path. Their Auto Loader checkpoints still remember it, and Auto Loader identifies files by full path. A full refresh resets the checkpoints and rebuilds dev from `energy_raw` once (Troubleshooting P1). Staging and prod start empty, so they don't need it. **Never** use `--full-refresh-all` on prod casually.

**Check that dev now reads only from the raw zone:**

```sql
SELECT DISTINCT regexp_extract(source_file, '^(/Volumes/[^/]+/[^/]+/[^/]+)', 1) AS volume
FROM workspace.energy_dev.bronze_smard_raw;
```

**Expected:** only `/Volumes/workspace/energy_raw/landing`.

Commit and push the branch, but don't open the PR yet:

```powershell
git branch --show-current          # must NOT be master
git add -A ; git commit -m "feat: shared raw zone, staging/prod targets deployed by cicd SP"
git push -u origin feat/environments
```

---

## 5. Part B — GitHub automation

### Step 07 — Secrets, production environment, branch protection

**Secrets:**

```powershell
gh secret set DATABRICKS_CICD_CLIENT_ID --body "<cicd-sp-application-id>"
gh secret set DATABRICKS_CICD_CLIENT_SECRET      # paste the dose… secret when prompted (hidden)
gh secret list
```

**Expected:** 5 secrets: `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` (refresh SP, Lab 4), plus the two CICD ones.

**Production environment with approval:**

Repo **Settings → Environments → New environment** → name `production` → enable **Required reviewers** → add yourself → turn **off** "Prevent self-review" → **Save protection rules**.

**Branch protection (ruleset):**

Repo **Settings → Rules → Rulesets → New ruleset → New branch ruleset**:

| Field | Value | Why |
| --- | --- | --- |
| Ruleset name | `protect-master` | |
| Enforcement status | **Active** | "Evaluate" only logs |
| Bypass list | **empty** | If you add yourself, your direct pushes still pass |
| Target branches | **Include default branch** | |
| Restrict deletions | ✅ | |
| Require a pull request before merging | ✅, **Required approvals: 0** | Solo developer: a PR is required, but you can merge it yourself |
| Require status checks to pass | ✅ → add `ci` | No merge without green CI |
| Block force pushes | ✅ | History can't be rewritten |

If `ci` isn't offered yet, finish Steps 08–09 first (the check appears after its first run), then come back and add it.

📸 `lab5-step07-environment.png`, `lab5-step07-ruleset.png`

---

### Step 08 — Generate the workflows

**Option A — Claude Code prompt** (on the same branch):

```text
Create/adjust GitHub Actions for CI/CD. Auth for deploys: secrets DATABRICKS_HOST,
DATABRICKS_CICD_CLIENT_ID, DATABRICKS_CICD_CLIENT_SECRET mapped to env DATABRICKS_HOST/CLIENT_ID/CLIENT_SECRET.
Use databricks/setup-cli and astral-sh/setup-uv. No profiles.

1. .github/workflows/ci.yml — on pull_request and push to the default branch:
   job `ci`: uv sync --locked, ruff check, ruff format --check, pytest;
   then `databricks bundle validate -t staging` and `-t prod` (with cicd SP env).
2. .github/workflows/deploy-staging.yml — on push to the default branch and workflow_dispatch:
   job `ci` (reuse ci.yml via workflow_call), then job `deploy` (needs ci):
   `databricks bundle deploy -t staging`, `databricks bundle run daily_refresh_job -t staging`.
   concurrency group deploy-staging.
3. .github/workflows/deploy-prod.yml — on release types [published] and workflow_dispatch:
   environment: production (requires approval), checkout the release tag,
   `databricks bundle deploy -t prod`; do NOT run the job (the daily refresh does).
   Print `databricks bundle summary -t prod` at the end.
4. .github/workflows/daily-refresh.yml — upload to /Volumes/workspace/energy_raw/landing, then run
   `databricks jobs run-now ${{ vars.DATABRICKS_JOB_ID }}` (will point to the prod job). Keep the refresh SP secrets.
5. README: CI/CD section with the flow diagram and badges for ci, deploy-staging, daily-refresh.
List changed files.
```

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run pytest
git add -A ; git commit -m "ci: PR checks, staging on merge, prod on release with approval"
git push
```

---

### Step 09 — Pull request → CI → merge → staging

```powershell
git branch --show-current                 # feat/environments
gh pr create --fill --base master
gh pr checks --watch
```

**Expected result:** a PR URL, then:

```
✓  ci/ci (pull_request)
```

Merge, then bring your local default branch in sync:

```powershell
gh pr merge --squash --delete-branch
git switch master
git fetch origin
git reset --hard origin/master
```

Watch the staging deploy. **Start watching right away:** `gh run watch` only follows runs that are still in progress.

```powershell
gh run list --workflow deploy-staging.yml --limit 1
gh run watch
```

**Expected result:**

```
✓ master deploy-staging
JOBS
✓ ci / ci
  ✓ Run uv run pytest
  ✓ Validate bundle (staging)
  ✓ Validate bundle (prod)
✓ deploy
  ✓ Deploy bundle (staging)
  ✓ Run daily_refresh_job (staging)
✓ Run deploy-staging completed with 'success'
```

If the run already finished, `gh run watch` says *"found no in progress runs to watch"*. That's not an error; use `gh run list` instead.

**Verify staging data:**

```sql
SELECT count(*) AS hours, max(hour_utc) AS latest FROM workspace.energy_staging.gold_energy_hourly;
```

**Expected:** ~1,500+ hours, `latest` ≈ yesterday. In **Jobs & Pipelines**: `[staging] energy_pipeline` and `[staging] daily_refresh_job`, owned by the CI/CD SP.

📸 `lab5-step09-staging-green.png`

---

### Step 10 — Release → approval → prod

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
gh release create v0.1.0 --target master --generate-notes --title "v0.1.0 - first production release"
gh run list --workflow deploy-prod.yml --limit 1
```

**Expected:** a `deploy-prod` run with status **waiting**.

In the browser: **Actions → deploy-prod → the waiting run → Review deployments** → tick `production` → **Approve and deploy**.

Then (if the run is still in progress):

```powershell
gh run watch
```

**Expected result:** `Deploy bundle (prod)` ✓, and the summary lists `[prod] energy_pipeline` and `[prod] daily_refresh_job`. The run summary page shows *"tutul032 approved — production"* under **Deployment protection rules**.

📸 `lab5-step10-approval.png`

**If a release fails:** don't move or reuse the tag. Fix forward with a **new version** (`v0.1.1`). Releases are history.

---

### Step 11 — Point the daily refresh at prod

```powershell
databricks jobs list -p free | Select-String "\[prod\] daily_refresh_job"
gh variable set DATABRICKS_JOB_ID --body "<prod-job-id>"
gh workflow run daily-refresh.yml
```

Wait ~10 seconds (so the run exists), then:

```powershell
gh run watch
```

**Expected:** all steps green: extract → upload to `energy_raw` → `[prod] daily_refresh_job`. The first prod run takes longer, because it processes all raw files.

```sql
SELECT count(*) AS hours, max(hour_utc) AS latest FROM workspace.energy_prod.gold_energy_hourly;
SELECT count(*) AS hours_with_weather FROM workspace.energy_prod.gold_energy_weather_hourly
WHERE wind_speed_100m_onshore_avg_ms IS NOT NULL;
```

**Expected:** ~1,500+ hours, with weather for almost all of them.

📸 `lab5-step11-prod-data.png`

---

### Step 12 — Prove branch protection

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git commit --allow-empty -m "test: direct push (should be rejected)"
git push
```

**Expected result:**

```
remote: error: GH013: Repository rule violations found for refs/heads/master.
remote: - Changes must be made through a pull request.
! [remote rejected] master -> master (push declined due to repository rule violations)
```

Undo the local test commit:

```powershell
git reset --hard origin/master
```

If the push **succeeds**, the ruleset isn't active (Troubleshooting P9).

📸 `lab5-step12-push-rejected.png`

---

### Step 13 — Clean up, rotate secrets, tag

**Remove leftovers from earlier attempts:**

```powershell
databricks workspace delete /Workspace/Shared/.bundle/energy-lakehouse-de --recursive -p free
```

**Expected:** no output, or *does not exist*.

**Rotate the CI/CD secret** (mandatory if it was ever exposed, recommended anyway after setup):
1. `sp-energy-cicd` → **Secrets** → **Generate secret** → copy it
2. `gh secret set DATABRICKS_CICD_CLIENT_SECRET` (paste)
3. `gh workflow run deploy-staging.yml`, wait ~10 s, `gh run watch` → ✓
4. Only then **delete the old secret** on the Secrets tab
5. Record the new expiry date in your runbook

**Tag:**

```powershell
git fetch origin
git tag lab5-complete origin/master
git push origin lab5-complete
```

---

## 6. Checkpoint

1. A PR shows the `ci` check, and merging is blocked until it's green.
2. Merging deploys staging automatically. `[staging] daily_refresh_job` runs green as `sp-energy-cicd`.
3. A published release waits for approval, then deploys prod.
4. `energy_prod` gold tables contain data, delivered by the daily refresh.
5. A direct push to the default branch is rejected (GH013).
6. `uv run pytest` includes guard tests for placeholders, UUIDs and secret-shaped strings.
7. Dev bronze reads only from `/Volumes/workspace/energy_raw/landing`.
8. No `/Workspace/Shared/.bundle/energy-lakehouse-de` folder remains.

---

## 7. Troubleshooting — every problem I actually hit

These are listed in the order they happened. The pattern repeats: **read the exact error → compare what's configured with what's real → fix the cause, not the symptom → add a test so it can't come back.**

### How to read logs (needed for every problem below)

```powershell
# latest run of a workflow
$id = gh run list --workflow <workflow>.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $id --log-failed
```

`gh run view --log-failed` **without** an ID sometimes prints nothing, so always pass the ID. For pipeline errors, open the pipeline (`databricks bundle open energy_pipeline -t dev -p free`), pick the failed run and click the red node.

---

### P1 — Dev pipeline failed after moving the landing zone: `CF_PARTITON_INFERENCE_ERROR`

**Symptom:** the dev job failed after ~17 minutes:

```
"[dev rezaul_tutul] daily_refresh_job" INTERNAL_ERROR FAILED Task run_pipeline failed ...
```

The pipeline event log showed, for **both** bronze tables:

```
[CF_PARTITON_INFERENCE_ERROR] There was an error when trying to infer the partition schema of the current batch of files.
Please provide your partition columns explicitly by using: .option("cloudFiles.partitionColumns", "{comma-separated-list}")
...
CloudFilesSource[/Volumes/workspace/energy_raw/landing/smard]: { ... "lastInputPath":"/Volumes/workspace/energy_dev/landing/smard" ... }
```

Silver and gold were only *"SKIPPED due to upstream failure(s)"*. A second message (*"overlaps with managed storage within 'CheckPathAccess'"*) was a follow-on effect, not a separate permission problem.

**Cause:** the streaming source now pointed at `energy_raw`, but its **checkpoint** was created for `energy_dev/landing` (`lastInputPath`). Auto Loader infers Hive-style partition columns (`filter=…`, `location=…`) relative to the base path it remembers, so files under a different base path don't fit.

**Fix:**
1. Disable implicit partition inference (the columns already come from `_metadata.file_path`):
   ```python
   .option("cloudFiles.partitionColumns", "")
   ```
2. Reset dev's checkpoints:
   ```powershell
   databricks bundle deploy -t dev -p free
   databricks bundle run energy_pipeline -t dev -p free --full-refresh-all
   ```

**Lesson:** a streaming checkpoint is bound to its source. When the source location changes, plan a checkpoint reset. Don't rely on implicit inference from folder names.

---

### P2 — Deploy failed on an unfilled placeholder

**Symptom:**

```
Error: cannot create resources.jobs.daily_refresh_job.permissions:
Principal: ServicePrincipalName(REPLACE_WITH_SP_APPLICATION_ID) does not exist (404 RESOURCE_DOES_NOT_EXIST)
```

**Cause:** generated config contained placeholders (`REPLACE_WITH_SP_APPLICATION_ID`, `REPLACE_WITH_CICD_SP_APPLICATION_ID`, `REPLACE_WITH_ALERT_EMAIL`) for all three targets, and they were never replaced.

**Fix:**
1. Find them: `Select-String -Path databricks.yml, resources\*.yml -Pattern "REPLACE_WITH"`
2. Replace them. In VS Code, use **Ctrl+H** and **save** (an unsaved tab shows ● instead of ✕). Or deterministically from PowerShell (writes UTF-8 without BOM):
   ```powershell
   $p = "C:\dev\energy-lakehouse-de\databricks.yml"
   $t = [IO.File]::ReadAllText($p)
   $t = $t.Replace("REPLACE_WITH_CICD_SP_APPLICATION_ID", "<cicd-sp-application-id>").Replace("REPLACE_WITH_SP_APPLICATION_ID", "<refresh-sp-application-id>").Replace("REPLACE_WITH_ALERT_EMAIL", "<your-email>")
   [IO.File]::WriteAllText($p, $t, (New-Object System.Text.UTF8Encoding $false))
   ```
   Replace the **longer** placeholder first, so it can't be partially matched by the shorter one.
3. Add a guard test that fails on `REPLACE_WITH`. The test later caught exactly this in CI:
   ```
   FAILED tests/test_bundle_config.py::test_bundle_file_has_no_unfilled_placeholders[databricks.yml]
     line 39: placeholder: cicd_sp_app_id: REPLACE_WITH_CICD_SP_APPLICATION_ID
     ...
   ```

**Pitfall:** after the first "fix", the local test still failed with the same line numbers. The edit had never been saved to disk. Check with `(Get-Item databricks.yml).LastWriteTime`.

**Better design:** set each value **once** as a top-level variable `default` instead of repeating it in every target.

---

### P3 — Staging deploy: `'***' cannot be set as run_as service principal, because it doesn't exist`

**Symptom (CI log):**

```
Error: cannot create resources.pipelines.energy_pipeline: '***' cannot be set as run_as service principal,
because it doesn't exist. (400 INVALID_PARAMETER_VALUE)
```

The same error appeared for every job.

**What I tried:**
- **Fix A:** grant `sp-energy-cicd` the role *Service principal: User* on itself → **still failed**
- **Fix B:** remove `run_as` from staging and prod → **worked**

**Why removing `run_as` is correct here:** CI deploys **as** the CI/CD SP, so it becomes the **owner** of the jobs and pipelines, and owners run them. `run_as` only matters when a *human* deploys prod and the resources should still run as an SP, which our process forbids. (Part of the original error was also P4: the configured ID was wrong.)

**Lesson:** don't add configuration you don't need. Every extra setting is a new way to fail.

---

### P4 — Application ID vs. secret: the root cause of three "does not exist" errors

**Symptoms:** after P3, the next deploy failed again:

```
Recommendation: permissions section should explicitly include the current deployment identity '***' ...
Error: Principal: ServicePrincipalName(***) does not exist (404 RESOURCE_DOES_NOT_EXIST)
Endpoint: PUT ***/api/2.0/permissions/directories/613721670631422
```

Two clues:
1. The **recommendation** still appeared, although the CI/CD SP *was* in the permissions block. The CLI only says this when the configured ID ≠ the deploying identity.
2. GitHub showed the principal as `***`. **GitHub masks any value that equals a secret.** So the "SP ID" in the config was identical to a secret.

**Investigation (locally, where nothing is masked):**

```powershell
Select-String -Path databricks.yml -Pattern "cicd_sp_app_id|refresh_sp_app_id"
#   cicd_sp_app_id: dose7688…            ← a secret, not an ID!
#   refresh_sp_app_id: e1370f9f-0819-48ba-92f0-365c18361518

# which identity do the CI/CD credentials really belong to?
databricks current-user me -o json | ConvertFrom-Json | Select-Object userName, displayName   # (with CI/CD env vars, outside the repo)
#   7d0f1e32-4a86-42e2-b352-461dae1b3634  sp-energy-cicd
```

**Cause:** the CI/CD SP's **OAuth secret** (`dose…`) had been pasted where its **Application ID** (UUID) belonged. That single mistake explains `run_as … doesn't exist` (P3), the permissions 404, and the recommendation. It also **leaked the secret into a public repo**.

**Fix:**
1. **Revoke the leaked secret** (SP → Secrets → delete → generate new → `gh secret set DATABRICKS_CICD_CLIENT_SECRET`). After revocation, the old value in Git history is worthless, so there's no need to rewrite history.
2. Replace the secret with the Application ID in `databricks.yml` (branch + PR).
3. Add guard tests: SP IDs must be UUIDs, and no tracked file may contain `dose[0-9a-f]{32}`.

After the fix: `Validation OK!` **without** the recommendation, and staging deployed ✓.

**Lessons:**
- **IDs have dashes, secrets start with `dose`.** Check before you paste.
- `***` in a GitHub log means "this equals a secret". If an *ID* shows up as `***`, something is wrong.
- Verify identities with `databricks current-user me` instead of trusting what you copied.

---

### P5 — "head branch master is the same as base branch master"

**Symptom:**

```
head branch "master" is the same as base branch "master", cannot create a pull request
no pull requests found for branch "master"
```

**Cause:** I was still on `master`. The `git switch -c fix/…` step had failed silently, because the branch already existed:

```
fatal: a branch named 'fix/bundle-permissions' already exists
```

**Fix:**

```powershell
git switch fix/bundle-permissions      # use the existing branch
git status ; git log --oneline -3      # is the fix committed? pushed?
gh pr create --fill --base master
```

If commits accidentally landed on local `master`, move them: `git switch -c <branch>` (takes the commits along), then `git branch -f master origin/master`.

**Lesson:** run `git branch --show-current` before every commit and every `gh pr create`.

---

### P6 — Release created, but `deploy-prod` never ran

**Symptom:**

```
gh release create v0.1.0 ...
HTTP 422: Validation Failed — Release.tag_name already exists
gh run list --workflow deploy-prod.yml --limit 1
no runs found
```

**Cause:** `v0.1.0` had been created earlier, on a commit **before** `deploy-prod.yml` existed. For release events, GitHub runs the workflow file **from the tagged commit**, so no workflow existed to run.

**Diagnosis:**

```powershell
gh release view v0.1.0 --json tagName,isDraft,createdAt,publishedAt
git show v0.1.0:.github/workflows/deploy-prod.yml     # "path does not exist" → this cause
```

(If `isDraft` is `true`: drafts don't fire `release: published`. Publish with `gh release edit v0.1.0 --draft=false`.)

**Fix:** the release had never reached prod, so delete and recreate it on the current commit:

```powershell
gh release delete v0.1.0 --cleanup-tag --yes
git fetch --prune --prune-tags origin
gh release create v0.1.0 --target master --generate-notes --title "v0.1.0 - first production release"
```

Fallback to start it manually: `gh workflow run deploy-prod.yml --ref v0.1.0`.

---

### P7 — Prod deploy: pipeline name already used

**Symptom:** the approval worked, then:

```
Error: cannot create resources.pipelines.energy_pipeline: The pipeline name 'energy_pipeline' is already used
by another pipeline. (409 RESOURCE_CONFLICT)
```

**Cause:** all environments share **one workspace**. `mode: development` prefixes dev resources (`[dev <name>]`), but `mode: production` adds no prefix. Staging and prod both wanted `energy_pipeline`, and staging was deployed first. Pipeline names must be unique per workspace. (Jobs allow duplicates, which is why three identically named `daily_refresh_job` entries existed without an error, but it was confusing.)

**Fix:** a name prefix per target:

```yaml
targets:
  staging:
    presets:
      name_prefix: "[staging] "
  prod:
    presets:
      name_prefix: "[prod] "
```

Merging this renamed the staging resources in place (the tables stayed). Then a **new** release `v0.1.1` deployed prod. The failed `v0.1.0` wasn't moved: fix forward.

**Bonus:** with prefixes, finding the right job ID is trivial: `databricks jobs list -p free | Select-String "\[prod\]"`.

---

### P8 — `gh run watch`: "found no in progress runs to watch"

**Cause:** the run had already finished. `gh run watch` only follows runs in progress.
**Fix:** `gh run list --workflow <file>.yml --limit 1` to see the result. Next time, start `gh run watch` ~10 seconds after triggering the run.

---

### P9 — A direct push to `master` succeeded

**Symptom:**

```
git commit --allow-empty -m "test: direct push" ; git push
   f493db2..1baaddd  master -> master
```

**Cause:** branch protection hadn't been set up yet.
**Fix:** create the ruleset (Step 07): require PR (0 approvals), require `ci`, block force pushes, **empty bypass list**. The retest was rejected with `GH013`. The empty test commit is harmless and can stay.

---

### Warnings worth acting on

**`/Workspace/Shared` is writable by all users**

```
Warning: the bundle root path /Workspace/Shared/.bundle/energy-lakehouse-de/staging is writable by all workspace users
```

Anyone could modify deployed prod code. **Fix:** `root_path: /Workspace/Users/${workspace.current_user.userName}/.bundle/${bundle.name}/${bundle.target}`. In CI the current user is the CI/CD SP, so the folder is private to it.

**Moving `root_path` creates a new deployment.** The old one keeps owning its pipeline and tables, so remove it first, **as the identity that deployed it**, from the commit that still has the old path:

```powershell
$env:DATABRICKS_HOST = "https://dbc-xxxxxxxx-xxxx.cloud.databricks.com"
$env:DATABRICKS_CLIENT_ID = "<cicd-sp-application-id>"
$env:DATABRICKS_CLIENT_SECRET = "<cicd-secret>"
databricks bundle destroy -t staging --auto-approve
Remove-Item Env:DATABRICKS_* -ErrorAction SilentlyContinue
```

(In my case all earlier staging deploys had failed before creating resources, so only the leftover files had to be deleted.)

**Node.js 20 / Ubuntu annotations:** warnings, not errors. Update `actions/checkout` and `astral-sh/setup-uv` to their newest major versions in a small "chore" PR.

---

### Other problems you may hit

| Problem | Cause | Fix |
| --- | --- | --- |
| `invalid_client` in CI | Wrong secret in `DATABRICKS_CICD_CLIENT_SECRET`, or ID/secret from different SPs | Test the pair locally outside the repo (Step 04), then `gh secret set` again |
| `PERMISSION_DENIED` creating tables in `energy_prod` | Schema not owned by the CI/CD SP | `ALTER SCHEMA workspace.energy_prod OWNER TO \`<cicd-sp-application-id>\`` |
| Staging/prod pipeline can't read raw files | CI/CD SP lacks `READ VOLUME` on `energy_raw.landing` | Step 03 grants |
| Daily refresh upload fails after the move | Refresh SP lacks `WRITE VOLUME` on `energy_raw.landing`, or the script still targets the old path | Step 02 grants; check `sync_landing.sh` |
| Daily refresh runs the wrong job | `DATABRICKS_JOB_ID` still points to dev or staging | `databricks jobs list -p free \| Select-String "\[prod\]"`, then `gh variable set` |
| `ci` check not selectable in the ruleset | The check has never run | Open a PR first, then add the check |
| Can't merge your own PR | Ruleset requires ≥1 approval | Set **Required approvals: 0** (solo) |
| `deploy-prod` never asks for approval | Workflow missing `environment: production`, or environment has no reviewer | Check the YAML and Settings → Environments |
| Prod data duplicated after a raw-path change | Checkpoints bound to the old path (P1) | Plan a controlled full refresh, and announce it, since it's prod |

---

## 8. What you learned

- **Environment strategy:** dev / staging / prod with one bundle, separated by schema, name prefix and identity
- A **shared raw zone** with per-environment curated layers
- **CI:** lint, tests and bundle validation on every PR
- **CD:** automatic staging deploys, release-based prod deploys with a **human approval gate**
- **Service-principal ownership** of production, and why `run_as` was unnecessary
- **Branch protection** with rulesets, set up for a solo developer
- **Shift-left guard tests** that reject placeholders, non-UUID IDs and secret-shaped strings
- **Streaming checkpoints** and why moving a source path needs a planned reset
- **Release hygiene:** a release runs the workflow from its tagged commit; fix forward with a new version
- **Secret hygiene:** IDs vs. secrets, GitHub masking (`***`) as a diagnostic clue, rotation after exposure
- A repeatable **debugging method:** exact error → compare configured vs. real → fix the cause → add a test

**Interview-ready summary:** *"I built a three-environment CI/CD flow on Databricks Asset Bundles: PRs run lint, tests and bundle validation; merges auto-deploy staging and run the pipeline with quality checks; production deploys only from a GitHub release after an approval gate, owned by a dedicated service principal. Along the way I debugged streaming-checkpoint path binding, resource-name collisions in a shared workspace, and a config where an OAuth secret had been used as an Application ID. I fixed it, rotated the secret, and added guard tests so CI rejects secret-shaped strings and non-UUID IDs."*

---

## 9. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-21 |
| OS | Windows 11 (local), ubuntu-latest (GitHub runners) |
| Databricks | Free Edition (serverless), one workspace for dev/staging/prod |
| Databricks CLI | `<output of databricks -v>` |
| uv | `<output of uv --version>` |
| GitHub CLI | `<output of gh --version>` |
| Tests | 113 passing |
| Releases | v0.1.0 (failed: name collision), v0.1.1 (first prod deployment) |
| CI/CD SP secret expiry | `<date>` (runbook) |

**Next:** Lab 6 — Data products and data contracts: a contract per gold table validated in CI, Unity Catalog grants, tags and comments, and freshness checks via system tables.
