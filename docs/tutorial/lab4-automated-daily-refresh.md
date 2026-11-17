# Lab 4 — Automated Daily Refresh: Service Principal, Lakeflow Job and GitHub Actions

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 3 — Weather data. Next: Lab 5 — CI/CD from dev to staging to prod.

## 1. Goal

Until now, fresh data only arrived when you ran a script on your laptop. In production, nobody presses a button. In this lab the whole flow runs **every day on its own**:

- A **service principal** (a technical, non-human identity) with only the permissions it needs
- A **Lakeflow Job** that runs the pipeline and then **quality checks** that fail loudly, and emails you on failure
- A **GitHub Actions** workflow on a daily schedule: extract SMARD + weather → upload to the landing volume → start the job
- A manual "Run workflow" button for testing

**Architecture**

```
GitHub Actions (daily 05:15 UTC + manual trigger)
   ├─ extract SMARD + weather        (the GitHub runner has internet)
   ├─ upload to /Volumes/workspace/energy_dev/landing/   ← authenticated as the service principal
   └─ start Lakeflow Job "daily_refresh_job"
         ├─ task 1: energy_pipeline   (bronze → silver → gold)
         └─ task 2: quality_checks    (freshness + row counts; raises an error if not OK)
               └─ email on failure
```

**Time:** about 4–6 hours, plus one scheduled run the next morning.

---

## 2. Concepts in plain words

### Why does GitHub download the data instead of Databricks?

Databricks Free Edition has **no outbound internet**: a Databricks job can't call SMARD or Open-Meteo. A GitHub Actions runner is a short-lived cloud machine *with* internet, so it does the download and drops the files into the landing zone. Databricks then processes them.

| | Free Edition (this lab) | Paid workspace with outbound internet |
| --- | --- | --- |
| Who downloads | GitHub Actions runner | A Databricks job task (the notebooks `01_extract_smard` / `02_extract_weather` already exist) |
| Who schedules | GitHub cron | Databricks job schedule |
| Who processes | Databricks pipeline | Databricks pipeline |

Both are real-world patterns. External orchestration that drops files into a landing zone is very common (e.g. with Azure Data Factory or Airflow).

### What is a service principal?

A **service principal (SP)** is an identity for software, not for a person. Automation should never use *your* login: if you leave the company, change your password or enable MFA, the automation would break, and every automated action would look like it was done by you. The SP gets **only** the rights it needs. Here that's uploading files and starting one job.

### How does the SP authenticate?

There are two options:

| Option | How it works | Available here? |
| --- | --- | --- |
| **OIDC / workload identity federation** | GitHub proves "I am workflow X of repo Y" with a short-lived token. Databricks trusts it through a **federation policy**. No secret is stored anywhere. | ❌ Creating the policy needs an **account-level** command (`databricks account service-principal-federation-policy create ...`), and Free Edition has no account console or account-level APIs |
| **OAuth secret (M2M)** | The SP gets a client ID and a secret, stored as encrypted **GitHub secrets** | ✅ Used in this lab |

OIDC is the better practice. We'll use it in the AWS chapter, where a trial account provides account-level access.

### Lakeflow Job

A **job** chains tasks with dependencies (here: pipeline, then checks), with retries, notifications and run history. In Lab 2 you started the pipeline directly. Now the job wraps it, and adds a check that fails loudly when data is stale or missing.

### Least privilege

The SP may **write files to the landing volume** and **run this one job**. It can't read any table. Even if the secret leaked, the damage would be limited.

---

## 3. Prerequisites

- Labs 0–3 completed. `scripts/sync_landing.ps1 -RunPipeline` works.
- GitHub CLI logged in (`gh auth status`)
- VS Code open in `C:\dev\energy-lakehouse-de`

---

## 4. Steps

### Step 01 — Create the service principal

**What and why:** a dedicated identity for the automation.

**Click path:** profile icon → **Settings** → **Identity and access** → **Service principals** → **Manage** → **Add service principal** → **Add new service principal to account and workspace**. Name: `sp-energy-refresh`.

**Expected result:** the SP appears in the list. Open it and copy the **Application ID** (a UUID like `e1370f9f-…`). That's the client ID. The SP page may also show a separate numeric **ID**. Don't use that one for login.

📸 `lab4-step01-sp-created.png`

---

### Step 02 — Generate an OAuth secret

**What and why:** the "password" the SP uses to log in from GitHub.

**Click path:** on the SP's page → **Secrets** tab → **Generate secret**.

In the dialog:

