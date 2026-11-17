# Lab 10 — Packaging, Cleanup and Portfolio Documentation: From "It Works" to "Recruiter-Ready"

> Part of the series **"Databricks on AWS Portfolio — from zero to production-style projects"**.
> Project 1: `energy-lakehouse-de` — German energy data engineering on Databricks.
> Previous: Lab 9 — AI/BI dashboard and Genie. Next: Lab 10b — Reusable project template.

## 1. Goal

After Lab 9 the system works end to end. But a project that *works* isn't yet one that *convinces*: a hiring manager spends about **two minutes** on a repository. In this lab you pay off technical debt and make those two minutes count.

Three small pull requests, each easy to review:

| PR | Content | Why |
| --- | --- | --- |
| **A — Packaging** | Ship `energy_lakehouse` as a **wheel**; dependencies from `pyproject.toml` only; no `sys.path` workaround | Removes the root cause of Lab 7's dependency errors |
| **B — Cleanup** | Update GitHub Actions (Node.js 20 deprecation), remove leftovers (`hello_job`, `extract_smard_job`, smoke-test table), dead code | A clean repo signals care |
| **C — Docs** | README with diagram, results and screenshots; ADRs; runbook; changelog; tutorial index → release **v1.0.0** | What recruiters and engineers actually read |

**Time:** about one working day, plus recovery time if things go wrong (they did; see Troubleshooting).

---

## 2. Concepts in plain words

### 2.1 Why a wheel?

Until now, notebooks found the project code with `sys.path.append("..")`, and each job environment listed packages **separately** from `pyproject.toml`. Two lists drift apart, which caused the `holidays` error and the NumPy pickle mismatch in Lab 7.

A **wheel** (`.whl`) is Python's standard package format. The bundle builds it once from `pyproject.toml`, uploads it, and every task installs **exactly that package with its declared dependencies**: one source of truth.

### 2.2 Code vs. data files in a package

A wheel contains your Python modules. **Data files** your code reads (here: the contract YAMLs) are *not* included automatically. They must be declared as **package data** and loaded via `importlib.resources`, not via paths relative to the module file (Troubleshooting P1).

### 2.3 ADRs (Architecture Decision Records)

Short documents answering: *what did we decide, why, and what are the consequences?* They show reviewers that choices were deliberate, e.g. *"why GitHub Actions extracts instead of Databricks jobs"*.

### 2.4 Runbook

Operational instructions for when something breaks: *how to find the failing task, typical causes, how to rerun prod safely, when secrets expire.* It's the document a colleague on call needs at 07:30.

---

## 3. Prerequisites

- Labs 0–9 completed
- Golden rules, plus three new ones from this lab:
  - **Stop at the first error.** The next command in a sequence may assume something that didn't happen.
  - **Never `git reset --hard` with a dirty working tree.** Check `git status` first.
  - **Verify every commit** with `git log --oneline -1` before pushing.

---

## 4. PR A — Packaging as a wheel

### Step A1 — Branch and prompt

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c feat/wheel-packaging
```

**Claude Code prompt** (already includes the fix for P1):

```text
Read CLAUDE.md first. Package energy_lakehouse as a wheel and use it in all jobs and the pipeline. Do not deploy.
1. pyproject.toml: [build-system] (hatchling or uv_build) and [project] with runtime dependencies (requests, pydantic,
   pyyaml, holidays, scikit-learn, mlflow). Dev dependencies separate (pytest, ruff, pre-commit).
   Never list pyspark, databricks-connect, delta-spark, numpy, pandas, pyarrow as runtime dependencies.
