# Lab 1 — Deploy and run the hello_job smoke test

> **Status:** placeholder — fill in results, troubleshooting notes and screenshots after running.

## Goal
Deploy the bundle to the `dev` target and run `hello_job` on serverless compute. The job creates
schema `workspace.energy_dev` if missing and appends a timestamp row to `_smoke_test`.

## Prerequisites
- Lab 0 completed (`databricks bundle validate -t dev -p free` is OK)

## Steps

### Step 01 — Deploy to dev
- **Action:** Upload files and create the job (development mode prefixes it with `[dev <you>]`).
- **Command:**
  ```powershell
  databricks bundle deploy -t dev -p free
  ```
- **Expected result:** `Deployment complete!`
- **Screenshot:** `docs/img/lab1-step01-deploy.png`

### Step 02 — Run the job
- **Action:** Trigger `hello_job` and wait for it to finish.
- **Command:**
  ```powershell
  databricks bundle run hello_job -t dev -p free
  ```
- **Expected result:** A run URL is printed and the run ends `TERMINATED SUCCESS`. The notebook
  output shows ``` `workspace`.`energy_dev`.`_smoke_test` row count: 1 ```.
- **Screenshot:** `docs/img/lab1-step02-run.png`

### Step 03 — Inspect the table
- **Action:** Query the table in the SQL editor.
- **Command:**
  ```sql
  SELECT * FROM workspace.energy_dev._smoke_test ORDER BY run_ts DESC;
  ```
- **Expected result:** One row per job run, each with its run timestamp.
- **Screenshot:** `docs/img/lab1-step03-table.png`

## Checkpoint
- [ ] Job run succeeded
- [ ] `workspace.energy_dev._smoke_test` exists with at least 1 row

## Troubleshooting
- _TBD — record errors hit while running this lab._

## What you learned
- _TBD_

## Tested on
- _TBD_