| Field | Value | Why |
| --- | --- | --- |
| Lifetime (days) | 729 (maximum 730) | Fewer rotations. Write the **expiry date** in your runbook. |
| Scope | **Other APIs**, then **All APIs** if offered, otherwise leave "No scopes selected" | "BI Tools" is for SQL clients like Power BI. A secret without scopes keeps all-apis access by default. |

**Why not narrow scopes?** The Databricks CLI and SDKs request the `all-apis` scope when they authenticate, so a narrowly scoped secret breaks CLI login. Least privilege is enforced instead through **Unity Catalog grants** (Step 03) and **job permissions** (Step 05).

**Expected result:** a secret starting with `dose`, **36 characters** long. **It's shown only once.** Copy it with the copy button and store it in a password manager.

⚠️ **Never paste the secret into a chat, a ticket, a screenshot or a commit.** If that happens, delete it on the Secrets tab and generate a new one (see Troubleshooting P2).

📸 `lab4-step02-generate-secret.png` (blur the secret)

---

### Step 03 — Grant minimal permissions

**What and why:** the SP must be able to reach the volume, and nothing more.

In **SQL Editor** (replace with your Application ID, keep the backticks):

```sql
GRANT USE CATALOG ON CATALOG workspace TO `<application-id>`;
GRANT USE SCHEMA ON SCHEMA workspace.energy_dev TO `<application-id>`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME workspace.energy_dev.landing TO `<application-id>`;
SHOW GRANTS `<application-id>` ON VOLUME workspace.energy_dev.landing;
```

**Expected result:** the last query lists `READ VOLUME` and `WRITE VOLUME`.

`USE CATALOG` and `USE SCHEMA` only allow the SP to *navigate* to the volume. They don't give access to any table.

---

### Step 04 — Test the SP from your laptop (outside the repo!)

**What and why:** prove that the credentials and grants work before GitHub is involved. Debugging on your laptop is much faster than in CI.

**Important:** run this test **outside** the repo folder. Inside the repo, the CLI reads `databricks.yml`, and if it contains `profile: free`, your user profile gets mixed with the SP credentials (see Troubleshooting P3).

```powershell
Remove-Item Env:DATABRICKS_* -ErrorAction SilentlyContinue      # start clean
cd $HOME
$env:DATABRICKS_HOST = "https://dbc-xxxxxxxx-xxxx.cloud.databricks.com"
$env:DATABRICKS_CLIENT_ID = "<application-id>"
$env:DATABRICKS_CLIENT_SECRET = "<secret>"
$env:DATABRICKS_CLIENT_SECRET.Length                             # expect 36
databricks auth describe
databricks current-user me
databricks fs ls dbfs:/Volumes/workspace/energy_dev/landing
```

**Expected result:**
- length `36`
- `auth describe` shows auth type `oauth-m2m`, with host and client ID taken from the environment, and **no** `profile` line
- `current-user me`: `userName` = your **Application ID** (not your email)
- `fs ls`: `smard` and `weather`

Clean up and go back:

```powershell
Remove-Item Env:DATABRICKS_* -ErrorAction SilentlyContinue
cd C:\dev\energy-lakehouse-de
databricks current-user me -p free        # your email again
```

📸 `lab4-step04-sp-test.png`

---

### Step 05 — Generate the job, checks and workflow

**What and why:** four pieces of code:
1. a **job** in the bundle (pipeline → checks, email on failure, SP may run it)
2. a **quality-check notebook** with logic in tested pure functions
3. a **bash sync script** for the Linux GitHub runner (the PowerShell script only runs on Windows)
4. the **GitHub Actions workflow**

The prompt also fixes `databricks.yml`: a bundle must not hardcode a local profile, otherwise both the SP test and CI fail.

**Option A — Claude Code prompt:**