2. Include contracts/*.yaml as package data (e.g. hatch force-include "contracts" -> "energy_lakehouse/contracts_data").
   load_contracts() reads them via importlib.resources; fall back to the repo's contracts/ only in a source checkout.
   Search the package for other paths relative to __file__ and fix them the same way.
3. databricks.yml: a whl artifact built with `uv build --wheel`.
4. Job environments install the built wheel (e.g. - ../dist/*.whl); train and score stay identical.
5. Pipeline: install the same wheel if the bundle schema supports it; otherwise document the current mechanism.
6. Remove sys.path manipulation from notebooks/pipelines.
7. Tests: notebook tasks importing energy_lakehouse install the wheel; no sys.path manipulation; the built wheel contains
   every contracts/*.yaml (inspect with zipfile).
8. Update CLAUDE.md.
```

### Step A2 — Build and test locally

```powershell
uv sync
uv build --wheel
Get-ChildItem dist
python -c "import zipfile,glob; z=zipfile.ZipFile(sorted(glob.glob('dist/*.whl'))[-1]); print([n for n in z.namelist() if n.endswith('.yaml')])"
uv run ruff check . ; uv run ruff format --check . ; uv run pytest -q
Select-String -Path src\notebooks\*.py, src\pipelines\*.py -Pattern "sys.path"
```

**Expected:**
- `dist\energy_lakehouse-<version>-py3-none-any.whl`
- the YAML list shows `energy_lakehouse/contracts_data/<table>.yaml` for every contract
- all tests pass (384 in my run)
- no `sys.path` hits

### Step A3 — Deploy dev and run both jobs

```powershell
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run daily_refresh_job -t dev -p free
databricks bundle run train_forecast_job -t dev -p free
```

**Expected:** `Building energy_lakehouse...` during deploy, and all tasks ✓ in both jobs.

**If the old wheel seems to be used** (the same error after a fix): bump the version in `pyproject.toml`, so the environment can't reuse a cached wheel.

### Step A4 — Commit (verified), PR, staging, release

```powershell
git branch --show-current                    # feat/wheel-packaging
uv run pre-commit --version                  # the hook needs this (P2)
git add -A
git commit -m "feat: package energy_lakehouse as a wheel (deps from pyproject, contracts as package data)"
git log --oneline -1                          # MUST show this commit
git status --short | Measure-Object           # MUST be 0
git push -u origin HEAD
gh pr create --fill --base master
```

Wait ~10 s → `gh pr checks --watch` → ✓ → `gh pr merge --squash --delete-branch`:

```powershell
git status --short | Measure-Object          # 0 → safe to reset
git switch master ; git fetch origin ; git reset --hard origin/master
gh run list --workflow deploy-staging.yml --limit 1
gh release create v0.6.0 --target master --generate-notes --title "v0.6.0 - wheel packaging"
```

Approve → `deploy-prod` ✓. (Mine failed here: Troubleshooting P4.)

### Step A5 — Retrain prod, then refresh

The job environments now come from the **wheel's** dependencies, so library versions may differ from those the prod champion was trained with. Train and score must share an environment (Lab 7 P5):

1. **Jobs & Pipelines → `[prod] train_forecast_job` → Run now** → ✓
2. Then `gh workflow run daily-refresh.yml`, wait ~10 s, `gh run watch` → ✓

**Verify:**

```sql
SELECT target, count(*) AS hours, max(model_version) FROM workspace.energy_prod.gold_forecast_hourly
WHERE issue_date = (SELECT max(issue_date) FROM workspace.energy_prod.gold_forecast_hourly) GROUP BY target;
```

**Expected:** 24 rows per target.

---

## 5. PR B — Cleanup

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c chore/cleanup
```

**Claude Code prompt:**

```text
Cleanup, no functional changes:
1. Update actions/checkout, actions/upload-artifact, astral-sh/setup-uv and databricks/setup-cli to their current major
   versions (check e.g. `gh api repos/actions/checkout/releases/latest --jq .tag_name`) so the Node.js 20 deprecation
   warnings disappear.
2. Remove resources/hello_job.yml + its notebook, and resources/extract_smard_job.yml (cannot run on Free Edition
   without outbound internet). Keep extract notebooks only if documented as "for workspaces with outbound internet".
3. Remove dead code, unused config keys and stale comments. Update CLAUDE.md and README references.
```

Test → commit (verified) → PR → merge → staging → release. After deploy:

```sql
DROP TABLE IF EXISTS workspace.energy_dev._smoke_test;
```

**Expected:** no `hello_job`/`extract_smard_job` in any environment, and no Node.js 20 annotations in GitHub Actions runs.

---

## 6. PR C — Portfolio documentation

### Step C1 — Screenshots

Copy your best screenshots into `docs/img/`: the prod dashboard pages (Market, Merit order, Forecast, Data quality), a Genie answer with SQL, the job graph, and an approval-gated prod deploy.

### Step C2 — Docs prompt

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git switch -c docs/portfolio
```

```text
Write the portfolio documentation. Audience: recruiters and hiring managers (2-minute read), then engineers.
1. README.md: one-line pitch + badges; a HERO image (docs/img/market_page.jpg) directly under the pitch;
   "What it does" in 4 bullets; architecture diagram as Mermaid; Results with real numbers (load MAPE 2.37 % vs 3.31 %;
   price MAE 43.0 vs 62.4 EUR/MWh; Genie 6/6; 0 critical in prod; merit-order effect); a Screenshots section with
   one caption per image, using the EXACT filenames in docs/img (do not comment them out); tech stack; how to run;
   data licences; engineering highlights (5 real problems, linking to the tutorials); known limitations.
2. docs/adr/0002–0009 (context, decision, consequences): GitHub Actions extraction; shared raw zone + schema per env;
   long silver / wide gold; contracts with CI guard + runtime verify; anomaly severity design; batch scoring instead of
   serving; dashboard schema per environment; Databricks vs native AWS.
3. docs/runbook.md: failed daily refresh (find the first failed task, common causes), secret rotation + expiry dates,
   DST issue window, safe prod rerun, "dashboard modified remotely", release checklist.
4. CHANGELOG.md from the GitHub releases. 5. docs/tutorial/README.md: index of all labs.
Factual, no marketing language.
```

### Step C3 — Check that every image link points to a real file

```powershell
Select-String -Path README.md -Pattern "\(docs/img/[^)]+\)" -AllMatches | ForEach-Object { $_.Matches.Value.Trim('(',')') } | Sort-Object -Unique | ForEach-Object { "{0}  ->  {1}" -f $_, (Test-Path $_) }
Select-String -Path README.md -Pattern "<!--"
```

**Expected:** every line ends in `True`, and there's **no** `<!--` around the screenshots (Troubleshooting P8, P9). Preview with **Ctrl+Shift+V** in VS Code.

### Step C4 — Commit, PR, merge, then release v1.0.0

```powershell
git add -A ; git commit -m "docs: portfolio README, ADRs, runbook, changelog"
git log --oneline -1
git push -u origin HEAD ; gh pr create --fill --base master
```

Wait ~10 s → `gh pr checks --watch` → `gh pr merge --squash --delete-branch`. **Only after the merge:**

```powershell
git switch master ; git fetch origin ; git reset --hard origin/master
git --no-pager ls-tree -r --name-only origin/master | Select-String "README.md|docs/img|CHANGELOG"    # present on master
gh release create v1.0.0 --target master --generate-notes --title "v1.0.0 - Project 1 complete"
git tag lab10-complete origin/master ; git push origin lab10-complete
```

Open the repo on GitHub: hero image, diagram, results table and screenshots are visible.

### Step C5 — Make the project findable

- **Pin** the repository on your GitHub profile
- Add **topics:** `databricks`, `data-engineering`, `mlops`, `lakehouse`, `unity-catalog`, `delta-lake`, `genai`
- Optional, high value: a **2–3 minute video walkthrough** linked under the pitch (Troubleshooting P11 explains why not a dashboard link)

---

## 7. Checkpoint

1. The wheel contains code **and** contract YAMLs; no notebook uses `sys.path`; all jobs green in dev and prod.
2. Prod was retrained under the new environment, and forecasts score.
3. No Node.js 20 warnings; no leftover jobs.
4. README on `master` shows hero image, diagram, results and all screenshots.
5. ADRs, runbook, changelog and tutorial index exist.
6. `v1.0.0` contains the documentation; the repo is pinned with topics.

---

## 8. Troubleshooting — problems I actually hit

### P1 — `FileNotFoundError: no contracts (*.yaml)` after switching to the wheel

```
FileNotFoundError: no contracts (*.yaml) in /local_disk0/.ephemeral_nfs/envs/pythonEnv-…/lib/python3.11/contracts
```

`apply_contracts` failed; four tasks after it were `UPSTREAM_FAILED` (and `dq_summary_llm` ran, because of `run_if: ALL_DONE`).

**Cause:** `contracts.py` located the YAMLs **relative to its own file** ("two folders up, then `contracts/`"). Installed from the wheel, the module lives in `site-packages/energy_lakehouse/`, so "two folders up" pointed to `lib/python3.11/`. The YAMLs weren't in the wheel at all.
**Fix:** ship them as **package data** and load via `importlib.resources`. Add a test that opens the built wheel and checks every YAML is inside.
**Lesson:** when code moves into a package, the data files it reads must move with it.

### P2 — Commit failed: `No module named pre_commit`

```
C:\dev\energy-lakehouse-de\.venv\Scripts\python.exe: No module named pre_commit
```

**Cause:** the pre-commit hook (Lab 9) was installed in `.git/hooks`, but the `pre-commit` package wasn't in the environment, so the hook couldn't start and Git **aborted the commit**.
**Fix:** `uv add --dev pre-commit` (or `uv sync --all-groups` if already listed), then `uv run pre-commit install`.

### P3 — `git reset --hard` deleted 35 uncommitted files, and how they came back

**What happened:** the commit had silently failed (P2). The following commands ran anyway:

```
git push -u origin HEAD                         → pushed an EMPTY branch
gh pr create …                                   → "Warning: 35 uncommitted changes … could not find any commits"
git switch master ; git fetch origin ; git reset --hard origin/master
```

`reset --hard` discarded **all 35 changed files** of PR A.

**Recovery:** the last **dev deploy** had uploaded the complete project to the workspace:

```powershell
git switch feat/wheel-packaging
databricks workspace export-dir "/Workspace/Users/<you>/.bundle/energy-lakehouse-de/dev/files" C:\dev\recover -p free
Select-String -Path C:\dev\recover\src\energy_lakehouse\contracts.py -Pattern "importlib"   # proves it's the latest state
robocopy C:\dev\recover C:\dev\energy-lakehouse-de /E /XD .git .venv dist build .databricks
git status --short | Measure-Object       # ~35 again
uv sync ; uv run ruff format . ; uv run pytest -q
```

Other recovery options worth knowing: **VS Code Timeline** (local history per file), and staged files remaining as dangling objects in Git (`git fsck --lost-found`, tedious).

**Lessons:**
- **Stop at the first error.**
- **Verify each commit** (`git log --oneline -1`, `git status` = 0) before push, PR or reset.
- **Never `reset --hard` with a dirty working tree.**
- A deployed copy is a real backup, one more reason to deploy dev often.

### P4 — Prod deploy failed: `dashboard "energy_market_de" has been modified remotely`

```
Error: dashboard "energy_market_de" has been modified remotely
This dashboard has been modified remotely since the last bundle deployment.
These modifications are untracked and will be overwritten on deploy.
To overwrite the remote changes with your local version, use --force.
```

**Cause:** the prod dashboard had been changed in the UI after the last deploy (publishing, edits or saved draft changes). The bundle refuses to overwrite untracked changes, which is a safety feature.
**Decision rule:** the **repo wins** (dashboards come only from code). Intended changes are made in the **dev** UI → export → PR.
**Fix:** an **opt-in, approval-gated** force deploy, never a permanent `--force`:

```yaml
on:
  release: { types: [published] }
  workflow_dispatch:
    inputs:
      tag:   { description: "Release tag to deploy", required: true }
      force: { description: "Overwrite remote dashboard changes", type: boolean, default: false }
```

```powershell
gh workflow run deploy-prod.yml --ref master -f tag=v0.6.0 -f force=true
```

`--ref master` takes the **workflow file** from master (with the `force` input); `-f tag=v0.6.0` deploys the **code** of that release. After this, the normal release `v0.6.1` deployed without force.

The deploy also warned *"'dashboards' already has permissions set for …"*: the dashboard resource repeated permissions already granted at target level. Keep only what's specific to the dashboard (the consumer group).

### P5 — `HTTP 422: Unexpected inputs provided: ["force"]`

**Cause:** the PR adding the `force` input wasn't merged yet, so `master`'s workflow had no such input. Check what master really contains:

```powershell
git --no-pager show origin/master:.github/workflows/deploy-prod.yml | Select-String -Pattern "workflow_dispatch|inputs|force" -Context 0,3
gh pr view <n> --json state,mergedAt
```

### P6 — Released `v1.0.0` before the docs PR was merged

**Symptom:** `gh pr create` for the docs PR, then immediately `git switch master … reset --hard origin/master` and `gh release create v1.0.0`. The "Project 1 complete" release didn't contain the documentation.
**Fix:** reject or cancel the triggered prod deploy if it's still waiting, delete the fresh release and tag, merge the PR, and create `v1.0.0` again:

```powershell
gh release delete v1.0.0 --cleanup-tag --yes
git fetch --prune --prune-tags origin
```

(Deleting a release is acceptable only minutes after creating it, when nothing depends on it. Otherwise, fix forward with a new version.)

**Release checklist:**
1. `gh pr view <n> --json state` → `MERGED`
2. `git ls-tree` on `origin/master` shows a file from the PR
3. staging ✓
4. only then `gh release create`

### P7 — The pre-commit hook refused a commit on master, which is correct

```
Do not commit on master: create a feature branch (git switch -c feat/...)....Failed
```

**Fix:** `git switch -c fix/<name>` takes the staged changes along; commit there. The hook prevented the exact mistake from Labs 5, 7 and 9.

### P8 — README showed the diagram but no screenshots: wrong filenames

**Cause:** the README referenced invented names (`docs/img/readme-dashboard-market.png`) while the files were `docs/img/market_page.jpg`, etc. GitHub is also **case-sensitive**.
**Fix:** map the links to the real filenames (a PowerShell `Replace`), and check them with `Test-Path` (Step C3).

### P9 — Screenshots still invisible: they were inside an HTML comment

```markdown
<!--
Screenshots: add the files to docs/img/ and uncomment.
![Dashboard: Market](docs/img/market_page.jpg)
...
-->
```

**Fix:** remove the comment markers, add a caption per image, and put one hero image under the pitch.

### P10 — Retrain after changing environments

Not an error I hit, but a deliberate step: after switching job environments to the wheel, the prod champion was **retrained** before scoring, to avoid the Lab 7 pickle mismatch.

### P11 — "Why not share the dashboard link publicly?"

AI/BI dashboards are shared with users **inside** a Databricks account; recipients must sign in. Free Edition isn't meant for public showcases, and external embedding requires your own app with token handling. **Better for recruiters:** screenshots in the README, a 2–3 minute **video walkthrough** (unlisted YouTube or a video in the README), a PDF export for emails, and **live demos** in interviews.

### Follow-ups found while taking screenshots (plausibility check)

| Observation | Explanation | Follow-up |
| --- | --- | --- |
| Renewables **101 %** on 25 Oct | Probably real: on a stormy Sunday, wind + solar can exceed domestic load (exports) | Label the KPI "% of load"; mention it in the README |
| Freshness shows energy data **in the future** | Day-ahead **prices** for tomorrow are already published | Measure freshness as the latest hour **with load** |
| A single spike in the generation mix around 25/26 Oct | Probably the **DST double hour** (02:00 local twice), summed by the chart | Plot by `hour_utc`, or aggregate the duplicated hour |
| Accuracy rows with fewer hours than expected | The forecast day isn't fully published yet | Add an `is_final` column |

---

## 9. What you learned

- **Packaging** Python for Databricks: wheels, runtime vs. dev dependencies, **package data** with `importlib.resources`
- **Testing the artifact**, not just the code (inspecting the built wheel)
- Retraining after environment changes
- Handling **configuration drift** in prod (dashboard modified remotely) with an approval-gated opt-in force
- Git safety under pressure: verified commits, never `reset --hard` dirty, recovery from a deployed copy
- **Release discipline:** merge first, then release; delete a release only when it's fresh and unused
- Writing **portfolio documentation**: hero image, results first, ADRs, runbook, changelog
- Why a public dashboard link isn't the answer, and what to show recruiters instead

**Interview-ready summary:** *"I packaged the lakehouse code as a wheel so every job installs one versioned artifact with dependencies from pyproject.toml, including the data contracts as package data, verified by a test that inspects the built wheel. I handled prod dashboard drift with an approval-gated opt-in force deploy instead of a permanent --force, and documented the system for two audiences: a recruiter-friendly README with results and screenshots, and ADRs plus a runbook for engineers. I also recovered 35 lost files from the deployed bundle after a failed commit and a hard reset, and turned that into a commit-verification rule."*

---

## 10. Tested on

| Item | Version |
| --- | --- |
| Date | 2026-10-27/28 |
| OS | Windows 11 (local), ubuntu-latest (GitHub runners) |
| Packaging | uv build --wheel, contracts as package data |
| Tests | 384 passing |
| Releases | v0.6.0 (prod deploy blocked by dashboard drift, force-deployed), v0.6.1, v1.0.0 |
| Databricks CLI | `<output of databricks -v>` |

**Next:** Lab 10b — extract a reusable project template (Copier or Databricks bundle template) and a shared package for contracts, anomaly checks and guard tests, so Projects 2 and 3 start in half a day.
