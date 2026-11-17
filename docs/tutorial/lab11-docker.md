# Lab 11 — Docker: A Versioned, Scanned Extractor Image, Promoted to Prod, and a Dev Container

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 10 — Packaging, cleanup and portfolio documentation. Next: Lab 12 — Terraform.

## 1. Goal

Until now, the daily extraction ran as plain Python on a GitHub runner, using whatever Python and packages that runner happened to have. In this lab the extractor becomes a **container image**: built once, scanned, versioned, and **promoted** to prod together with the approved release.

| PR | Content | Result |
| --- | --- | --- |
| **A, the image** | Multi-stage Dockerfile, non-root user, `.dockerignore`; light base dependencies with an `ml` extra for the Databricks jobs; CI builds, smoke-tests, lints (hadolint) and scans (Trivy) the image | A 201 MB image; every PR checks it |
| **B, publish and promote** | Every release pushes `ghcr.io/<owner>/energy-extractor:<version>` and `:sha-<commit>`; after the approved prod deploy, the **same digest** is tagged `:prod`; the daily refresh pulls `:prod` and runs it **by digest** | Prod extraction runs on a pinned, scanned image whose digest is logged every day |
| **C, dev container** | `.devcontainer/` so the repo opens in VS Code with Python, uv, Databricks CLI and gh preinstalled; CI builds the container and runs the tests inside | "Works on my machine" becomes "works in the container" |

Plus three fixes that real operation forced on us: pinned runner and action versions, a completeness-severity redesign, and exact-digest promotion with `crane`.

**My results:** extractor image **201 MB**, runs as user `app`; prod refresh green on the image; `:prod` and `:v1.3.2` have the **same digest**; dev container builds in CI; releases v1.1.0 → **v1.3.2**.

**Time:** about three evenings, most of it spent on the real problems in Section 8.

---

## 2. Concepts in plain words

### 2.1 Image, container, registry, digest

| Term | Meaning |
| --- | --- |
| **Image** | A packaged, read-only filesystem plus a start command: your code, Python and the dependencies |
| **Container** | A running instance of an image. It's thrown away after the run (`docker run --rm`) |
| **Registry** | Where images are stored and pulled from, here **GHCR** (GitHub Container Registry) |
| **Tag** | A human-readable, **movable** name: `v1.3.2`, `prod` |
| **Digest** | `sha256:…`, the **content hash** of the image. It never changes; the same digest means byte-identical content |

**Rule:** tags are for people, digests are for machines. Prod should know **which digest** it ran.

### 2.2 Multi-stage builds and non-root

A **multi-stage** Dockerfile builds the wheel in a first stage (with uv and build tools) and copies **only the wheel** into a slim runtime stage. Build tools never reach the final image, which keeps it small and gives attackers less to work with.

Running as a **non-root user** (`USER app`) means a bug or a malicious dependency can't take over the container as root.

### 2.3 Build once, promote by tag

Industry teams don't rebuild an image per environment. They build **once**, scan it, and **promote** the same digest:

```
release vX.Y.Z → build + scan + push :vX.Y.Z and :sha-…  →  approval → bundle deploy prod  →  tag the SAME digest as :prod
daily refresh  → pull :prod → log digest + version → run by digest
```

Rollback is then just **moving `:prod` back** to an older, already-scanned version.

### 2.4 No secrets in the container

The container **only extracts** into a mounted folder. Uploading to Databricks stays a separate workflow step. So **no Databricks credential ever enters the image or the container**: a container that doesn't need secrets can't leak them.

### 2.5 Dev containers

A **dev container** (`.devcontainer/devcontainer.json`) tells VS Code to open the repo **inside a container** with all tools installed. A new colleague, or a recruiter cloning the repo, clicks *Reopen in Container* and has a working setup, without any Windows PATH, Python-version or Smart App Control problems.

---

## 3. Prerequisites

- Labs 0–10 completed; prod refreshing daily
- **Docker Desktop** (WSL 2 backend) and the VS Code extension **Dev Containers**
- Golden rules, plus three new ones from this lab:
  - **Check `mergeable` before waiting for checks:** `gh pr view <n> --json mergeable`
  - **Check releases before creating one:** `gh release list --limit 3`
  - **Check a branch really contains something before PR/push:** `git log --oneline origin/master..HEAD`

