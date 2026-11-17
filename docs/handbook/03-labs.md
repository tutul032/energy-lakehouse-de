# Part III — The labs

Each section has the same structure:
- **Goal**
- **Where it sits** in both architectures
- **Start → finish** (the lifecycle from Chapter 3, with the lab-specific parts)
- **Key prompt**, shortened (the full prompts are in the tutorial)
- **Checkpoint**
- **Typical errors**
- **Exam questions**

Always use the full tutorial (`docs/tutorial/labN-*.md`) for the actual work.

---

## Lab 0 — Environment setup

**Goal:** a repo with a Databricks Asset Bundle (dev/staging/prod), tests, lint and CI, and the CLI authenticated.
**Where:** CI/CD skeleton only.
**Start → finish:** install tools (winget: Git, gh, uv, Databricks CLI, VS Code) → `databricks auth login` → `gh repo create` → scaffold with Claude Code → `bundle deploy -t dev` → a hello job → CI green → tag.

**Key prompt (short):**

```text
Scaffold a Databricks bundle repo: CLAUDE.md, databricks.yml (dev/staging/prod, host only), a serverless hello job,
a pure function + pytest, pyproject (uv, ruff, pytest), .gitignore, a CI workflow (ruff + pytest), docs/ skeleton.
```

**Checkpoint:** `Validation OK!`, the hello job `SUCCESS`, CI green on GitHub.
**Typical errors:**
- a command not found after install (reopen the terminal)
- `gh auth login` asks for a token (use `--web`)
- CRLF noise (`core.autocrlf input`)

**Exam**
1. *What does a bundle target define?* Where and how resources deploy (host, schema, mode, permissions).
2. *Why `host:` and not `profile:` in the bundle?* Profiles are local to your laptop; CI and service principals have none (Lab 4).
3. *Why CI from day one?* Quality checks are cheapest at the start and become habits.

---

## Lab 1 — Extraction to a landing zone

**Goal:** download SMARD data raw and unchanged into a Unity Catalog volume, idempotently.
**Where:** Sources → Extract → Landing.
**Start → finish:** test outbound internet → extractor (pure Python, retries, skip existing) → offline tests → local smoke test → upload (`databricks fs cp`) → `read_files` check.

**Key prompt (short):**

```text
Add a Spark-free SMARD extractor: URL builders, fetch index, select latest N chunks, output_path with hive-style folders,
extract() that writes raw JSON unchanged, skips existing files, retries 3x; offline tests with fixtures; local CLI.
```

**Checkpoint:** a rerun reports `written: 0, skipped: N`; `read_files` shows the expected files per series.
**Typical errors:**
- no outbound internet on Free Edition (use the laptop/GitHub fallback)
- quarter-hour vs. hourly series

**Exam**
1. *Why keep raw data unchanged?* So later layers can be rebuilt if logic changes.
2. *Why Hive-style folders (`filter=410/`)?* The folder names become columns and lineage.
3. *What makes an extractor idempotent?* Deterministic file names plus skipping existing files.

---

## Lab 2 — Auto Loader and the medallion pipeline

**Goal:** bronze (Auto Loader) → silver (clean, expectations, dedupe) → gold (hourly/daily KPIs).
**Where:** Bronze → Silver → Gold.
**Start → finish:** pipeline code + pure helpers → deploy → run → check data quality per layer → prove incrementality (rerun: 0 new rows).

**Key prompt (short):**

```text
Add a Lakeflow declarative pipeline: bronze streaming table (cloudFiles json, multiLine, explicit schema, lineage columns),
silver MV (explode, typed timestamps, dedupe newest file wins, null-safe expectations), gold hourly/daily (SUM MWh, AVG price,
local time, renewables share). Pure helpers unit-tested.
```

**Checkpoint:** expectations visible in Table metrics; a rerun writes 0 bronze rows; gold values plausible.
**Typical errors:**
- `timestampNtz` table feature (Lab 2 T2)
- a null-unsafe expectation double-counting (T4)
- no URL printed after `bundle run`