```text
Read CLAUDE.md first. Implement an automated daily refresh. Do not deploy.

Context: Databricks Free Edition has no outbound internet and no account-level APIs (no OIDC
federation policy possible). So GitHub Actions extracts and uploads; auth uses a service principal
OAuth secret (M2M): env DATABRICKS_HOST, DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET.

0. databricks.yml: if the workspace section contains `profile: ...`, replace it with
   `host: <workspace URL>` (all targets). Locally I pass `-p free` on the command line.
1. databricks.yml: add variables `refresh_sp_app_id` and `alert_email` (set values in the dev target).
2. resources/daily_refresh_job.yml — serverless Lakeflow Job "daily_refresh_job":
   task run_pipeline (pipeline_task -> energy_pipeline, full_refresh false),
   task quality_checks (depends on run_pipeline) running src/notebooks/03_quality_checks.py with catalog/schema params.
   email_notifications on_failure: ${var.alert_email}. No schedule (GitHub triggers it).
   permissions: service_principal_name ${var.refresh_sp_app_id} level CAN_MANAGE_RUN.
3. src/notebooks/03_quality_checks.py — fail the task (raise) with a clear message if:
   gold_energy_hourly max(hour_utc) is older than 72 h; gold_energy_weather_hourly has 0 rows with weather
   in the last 7 days; silver_energy_long or silver_weather_hourly is empty. Print a summary table otherwise.
   Put the threshold logic as pure functions in src/energy_lakehouse/quality.py with pytest tests.
4. scripts/sync_landing.sh — bash equivalent of sync_landing.ps1 (extract all, upload smard+weather with
   `databricks fs cp -r --overwrite`, verify each target with `databricks fs ls`, `set -euo pipefail`).
   No profile flag: auth comes from env vars.
5. .github/workflows/daily-refresh.yml:
   on: schedule cron '15 5 * * *' and workflow_dispatch. concurrency group daily-refresh.
   steps: checkout, astral-sh/setup-uv, uv sync, databricks/setup-cli, run scripts/sync_landing.sh,
   then `databricks jobs run-now ${{ vars.DATABRICKS_JOB_ID }}` (waits for completion, fails the workflow if the run fails).
   env from secrets DATABRICKS_HOST, DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET.
   Upload data/landing extractor logs as an artifact. timeout-minutes 60.
6. Update CLAUDE.md and README (a "Daily refresh" section with the architecture and the note that OIDC is
   used in the AWS chapter because Free Edition lacks account-level APIs).
List changed files and checkpoint commands.
```

**Option B:** copy from my repo at tag `lab4-complete`.

**Expected result — new or changed files:**

```
energy-lakehouse-de/
├── .github/workflows/daily-refresh.yml     (new)  schedule + manual trigger
├── databricks.yml                          (changed) host instead of profile, new variables
├── resources/daily_refresh_job.yml         (new)  pipeline → quality checks
├── scripts/sync_landing.sh                 (new)  Linux version of the sync script
├── src/
│   ├── energy_lakehouse/quality.py         (new)  pure check functions
│   └── notebooks/03_quality_checks.py      (new)
└── tests/test_quality.py                   (new)
```

**Why the job has no schedule:** GitHub triggers it right after the upload. A separate Databricks schedule could run before the files arrive.

**Why `CAN_MANAGE_RUN`:** the SP may start and stop runs of this job, but not edit or delete it.

---

### Step 06 — Configure, test, deploy, and get the job ID

In `databricks.yml` (dev target), fill in `refresh_sp_app_id` (Application ID) and `alert_email`. These aren't secrets, so committing them is fine.

```powershell
uv run ruff check .
uv run pytest
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run daily_refresh_job -t dev -p free
databricks jobs list -p free | Select-String "daily_refresh_job"
```

**Expected result:**
- tests green, `Validation OK!`, `Deployment complete!`
- the job run shows both tasks with `SUCCESS`
- the last command prints a line with the numeric **job ID**. Copy it.

📸 `lab4-step06-job-success.png`

---

### Step 07 — Store configuration in GitHub

**What and why:** GitHub **secrets** are encrypted and never shown in logs. **Variables** are for non-secret settings.

```powershell
gh secret set DATABRICKS_HOST --body "https://dbc-xxxxxxxx-xxxx.cloud.databricks.com"
gh secret set DATABRICKS_CLIENT_ID --body "<application-id>"
gh secret set DATABRICKS_CLIENT_SECRET          # paste when prompted; input is hidden
gh variable set DATABRICKS_JOB_ID --body "<job-id>"
gh secret list
gh variable list
```

**Expected result:** three secrets and one variable are listed, with no values shown.

Using `gh secret set` **without** `--body` for the secret keeps it out of your PowerShell history.

---

### Step 08 — Push and run the workflow manually

```powershell
git add .
git commit -m "feat: daily refresh via GitHub Actions, SP auth, Lakeflow Job with quality checks"
git push
gh workflow run daily-refresh.yml
gh run watch
```

**Expected result:**
- every workflow step is green: install → extract → upload → verify → job run
- in Databricks, **Jobs & Pipelines → daily_refresh_job** shows a new successful run, started by the SP

📸 `lab4-step08-workflow-green.png`, `lab4-step08-job-run.png`

---

### Step 09 — Prove fresh data arrived

```sql
SELECT (SELECT max(ingested_at) FROM workspace.energy_dev.bronze_smard_raw) AS last_ingest,
       (SELECT max(hour_utc)    FROM workspace.energy_dev.gold_energy_hourly) AS latest_energy_hour;
```