---

## 4. PR A — the extractor image

### Step 01 — Install Docker Desktop

```powershell
winget install -e --id Docker.DockerDesktop
```

Restart Windows, start Docker Desktop, accept the terms, let it use WSL 2 (`wsl --update` if asked).

```powershell
docker version
docker run --rm hello-world
```

**Expected:** `docker version` shows **Client** and **Server**; `hello-world` prints *"Hello from Docker!"*.

📸 `lab11-step01-hello-world.png`

### Step 02 — Branch and prompt (plan mode)

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c feat/docker-extractor
```

```text
Read CLAUDE.md first. Lab 11 PR A: containerise the daily extractor. Plan first, don't deploy.
The container must ONLY extract to a local folder; uploading stays in the workflow. No Databricks credentials
in the image or container.
1. Dependencies: one source of truth in pyproject.toml. Base deps = what extraction needs; heavy ML deps
   (scikit-learn, mlflow, holidays) in an optional extra "ml" that the Databricks job environments install.
   Verify the bundle job environments still get the ml deps; train and score stay identical. Target image < 300 MB.
2. Dockerfile (multi-stage): builder with uv builds the wheel; runtime python:3.12-slim (pinned patch version),
   install only the wheel, non-root user "app", /data as VOLUME, ENTRYPOINT = the extractor CLI,
   OCI labels (source, version, revision). No secrets, no repo-wide COPY.
3. .dockerignore: .git, .venv, dist, build, .databricks, data, tests, docs.
4. Extractor CLI: --out (default /data); exit code != 0 if any file failed.
5. ci.yml job "docker": build, run --help, a small real extract into a temp folder, hadolint, Trivy
   (fail on CRITICAL with a fix available).
6. tests/test_docker.py: non-root USER, pinned base image, .dockerignore excludes .git/.venv/.databricks.
Handback: changed files, image size, checkpoint commands.
```

**Review the dependency plan before approving it.** It touches the job environments, the area where Labs 7 and 10 broke.

### Step 03 — Build and run locally

```powershell
uv sync ; uv run ruff check . ; uv run python -m pytest -q
docker build -t energy-extractor:dev .
docker images energy-extractor
docker run --rm energy-extractor:dev --help
docker run --rm --entrypoint whoami energy-extractor:dev
mkdir .tmp\landing -Force
docker run --rm -v "${PWD}\.tmp\landing:/data" energy-extractor:dev --source smard --weeks 1 --out /data
docker run --rm -v "${PWD}\.tmp\landing:/data" energy-extractor:dev --source smard --weeks 1 --out /data
Get-ChildItem .tmp\landing -Recurse -File | Measure-Object
```

**Expected:**
- the image is **under 300 MB** (mine: **201 MB**)
- `--help` lists the extractor options; `whoami` prints **`app`**
- the first run writes files (`written: N, failed: 0`); the second run **skips** most of them, because the state lives in the mounted folder, not in the container
- `.tmp` doesn't appear in `git status`

📸 `lab11-step03-docker-run.png`

**Note:** `--help` downloads nothing. A file count of 0 after only `--help` is expected.

### Step 04 — Prove the job environments still work

The image is small because scikit-learn and mlflow moved into the `ml` extra. **Prove** the Databricks jobs still get them:

```powershell
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run train_forecast_job -t dev -p free
databricks bundle run daily_refresh_job -t dev -p free
```

**Expected:** both jobs `TERMINATED SUCCESS`, including `train_*`, `score_forecast` and `detect_anomalies`. A `ModuleNotFoundError: sklearn` here would mean the jobs lost the heavy packages.

The deploy uploads two wheels (`patched_wheels/…0.1.0+<timestamp>` and `dist/…`): the bundle stamps a unique dev version so a job can't reuse a cached old wheel.

### Step 05 — PR, staging, release, retrain

```powershell
git add -A ; git commit -m "feat: containerised extractor (multi-stage, non-root) with ml extra for job environments"
git log --oneline -1 ; git status --short | Measure-Object
git push -u origin HEAD ; gh pr create --fill --base master
Start-Sleep -Seconds 30 ; gh pr view --json mergeable ; gh pr checks --watch
```

**Expected:** `ci ✓` and the new **`docker ✓`** (build → `--help` → test extract → hadolint → Trivy).

Merge, check staging ✓, `gh release create v1.1.0 …`, approve. **The job environments changed**, so retrain prod **before** it scores (the Lab 7 pickle lesson):

1. **`[prod] train_forecast_job` → Run now** → ✓
2. `gh workflow run daily-refresh.yml` → ✓

```sql
SELECT target, count(*) AS hours, max(cast(model_version AS INT)) AS model_version
FROM workspace.energy_prod.gold_forecast_hourly
WHERE issue_date = (SELECT max(issue_date) FROM workspace.energy_prod.gold_forecast_hourly) GROUP BY target;
```

**Expected:** 24 rows per target, model version one higher than before (mine: 3).

**Detail worth knowing:** `model_version` is a **text** column. `max()` on text compares character by character, so `max('10', '9')` returns `'9'`. Cast it to INT in checks.

---

## 5. PR B — publish to GHCR, promote to `:prod`, run it daily

### Step 06 — Prompt (plan mode)

```text
Read CLAUDE.md first. Lab 11 PR B: publish the extractor image to GHCR and run it in the daily refresh. Plan first.
1. deploy-prod.yml: job "publish-image" BEFORE the approval-gated deploy: build, Trivy scan, push
   ghcr.io/<owner>/energy-extractor:<release tag> and :sha-<short sha> with the OCI source label.
   packages: write only on this job. Keep the workflow_dispatch inputs (tag, force).