**Exam**
1. *Streaming table vs. materialized view?* Only new input vs. kept up to date from all inputs.
2. *Why null-safe rules?* All expectations evaluate on the same rows; `NULL` counts as a failure.
3. *How do you prove Auto Loader is incremental?* Rerun without new files: bronze writes 0 rows.

---

## Lab 3 — A second source (weather)

**Goal:** add Open-Meteo weather, join it with energy in gold, show the merit-order effect.
**Where:** Sources → … → Gold (join).
**Start → finish:** weather extractor → sync script (verifies uploads) → bronze/silver/gold weather → join → analysis query.

**Checkpoint:** gold energy × weather has weather for almost every hour; night-only wind-vs-price shows falling prices.
**Typical errors:**
- a silent upload failure (gold had 0 weather hours)
- seconds vs. milliseconds
- column names differing from the tutorial

**Exam**
1. *Why did gold show 0 weather hours although the pipeline was green?* The files were never uploaded; Auto Loader correctly found nothing.
2. *Why filter night hours for the merit-order chart?* To remove solar as a confounding factor.
3. *What makes a script "fail loudly"?* Checking every command's exit code and verifying results.

---

## Lab 4 — Automated daily refresh

**Goal:** a scheduled GitHub Actions workflow extracts, uploads (as a service principal) and runs a Lakeflow Job with quality checks.
**Where:** CI/CD: scheduled refresh; identities.
**Start → finish:** service principal + OAuth secret → minimal grants → test outside the repo → job + checks + workflow → GitHub secrets → manual run → scheduled run.

**Checkpoint:** a run with event `schedule` is green; a deliberately failing check sends an email.
**Typical errors:**
- `invalid_client` because the bundle's `profile:` mixed identities
- the secret leaked in a chat (rotate it)
- GitHub cron delays (use an off-peak minute)

**Exam**
1. *Why a service principal?* A stable, least-privileged identity, independent of a person.
2. *OIDC vs. OAuth secret?* Token exchange without stored secrets vs. a stored secret. Free Edition lacks the account APIs needed for OIDC.
3. *Why `CAN_MANAGE_RUN` for the refresh SP?* It may start the job, not change it.

---

## Lab 5 — CI/CD dev → staging → prod

**Goal:** PR checks, automatic staging, approval-gated prod releases, a shared raw zone, a CI/CD service principal, branch protection.
**Where:** the whole delivery architecture.
**Start → finish:** raw zone + grants → CI/CD SP owning staging/prod schemas → bundle targets (name prefixes, private root path, no `run_as`) → workflows → environment `production` + ruleset → PR → staging → release → prod.

**Checkpoint:** a direct push to master is rejected; staging deploys on merge; prod waits for approval.
**Typical errors (nine in the tutorial):**
- a checkpoint bound to the old path
- placeholders
- the **Application ID vs. secret** mix-up
- a pipeline name collision
- a release created before its workflow existed

**Exam**
1. *Why a shared raw zone?* Raw facts are the same for every environment; each environment builds its own curated layers.
2. *What does `***` in a GitHub log tell you?* The value equals a secret. If an "ID" shows as `***`, it's probably a secret.
3. *Why fix forward with a new version?* Releases are history, and moving tags confuses consumers.

---

## Lab 6 — Data contracts

**Goal:** an ODCS-style contract per gold table, a CI breaking-change guard, runtime verification, and governance generated from contracts.
**Where:** the Contracts box; a CI guard.
**Start → finish:** remove unused access → consumer group → contracts + tooling → product pages → deploy → see tags/grants → prove the guard (rename a column) → prove the runtime check (SLA 0 h) → release.

**Checkpoint:** a renamed column fails CI; a broken SLA fails the job with an email.
**Typical errors:**
- Smart App Control blocking Python
- `git checkout --` deleting uncommitted work
- a guard that "never fails"
- a machine-specific setting breaking CI

**Exam**
1. *Contract vs. table vs. data product?* The promise / the storage / table + contract + owner + docs + access.
2. *Which change is MAJOR?* Renaming or removing a column, or changing a type or the grain.
3. *Why must code own its column list?* Otherwise the test compares the contract with itself and can't fail.

---

## Lab 7 — Day-ahead forecast

