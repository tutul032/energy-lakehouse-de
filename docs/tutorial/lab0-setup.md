# Lab 0 — Local setup and Databricks CLI profile

## Goal
Prepare a Windows 11 machine to develop, test and deploy this bundle to Databricks Free Edition.

## Prerequisites
- Windows 11 with PowerShell
- A Databricks Free Edition account (workspace URL at hand)
- Git, and this repository cloned

## Steps

### Step 01 — Install uv
- **Action:** Install uv (it manages Python 3.12 and dependencies).
- **Command:**
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  uv --version
  ```
- **Expected result:** `uv x.y.z` is printed.
- **Screenshot:** `docs/img/lab0-step01-uv-version.png`

### Step 02 — Install project dependencies and run checks
- **Action:** Create `.venv` with Python 3.12 and the dev tools, then lint and test.
- **Command:**
  ```powershell
  uv sync
  uv run ruff check .
  uv run ruff format --check .
  uv run pytest
  ```
- **Expected result:** ruff prints `All checks passed!`; pytest reports all tests passed.
- **Screenshot:** `docs/img/lab0-step02-pytest.png`

### Step 03 — Install the Databricks CLI
- **Action:** Install the Databricks CLI (the version with `bundle` support).
- **Command:**
  ```powershell
  winget install Databricks.DatabricksCLI
  databricks --version
  ```
- **Expected result:** `Databricks CLI v...` is printed.
- **Screenshot:** `docs/img/lab0-step03-cli-version.png`

### Step 04 — Authenticate with profile `free`
- **Action:** OAuth login. The host is stored in `~/.databrickscfg`, never in the repo.
- **Command:**
  ```powershell
  databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile free
  databricks current-user me -p free
  ```
- **Expected result:** Browser login succeeds; `current-user me` prints your user as JSON.
- **Screenshot:** `docs/img/lab0-step04-auth.png`

### Step 05 — Validate the bundle
- **Action:** Check bundle syntax and variable resolution against the workspace.
- **Command:**
  ```powershell
  databricks bundle validate -t dev -p free
  ```
- **Expected result:** `Validation OK!`, bundle `energy-lakehouse-de`, target `dev`.
- **Screenshot:** `docs/img/lab0-step05-validate.png`

## Checkpoint
- [ ] `uv run pytest` passes
- [ ] `databricks current-user me -p free` returns your user
- [ ] `databricks bundle validate -t dev -p free` prints `Validation OK!`

## Troubleshooting
- **`python` opens the Microsoft Store:** use `uv run python`; uv manages Python 3.12. Optionally
  disable the alias in *Settings → Apps → Advanced app settings → App execution aliases*.
- **`databricks` not found after winget:** open a new PowerShell window so `PATH` refreshes.
- **Auth errors during validate:** check you passed `-p free` and that the profile exists
  (`databricks auth profiles`).

## What you learned
- uv manages the Python version and a locked dev environment.
- The CLI profile carries the workspace host and credentials, keeping the repo secret-free.
- `bundle validate` checks the bundle before anything is deployed.

## Tested on
- Windows 11 Pro, uv 0.13.1, Databricks CLI v1.21.0 — date: _TBD_