2. After a successful bundle deploy, job "promote-image" tags the SAME digest as :prod (no rebuild);
   fail loudly if the version tag doesn't point to the recorded digest.
3. daily-refresh.yml: docker run of ghcr.io/...:prod, mount $RUNNER_TEMP/landing at /data, --user "$(id -u):$(id -g)",
   log digest + version label before running, run BY DIGEST. Upload and job-run steps unchanged.
   permissions: contents: read, packages: read. No DATABRICKS_* passed to the container.
4. Tests: job order publish -> deploy -> promote; packages: write only there; no DATABRICKS_* on docker run.
5. Runbook: which image prod ran, rollback by retagging :prod; CLAUDE.md.
```

### Step 07 — Review the workflow

```powershell
Get-Content .github\workflows\daily-refresh.yml | Select-Object -Index (34..92)
```

**Expected (and what my version had):**
- `DATABRICKS_*` appear **only** on the upload and run-job steps
- GHCR login via `--password-stdin` (the token never appears in logs or the process list)
- a pull step that logs `extractor: …:prod = …@sha256:… (version …, revision …)`
- `docker run … "${EXTRACTOR_REF}"`, i.e. **by digest**: even if `:prod` moves during the run, this run keeps the image it logged
- `set -euo pipefail`, so a failed extractor fails the step despite `tee`

**Pin every action.** `databricks/setup-cli@main` means "whatever is on main today", a supply-chain risk. That action publishes **tags**, not releases:

```powershell
gh api repos/databricks/setup-cli/tags --jq '.[0:5][].name'
```

Pin it (mine: `v1.23.0`) and add a test that no workflow uses `@main`/`@master`.

### Step 08 — PR, merge, release in the same sitting

**The bootstrap trap:** after the merge, master's `daily-refresh.yml` expects `:prod`, but `:prod` only exists after the **next release**. Merge and release **in the same sitting**, well before the 06:17 scheduled run.

```powershell
gh pr view <n> --json mergeable            # MERGEABLE (see P5 if checks never start)
gh pr checks <n> --watch
gh pr merge <n> --squash --delete-branch
git switch master ; git fetch origin ; git reset --hard origin/master
gh run list --workflow deploy-staging.yml --limit 1
gh release list --limit 3                  # pick a version that isn't taken (P6)
gh release create v1.3.0 --target master --generate-notes --title "v1.3.0 - extractor image on GHCR, promoted to prod"
```

Approve. **Expected:** `publish-image` ✓ → `deploy` ✓ → `promote-image` ✓.

### Step 09 — Package settings

GitHub → your profile → **Packages → energy-extractor → Package settings**:
- **Visibility: Public** (it contains no secrets, and it shows on your profile)
- **Manage Actions access:** `energy-lakehouse-de` with role **Write**, not Read: the same repo **publishes** the image (P8)

Check that the tags point to the same content, **without** logging in (this also proves the package is public):

```powershell
docker logout ghcr.io
docker buildx imagetools inspect ghcr.io/tutul032/energy-extractor:prod   | Select-String "Digest:" | Select-Object -First 1
docker buildx imagetools inspect ghcr.io/tutul032/energy-extractor:v1.3.0 | Select-String "Digest:" | Select-Object -First 1
```

📸 `lab11-step09-ghcr-package.png`

### Step 10 — Run the refresh on the image

```powershell
gh workflow run daily-refresh.yml
Start-Sleep -Seconds 20
$id = gh run list --workflow daily-refresh.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $id
gh run view $id --log | Select-String "extractor: ghcr|summary:" | Select-Object -First 2
```

**Expected (mine):**

```
extractor: ghcr.io/tutul032/energy-extractor:prod = …@sha256:1fafef1b…12ae (version v1.3.0, revision 97603b3…)
INFO summary: {'written': 64, 'skipped': 0, 'failed': 0}
```

`skipped: 0` is expected: each runner starts with an empty temp folder. Skipping already-loaded files happens in the upload step, against the volume.

📸 `lab11-step10-refresh-digest.png` (the run summary page shows the extractor line)

---

## 6. Hardening: cleanup, completeness severity, exact-digest promotion

### Step 11 — Cleanup PR (v1.3.1)

```text
Read CLAUDE.md first. Cleanup, no functional changes. Do not deploy.
1. Remove leftover hello_job and extract_smard_job (if present).
2. All workflows: actions/checkout and actions/upload-artifact to their current major (Node 24);
   runs-on: ubuntu-24.04 instead of ubuntu-latest. Tests: no ubuntu-latest, no @main/@master, no checkout@v4.
