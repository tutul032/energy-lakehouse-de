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
src/notebooks/              # Databricks notebooks (source format .py)
tests/                      # pytest tests for src/energy_lakehouse
docs/adr/                   # architecture decision records (NNNN-title.md)
docs/tutorial/              # step-by-step labs (labN-*.md)
docs/img/                   # screenshots: labN-stepNN-name.png
.github/workflows/ci.yml    # lint + test (no deploy yet)
```

## Commands (PowerShell)
```powershell
uv sync                                    # create .venv, install dev deps
uv run ruff check .                        # lint
uv run ruff format .                       # format (CI runs --check)
uv run pytest                              # unit tests

databricks bundle validate -t dev -p free
databricks bundle deploy   -t dev -p free
databricks bundle run hello_job -t dev -p free
databricks bundle destroy  -t dev -p free  # tear down dev resources
```
Targets: `dev` (default, development mode, schema `energy_dev`), `staging` (`energy_staging`),
`prod` (production mode, schema `energy_prod`, root `/Workspace/Shared/.bundle/<name>/<target>`).

## Conventions
- **Serverless only.** Never add `new_cluster`, `job_clusters`, `existing_cluster_id` or cluster
  policies. Jobs omit compute config; pipelines use `serverless: true`.
- **All tables live in catalog `workspace`**, schema per target via `${var.catalog}` /
  `${var.schema}`. Never hardcode a schema in notebooks/SQL; pass it as a job/pipeline parameter.
- Build table names with `energy_lakehouse.utils.table_name()` (backtick-quoted names).
- **No hardcoded hosts, tokens or secrets.** The host comes from the CLI profile; secrets go in
  Databricks secret scopes (later: GitHub OIDC for CI deploys). `.env` is gitignored.
- **PowerShell-compatible commands** in docs and instructions: no bash-only syntax
  (`&&` in PS 5.1, `export`, `\` line continuations).
- **Small, pure, testable functions in `src/energy_lakehouse/`**; notebooks stay thin and call
  them. Every new function gets a pytest test.
- Ruff line length 100; CI must pass `ruff check`, `ruff format --check`, `pytest`.
- Do not run `databricks bundle deploy/run/destroy` on the user's behalf — the user runs
  checkpoints.
- Tutorial labs follow the template: Goal, Prerequisites, Steps (action → command → expected
  result → screenshot `labN-stepNN-name.png`), Checkpoint, Troubleshooting, What you learned,
  Tested on.