**Goal:** leakage-safe features, a baseline, gradient boosting in MLflow, UC models with a champion alias, daily scoring and accuracy.
**Where:** the Forecast box; train/score jobs.
**Start → finish:** backfill history + weather forecast (`run_ts` files) → features/training/registry/scoring → leakage test fails on purpose → train dev → registry → score → sanity check → release → **train prod**, then score.

**Checkpoint:** the model beats the baseline (load MAPE 2.37 % vs. 3.31 %); 24 rows per target for tomorrow.
**Typical errors:**
- idempotent file names for forecasts
- `%pip` + `restartPython` breaking Spark
- a NumPy pickle mismatch
- UTC `current_date()`
- scoring before training in a new environment

**Exam**
1. *Why lags ≥ 48 h?* At issue time, newer actuals don't exist yet. Shorter lags leak.
2. *Why a baseline?* A model is only worth running if it beats something simple.
3. *Why must train and score share an environment?* Pickled models depend on library versions.

---

## Lab 8 — ML-assisted data quality

**Goal:** seasonal robust z-scores, Isolation Forest, completeness, physics rules, forecast drift, severity design, and an LLM daily report.
**Where:** the Anomaly detection box; alerting.
**Start → finish:** detectors + tests → run dev → **explain** findings → redesign severity → injected anomaly → LLM endpoint → release (merge first!).

**Checkpoint:** 0 critical in prod; warnings ranked; a TEST injection shows up without failing the job.
**Typical errors:**
- 28 "critical" findings that were the real autumn shift
- job vs. task parameters
- concurrent runs
- releasing before merge
- the leakage guard on the DST night

**Exam**
1. *Why median/MAD instead of mean/standard deviation?* Robust to single outliers.
2. *Unusual vs. broken?* Seasonal deviations are warnings; impossible or missing data is critical.
3. *Why never fail the job on an LLM error?* The LLM report is a convenience, not the source of truth.

---

## Lab 9 — Dashboard as code and Genie

**Goal:** a 4-page AI/BI dashboard deployed per environment, and a Genie agent with instructions, examples and benchmarks.
**Where:** the Consumers box; dashboard as code.
**Start → finish:** datasets (unqualified names, explicit schema) → widgets → export → commit the YAML → tests → deploy → Genie (sources, instructions, examples, benchmarks) → release → **run prod** → publish.

**Checkpoint:** each environment shows its own data; KPIs use complete days; Genie 6/6.
**Typical errors:**
- `DESCRIBE` in a dataset
- counters showing 1
- the assistant qualifying table names
- `generate --force` overwriting the YAML
- 596 GWh (completeness)
- a cross-table rule needing `sql_check`
- prod KPIs failing until the pipeline ran

**Exam**
1. *How does one dashboard file serve three environments?* Unqualified names + `dataset_catalog`/`dataset_schema` per target.
2. *How was the 596-GWh bug found?* A plausibility check against domain knowledge, not a failing test.
3. *How do you test Genie?* Benchmark questions with known answers, comparing Genie's SQL.

---

## Lab 10 — Packaging, cleanup, documentation

**Goal:** a wheel (one dependency source, contracts as package data), clean CI, a recruiter-ready README, ADRs, runbook, `v1.0.0`.
**Where:** packaging; docs.
**Start → finish:** wheel + tests on the built artifact → deploy dev → release → **retrain prod** → cleanup PR → docs PR (exact image names, nothing commented out) → merge → **then** v1.0.0.

**Checkpoint:** the wheel contains the YAMLs; the README shows the hero image and screenshots; v1.0.0 contains the docs.
**Typical errors:**
- data files missing from the wheel
- the pre-commit package missing, so the commit failed
- `reset --hard` deleting work (recovered from the dev deploy)
- dashboard drift in prod
- releasing before merge
- wrong image names, images inside a comment

**Exam**
1. *Why did contracts break after packaging?* They were found relative to the module file, and they weren't inside the wheel.
2. *How do you recover lost local work after `reset --hard`?* From the deployed bundle files (`workspace export-dir`), VS Code Timeline, or Git's dangling objects.
3. *Why an opt-in force deploy and not a permanent `--force`?* Drift detection is a safety check; overriding it should be a conscious, approved decision.