3. Replace `uv run pytest` with `uv run python -m pytest` in docs/commands (Smart App Control, P9).
```

**Why pin the runner:** GitHub announced that `ubuntu-latest` moves to Ubuntu 26. Pinning means the OS changes only when **you** decide, the same idea as pinning images and actions.

### Step 12 — Completeness: gate vs. monitor (found in prod)

After v1.3.1, the prod refresh failed in `detect_anomalies` (P12):

```
RuntimeError: 1 critical data-quality anomalies:
- completeness hard_coal 2026-10-28 23:00 UTC: 5 of 24 hours missing on 2026-10-29 (21%)
```

**Investigation:**

```sql
SELECT series_name, count(*) AS rows, count(value) AS with_value, max(_ingested_at) AS newest_load
FROM workspace.energy_prod.silver_smard
WHERE to_date(from_utc_timestamp(ts_utc, 'Europe/Berlin')) = date'2026-10-29'
GROUP BY series_name ORDER BY series_name;
```

| series_name | rows | with_value |
| --- | --- | --- |
| day_ahead_price | 24 | 24 |
| **hard_coal** | **19** | **19** |
| lignite, natural_gas, solar, total_load, wind_* | 24 | 24 |

Only **hard coal** had gaps, and all series came from the same file batch. So it was a real gap at the source, not a failed load. But the forecasts and the KPIs' `is_complete` use **only load and price**. A gap in one generation series doesn't break anything downstream.

**Fix (Lab 8's severity principle, applied to a real case):**
- completeness of **load and price** stays **critical** (a **gate**)
- completeness of **generation series** becomes a **warning** (a **monitor**)
- the list of critical series is configurable in one place, with tests for both cases

### Step 13 — Exact-digest promotion with `crane` (v1.3.2)

The v1.3.1 release failed in `promote-image` (P10): `docker buildx imagetools create --tag :prod IMAGE@DIGEST` had written a **new index** with a different digest. The fix tags the existing manifest **without rewriting it**:

```bash
crane tag "${IMAGE}@${DIGEST}" prod          # adds a tag, digest unchanged
crane digest "${IMAGE}:prod"                 # must equal ${DIGEST}
```

`publish-image` now records the digest **from the registry** (`crane digest IMAGE:TAG`), so both checks compare the same thing.

Release and verify:

```powershell
gh release create v1.3.2 --target master --generate-notes --title "v1.3.2 - completeness severity, exact-digest promotion"
docker buildx imagetools inspect ghcr.io/tutul032/energy-extractor:prod   | Select-String "Digest:" | Select-Object -First 1
docker buildx imagetools inspect ghcr.io/tutul032/energy-extractor:v1.3.2 | Select-String "Digest:" | Select-Object -First 1
gh workflow run daily-refresh.yml
```

**Expected:** `promote-image ✓`, **identical digests**, and every step of the refresh ✓ (hard coal is now a warning).

📸 `lab11-step13-prod-refresh-green.png`

---

## 7. PR C — dev container

### Step 14 — Prompt

```text
Read CLAUDE.md first. Add a VS Code dev container. Don't change application code.
.devcontainer/Dockerfile: FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm (verified tag),
  RUN rm -f /etc/apt/sources.list.d/yarn.list (stale Yarn apt key, P15).