**Expected result:** `last_ingest` is a few minutes old. `latest_energy_hour` is yesterday or today, because SMARD publishes with a delay.

---

### Step 10 — Prove failures are loud

**What and why:** an alert you've never seen fire is an alert you can't trust.

Temporarily set the freshness threshold in `quality.py` to `0` hours, then:

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run daily_refresh_job -t dev -p free
```

**Expected result:** `quality_checks` fails with your message (e.g. *"gold_energy_hourly is stale: latest hour …"*), and **you receive a failure email**.

Revert the threshold, redeploy, rerun (green), and commit.

📸 `lab4-step10-failure-email.png`

---

### Step 11 — Check the schedule

**Where the schedule is set:** in `.github/workflows/daily-refresh.yml`:

```yaml
on:
  schedule:
    - cron: '15 5 * * *'     # minute hour day month weekday — always UTC
  workflow_dispatch:          # manual "Run workflow" button
```

`15 5 * * *` means every day at 05:15 UTC (07:15 in Germany in summer, 06:15 in winter).

**Better: use an off-peak minute** such as `'17 5 * * *'`. GitHub's scheduled runs are best effort, and runs at popular minutes (`:00`, `:15`, `:30`) are more likely to be delayed or dropped under load (see Troubleshooting P6).

Schedules only run from the repository's **default branch** (`main` or `master`). Check yours with `gh repo view --json defaultBranchRef --jq .defaultBranchRef.name`.

**To test the schedule without waiting until tomorrow:**

1. Check the current UTC time: `(Get-Date).ToUniversalTime().ToString("HH:mm")`
2. Set the cron about 15 minutes ahead on an odd minute, e.g. for 10:07 UTC: `cron: "23 10 * * *"`
3. Commit and push to the default branch.
4. Wait up to ~30 minutes, then check:
   ```powershell
   gh run list --workflow daily-refresh.yml --event schedule
   ```
   **Expected:** one run with event `schedule` and a ✓, e.g.:
   ```
   STATUS  TITLE          WORKFLOW      BRANCH  EVENT     ELAPSED
   ✓       daily-refresh  daily-ref...  master  schedule  4m31s
   ```
5. Restore the daily schedule on an off-peak minute (`"17 5 * * *"`), commit, push.

Don't use `*/5` (every 5 minutes) for testing. It would run the whole pipeline every 5 minutes and burn Free Edition quota.

Finally:

```powershell
git tag lab4-complete
git push --tags
```

📸 `lab4-step11-scheduled-run.png`

---

## 5. Checkpoint

1. `databricks current-user me` with SP credentials (outside the repo) returns the Application ID.
2. `daily_refresh_job` succeeds with both tasks green.
3. A manual GitHub workflow run is green end to end.
4. Bronze `max(ingested_at)` is updated by that run.
5. A deliberately failing check sends you an email.
6. A run with event `schedule` appears in GitHub Actions.

---

## 6. Troubleshooting — problems I actually hit

### P1 — OIDC isn't possible on Free Edition

**Symptom:** the Databricks docs for GitHub OIDC require `databricks account service-principal-federation-policy create ...`.
**Cause:** that's an account-level API. Free Edition has no account console and no account-level APIs.
**Solution:** use an OAuth secret stored as GitHub secrets. Use OIDC in the AWS chapter (trial account). Mention this trade-off in your README. It shows you know the better practice.

### P2 — Secret accidentally shared

**Symptom:** I pasted the secret into a chat while debugging.
**Cause:** human error, and it happens in real teams too.
**Solution:** treat it as compromised. **Delete** it on the SP's Secrets tab and **generate a new one**. When debugging, share only the first characters (`dose…`) and the length (`36`).

### P3 — `invalid_client` although ID and secret were correct

**Symptom:**
```
Error: error getting token: token refresh: Client authentication failed (error code: invalid_client)
```
The secret length was correct (36). `databricks auth describe` showed:
```
✓ profile: free (from bundle)
```
**Cause:** I ran the test **inside the repo folder**. The CLI read `databricks.yml`, which contained `profile: free`, and combined my user profile with the SP environment variables. The same setup would break GitHub Actions, where no profile `free` exists.
**Solution:**
1. Test SP credentials **outside** the repo (`cd $HOME`).
2. In `databricks.yml`, replace `profile: free` with `host: <workspace URL>` and pass `-p free` only on the command line (included in the Step 05 prompt).

**Lesson:** a bundle describes *where* to deploy (host), not *who* deploys (profile). Identity comes from the environment.

### P4 — Which scope for the OAuth secret?

**Symptom:** the dialog offers "BI Tools" or "Other APIs" with an "Add scope" list.
**Solution:** choose **Other APIs** → **All APIs**, or no scope (all-apis by default). Narrow scopes break CLI authentication. Least privilege comes from grants and job permissions.

### P5 — "Where is the schedule? Nothing appears in Actions."

**Cause:** the schedule lives in the workflow YAML (`on.schedule.cron`), is in **UTC**, only runs from the default branch, and GitHub delays scheduled runs by 5–30 minutes.
**Solution:** see Step 11 for a temporary test schedule.

### P6 — No scheduled run the morning after adding the workflow

**Symptom:** the workflow was pushed late in the evening. The next morning there was no run at 05:15 UTC.
**Investigation:** everything was configured correctly:
```powershell
gh repo view --json defaultBranchRef --jq .defaultBranchRef.name          # master
git show master:.github/workflows/daily-refresh.yml | Select-String "cron" # - cron: "15 5 * * *"
gh workflow list                                                            # daily-refresh  active
gh run list --workflow daily-refresh.yml --event schedule                   # no runs found
```
**Cause:** GitHub cron is best effort. Runs can be delayed or dropped under load, especially at popular minutes like `:00` and `:15`, and a new schedule may miss its first slot.
**Solution:** a test schedule ~15 minutes ahead on an odd minute (`"23 10 * * *"`) ran successfully (event `schedule`, 4 min 31 s). The daily schedule now uses an off-peak minute: `"17 5 * * *"`. `workflow_dispatch` stays as a manual fallback.

### Other problems you may hit

| Problem | Cause | Fix |
| --- | --- | --- |
| `PERMISSION_DENIED` on `fs cp` in the workflow | SP is missing `WRITE VOLUME` or `USE SCHEMA` | Rerun the Step 03 grants and check with `SHOW GRANTS` |
| `jobs run-now` fails with permission error | SP lacks `CAN_MANAGE_RUN` on the job | Check `permissions` in `daily_refresh_job.yml`, redeploy |
| `jobs run-now`: job not found | Wrong `DATABRICKS_JOB_ID`, e.g. after the job was recreated | `databricks jobs list -p free`, update with `gh variable set` |
| Workflow can't find `databricks` | `databricks/setup-cli` step missing | Add the step before the sync script |
| `sync_landing.sh: Permission denied` on the runner | Script not executable | `git update-index --chmod=+x scripts/sync_landing.sh`, commit |
| Bash script fails with `\r: command not found` | Windows CRLF line endings | `git config core.autocrlf input` (Lab 0), and add `*.sh text eol=lf` to `.gitattributes` |
| No failure email | Wrong address, or it went to spam | Check `alert_email` and the job's notification settings |
| Scheduled runs stop after weeks | GitHub disables schedules in public repos after 60 days without activity | Any commit re-enables them |
| Auth suddenly fails after ~2 years | OAuth secret expired | Generate a new secret, `gh secret set DATABRICKS_CLIENT_SECRET` |

---

## 7. What you learned

- Why automation needs a **service principal**, and how to give it **least privilege** with UC grants and job permissions
- **OAuth M2M** vs. **OIDC workload identity federation**, and why Free Edition limits you to M2M
- Building a **Lakeflow Job** with task dependencies, quality checks and failure notifications
- **External orchestration**: GitHub Actions downloads, Databricks processes
- Handling **secrets** safely: GitHub secrets, rotation after exposure, expiry dates
- Separating **where** (host in the bundle) from **who** (identity from the environment)
- Testing failure paths on purpose, and testing **cron schedules** without waiting a day

**Interview-ready summary:** *"I automated a daily refresh: a scheduled GitHub Actions workflow extracts market and weather data, uploads it to a Unity Catalog volume as a least-privilege service principal, and triggers a Lakeflow Job that runs the pipeline and fails loudly on stale data. Free Edition has no account-level APIs, so I used an OAuth secret instead of OIDC. The OIDC version is implemented in the AWS chapter."*

---

## 8. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-20 |
| OS | Windows 11 (local), ubuntu-latest (GitHub runner) |
| Databricks | Free Edition (serverless), no outbound internet, no account-level APIs |
| Databricks CLI | `<output of databricks -v>` |
| uv | `<output of uv --version>` |
| GitHub CLI | `<output of gh --version>` |
| SP secret expiry | `<date>` (record in runbook) |

**Next:** Lab 5 — CI/CD: PR checks, automatic deploy to staging on merge, prod on release tag, and running prod as the service principal.