devcontainer.json: "build": {"dockerfile": "Dockerfile"}; features github-cli:1, docker-outside-of-docker:1,
  a local databricks-cli feature at the same version as CI (1.23.0); remoteUser vscode; postCreateCommand
  installs uv and runs uv sync + pre-commit install. No secrets: Databricks login inside with `databricks auth login`.
.gitattributes: "*.sh text eol=lf". CI job "devcontainer" (pinned action, ubuntu-24.04) builds it and runs pytest inside.
Tests: base image not :latest, remoteUser not root, CLI version == CI version, no TOKEN/SECRET env.
```

**Verify every pin exists before using it.** The first version Claude Code wrote "pinned" `python:3.3.2-3.12-trixie` and exact feature versions that looked precise but couldn't be verified (P13):

```powershell
docker manifest inspect mcr.microsoft.com/devcontainers/python:1-3.12-bookworm > $null; "image: $LASTEXITCODE"   # 0 = exists
```

### Step 15 — Open it

1. Install the VS Code extension **Dev Containers** (`code --install-extension ms-vscode-remote.remote-containers`)
2. Docker Desktop running (`docker version` shows a **Server** section)
3. **Ctrl+Shift+P → "Dev Containers: Rebuild and Reopen in Container"**
4. Inside (the terminal is now **bash**):

```bash
uv --version ; databricks -v ; whoami ; uv run python -m pytest -q
```

**Expected:** `x86_64-unknown-linux-gnu`, `Databricks CLI v1.23.0`, `vscode`, all tests pass.

📸 `lab11-step15-devcontainer.png` (VS Code's bottom-left shows *Dev Container*)

To go back: **Ctrl+Shift+P → "Dev Containers: Reopen Folder Locally"**.

### Step 16 — PR and tag

```powershell
gh pr view --json mergeable ; gh pr checks --watch           # ci ✓, docker ✓, devcontainer ✓
gh pr merge --squash --delete-branch
git switch master ; git fetch origin ; git reset --hard origin/master
git tag lab11-complete origin/master ; git push origin lab11-complete
```

---

## 8. Troubleshooting — problems I actually hit

### P1 — "No module named sklearn"? Prove the dependency split, don't assume it

Moving heavy packages into an `ml` extra made the image small, but it could silently remove them from the Databricks jobs. **Fix:** run both dev jobs after the change (Step 04) before merging, then retrain prod before scoring.

### P2 — `head` not recognised, `Get-Content` not recognised

```
head : The term 'head' is not recognized …
'Get-Content' is not recognized as an internal or external command
```

**Cause:** the first ran Linux commands in **PowerShell** (they were meant for the container's bash); the second ran PowerShell commands in **cmd** (prompt `C:\…>` without `PS`).
**Fix:** check the prompt. `PS C:\…>` is PowerShell, `C:\…>` is cmd, `vscode ➜ /workspaces/…` is the container. PowerShell's `head` is `Select-Object -First 1`.

### P3 — PowerShell mangled `--jq` strings

```
unknown command "\\(.createdAt)" for "gh run list"
function not defined: PR/0
```

**Cause:** PowerShell's quoting breaks `jq` string interpolation like `"\(.x)"`.
**Fix:** use plain `--json field1,field2` output, or simple `--jq '.[0].databaseId'` paths without interpolation.

### P4 — "No checks reported", then "Everything up-to-date"

Two different causes with the same symptom.

1. **Too fast:** `gh pr checks` right after a push. Wait 20–30 s.
2. **Nothing to check:** `git push` said *"Everything up-to-date"* and `gh pr create` said *"could not find any commits between origin/master and …"*. The commit had **never happened**, because Claude Code hadn't written the change yet.

**Check:** `git log --oneline origin/master..HEAD` must list your commit before you push or open a PR.

### P5 — CI never started: a silent merge conflict

```
gh pr checks 16 --watch  →  no checks reported on the 'feat/ghcr-image' branch
gh run list (newest run)  →  headSha from yesterday, not the PR head
```

**Cause:** master had changed the same files (`daily-refresh.yml`, `tests/test_workflows.py`) in the meantime. **GitHub doesn't run `pull_request` workflows on a PR it can't merge, and it doesn't say why.**
**Diagnosis:** `gh pr view <n> --json mergeable` → `CONFLICTING`.
**Fix:**

```powershell
git fetch origin ; git merge origin/master
git status --short                          # UU / AA = conflicts
# resolve (keep both sides' intent), then:
Select-String -Path .github\workflows\*.yml, tests\*.py -Pattern '^(<<<<<<<|=======|>>>>>>>)'   # no output
uv run python -m pytest -q
git add -A ; git commit --no-edit ; git push
```

A merge that pushes only **1 object** deserves a second look: check `git diff --stat origin/master HEAD` to make sure your PR's changes survived the resolution.

### P6 — `Release.tag_name already exists`

```
HTTP 422: Validation Failed … Release.tag_name already exists
```

**Cause:** `v1.2.0` had been created the night before, **before** PR B was merged, so it didn't contain PR B.
**Fix:** fix forward with the next version (`v1.3.0`); never reuse or move a tag.
**Prevention:** `gh release list --limit 3` before every release.

### P7 — `releases/latest` → 404 for an action

```
gh api repos/databricks/setup-cli/releases/latest  →  HTTP 404
```

**Cause:** that action publishes **tags**, not GitHub releases.
**Fix:** `gh api repos/databricks/setup-cli/tags --jq '.[0].name'`.

### P8 — `denied: permission_denied: write_package`

```
Push image   denied: permission_denied: write_package
```

**Cause:** after the first publish, I set the repo's package role to **Read** (Step 09 in my first version said "at least Read"). The first push had worked because creating the package gave the repo full rights; setting Read afterwards removed write.
**Fix:** role **Write** for the repo that publishes; Read is enough only for repos that just pull. Then rerun only the failed jobs: `gh run rerun <id> --failed`.

### P9 — Smart App Control blocked `pytest.exe`

```
error: Failed to spawn: pytest
  cause: An Application Control policy has blocked this file. (os error 4551)
```

**Cause:** Smart App Control judges every new or changed `.exe` again. After a venv rebuild, uv's generated `pytest.exe` launcher was blocked (signed `ruff.exe` still ran).
**Fix:** run tools through the signed interpreter: **`uv run python -m pytest -q`**. Updated in CLAUDE.md and all docs.

### P10 — The promotion check caught a rewritten digest

```
promote-image  #1 pushing sha256:51a137… to ghcr.io/tutul032/energy-extractor:prod
##[error] …:prod is sha256:51a137…, expected sha256:b73172…
```

**Cause:** `docker buildx imagetools create --tag :prod IMAGE@b731…` doesn't just add a pointer. For a single-platform manifest it writes a **new index** around it, with its own digest (`51a1…`). With v1.3.0 the comparison happened to match; v1.3.1 was built on the newly pinned `ubuntu-24.04` runner and pushed a plain manifest, so the same command produced a different result.
**State in prod:** safe. `:prod` pointed to an index containing exactly the scanned v1.3.1 image.
**Fix:** `crane tag IMAGE@DIGEST prod`, which adds a tag without rewriting anything (Step 13).
**Lesson:** without the exact-digest check, `:prod` would silently have pointed to a digest nobody had recorded. **Guards that compare exact values catch tools that "almost" do what you asked.**

### P11 — CI auth failure: the CI/CD service principal had no secret

```
Validate bundle (staging)  Error: default auth: cannot configure default credentials …
Config: host=***, client_id=***, client_secret=***
```

**Diagnosis:**

```powershell
databricks service-principals list -p free -o json | ConvertFrom-Json | ForEach-Object { $_ } | Select-Object id, displayName, applicationId
databricks service-principal-secrets-proxy list <numeric id> -p free -o json
```

`sp-energy-cicd` returned **`[]`**: no secret at all. The GitHub secret pointed to a secret that no longer existed. The refresh SP's secret was fine (expires 2028-10-17).
**Fix:** create a new secret and put it straight into GitHub, never into chat or files:

```powershell
databricks service-principal-secrets-proxy create <numeric id> --lifetime 31536000s -p free
gh secret set DATABRICKS_CICD_CLIENT_SECRET        # paste at the prompt; nothing is echoed
Clear-Host
```

**Lessons:**
- The secrets command takes the SP's **numeric id**, not the applicationId.
- My first rerun "failed again" because the new secret had **not been created yet**. Check `secrets-proxy list` and `gh secret list` (the update time) before rerunning.
- **The runbook's expiry table had placeholders.** Credentials need a creation date, an expiry date and a rotate-by date, written down **when they're created**.

### P12 — Prod failed on hard-coal completeness

See Step 12. Two diagnosis details worth keeping:
- With `-o json`, `databricks jobs list-runs` returns a **plain list**, so use `($runs | Select-Object -First 1).run_id`, not `$runs.runs`. With an empty ID, the CLI starts an **interactive prompt**.
- `gh run view --log-failed | Select-Object -Last 25` showed only the checkout cleanup. Ask Databricks for the failed task instead (`jobs get-run` → first FAILED task → `get-run-output`).

### P13 — Dev container: invented "precise" pins

```
Command failed: … devContainersSpecCLI.js up … Exit code 1
```

**Cause:** the generated `devcontainer.json` pinned `python:3.3.2-3.12-trixie`, `github-cli:1.1.3` and similar values that looked precise but didn't exist or couldn't be verified.
**Fix:** use tags verified with `docker manifest inspect` (`1-3.12-bookworm`), major tags for features (`:1`), and **build the dev container in CI** so a non-existent pin fails there, with a readable log.

### P14 — VS Code stuck in the container window; Vim opened by Git

- **No "Reopen in Container" command:** the **Dev Containers** extension wasn't installed.
- **Couldn't get back to the normal project (no Claude Code):** VS Code reopened the failed container window. Fix: close VS Code, switch to a branch without `.devcontainer/`, then `code --new-window --disable-extension ms-vscode-remote.remote-containers C:\dev\energy-lakehouse-de`. Or use **"Dev Containers: Reopen Folder Locally"**.
- **`git merge` opened Vim:** `Esc`, `:wq`, `Enter` saves and closes. After I closed the window instead, `git status` showed *"All conflicts fixed but you are still merging"*, fixed with `git commit --no-edit`. Prevention: `git config --global core.editor "code --wait"` or `git merge --no-edit`.
- **"Starting Docker" for 40 minutes:** Docker Desktop itself was hanging. Fix: `Stop-Process` Docker Desktop, `wsl --shutdown`, start it again, check `docker version`.

### P15 — `NO_PUBKEY 62D54FD4003F6525` (Yarn) broke a feature install

```
W: GPG error: https://dl.yarnpkg.com/debian stable InRelease: … NO_PUBKEY 62D54FD4003F6525
ERROR: Feature "Docker (docker-outside-of-docker)" failed to install!
```

**Cause:** the base image ships a **Yarn apt source** whose signing key changed; `apt-get update` in the feature refused it.
**Fix:** a two-line `.devcontainer/Dockerfile` that removes `yarn.list` **before** features install (features always run after the Dockerfile). Found in **CI's** readable build log, while my laptop only showed a hang.

### Other things worth knowing

| Situation | Explanation |
| --- | --- |
| `written: 64, skipped: 0` in every refresh | Each runner starts empty; skipping happens in the upload step |
| A merge pushed only `Total 1` object | Only the merge commit was new; verify with `git diff --stat origin/master HEAD` |
| A deleted branch can be restored | `git branch <name> <commit-id>` (the ID is printed by `git branch -D`) |
| GitHub's cron ran hours late | The 05:17 UTC schedule started at 11:24 UTC once; GitHub delays scheduled runs under load |
| Node.js 20 / ubuntu-latest annotations | Deprecation notices: pin action majors and the runner image |

---

## 9. Checkpoint

1. The extractor image is **≤ 300 MB**, runs as **non-root**, and is hadolint- and Trivy-checked in CI.
2. Every release publishes `vX.Y.Z` and `sha-…`; **`:prod` is moved only after an approved prod deploy**, with `crane tag`, and `:prod` has **the same digest** as the release tag.
3. The **scheduled** daily refresh runs `:prod` **by digest**, logs digest + version, and passes **no Databricks secrets** to the container.
4. All actions and the runner image are pinned; no `@main`, no `ubuntu-latest`.
5. Completeness is critical only for **load and price**; generation gaps are warnings.
6. The dev container builds **in CI** and opens locally; tests pass inside.
7. The runbook's secret table has real dates.
8. Tag `lab11-complete` exists.

---

## 10. What you learned

- **Containers for data pipelines:** multi-stage builds, non-root users, slim images, `.dockerignore`, OCI labels
- **Dependency design:** light base dependencies plus an `ml` extra, with **one source of truth** in `pyproject.toml`
- **Supply-chain hygiene:** pinned images, actions and runners; image scanning with Trivy; Dockerfile linting with hadolint
- **Build once, promote by digest:** release tags vs. a moving `:prod` tag, exact-digest checks, `crane tag` vs. `imagetools create`
- **Secrets stay outside containers:** the container extracts, the workflow uploads
- **Least privilege in practice:** package roles (Read vs. Write), short-lived write scopes for manual operations
- **Operating CI/CD:** silent merge conflicts, taken release tags, rerunning failed jobs, missing service principal secrets
- **Gate vs. monitor, again:** a real hard-coal gap redesigned completeness severity
- **Dev containers:** reproducible environments, and why the container should be built in CI

**Interview-ready summary:** *"I containerised the daily extractor as a 200 MB multi-stage, non-root image that only extracts and never sees Databricks credentials. Every release builds it once, scans it with Trivy, pushes version and commit tags to GHCR, and after the approved prod deploy promotes the exact same digest to `:prod`. The daily refresh pulls `:prod`, logs the digest and runs it by digest, so I can prove which build ran. An exact-digest check caught a tool silently rewriting the manifest, which I fixed with `crane tag`. I also handled real operations issues: a silent merge conflict that blocked CI, a missing service-principal secret, and a source data gap that led me to make completeness critical only for the forecast inputs. A dev container, built and tested in CI, gives every contributor the same environment."*

---

## 11. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-28/31 |
| OS | Windows 11 (local, Smart App Control on), ubuntu-24.04 (GitHub runners) |
| Docker | Docker Desktop 4.97.0 (WSL 2 backend), Engine 29.9.2, containerd v2.4.2 |
| Extractor image | python:3.12-slim (pinned patch), 201 MB, user `app` |
| Registry | GHCR, public package, repo role Write |
| Promotion | crane tag (exact digest) |
| Scanning / linting | Trivy (fail on fixable CRITICAL), hadolint, actionlint |
| Databricks CLI | v1.23.0 (CI and dev container), v1.21.0 (laptop) |
| Dev container | devcontainers/python:1-3.12-bookworm + Dockerfile (Yarn source removed), features github-cli:1, docker-outside-of-docker:1 |
| Releases | v1.1.0, (v1.2.0 created before PR B was merged), v1.3.0, v1.3.1, **v1.3.2** |

**Open follow-ups:**
- the rollback drill (`crane tag …:v1.3.1 prod` → refresh → digest check → back)
- an approval-gated `rollback-image.yml` workflow
- the README update (diagram, container highlights, GHCR link, CHANGELOG)
- the runbook's secret table with real dates
- investigating why `sp-energy-cicd`'s secret disappeared

**Next:** Lab 12 — Terraform: codify the setup done by hand (schemas, volumes, groups, grants, service principal permissions), import the existing resources, and prove it with a `terraform plan` showing no changes.
