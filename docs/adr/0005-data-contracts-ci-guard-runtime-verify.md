# 5. Data contracts with a CI guard and runtime verification

- **Status:** Accepted
- **Date:** 2026-10-22 (recorded 2026-10-27)

## Context
Gold tables are consumed by a dashboard, a Genie agent and forecasting code. A pipeline change
that renames or retypes a column would break them silently. Column comments, tags and grants set
by hand drift over time.

## Decision
- One YAML contract per consumed table in `contracts/` (ODCS-inspired; parsed with pydantic,
  unknown keys rejected). It holds owner, grain, primary key, SLA (`freshness_hours`), consumer
  groups, columns (name, type, nullable, description) and quality rules (`unique`, `not_null`,
  `row_condition` with an allowed violation percentage). Versions follow semver: removing,
  renaming or retyping a column is MAJOR; a new column is MINOR; descriptions, rules and SLA
  wording are PATCH.
- **CI guard** (`tests/test_contracts.py`): fails unless each contract's columns, types and
  descriptions equal the column definitions in `transforms.py` that the pipeline uses, and unless
  the generated product pages (`docs/data-products/`) are up to date.
- **Runtime** in `daily_refresh_job`: `apply_contracts` sets table/column comments and tags where
  they differ and grants `SELECT` to consumer groups; `verify_contracts` checks
  `information_schema` names and types, the quality rules and freshness, and fails the job
  listing every violation.
- Status gates enforcement: only `active` and `deprecated` contracts are applied and verified;
  `draft` contracts (tables that may still be empty) are skipped.

## Consequences
- A breaking schema change cannot be merged without updating the contract and its version in the
  same PR.
- Drift in the workspace (comments, tags, missing grants) is corrected daily; violations stop the
  job before forecasts and anomalies are computed on bad data.
- Removed grants and tags are not revoked automatically, and consumer groups must exist before the
  grant runs, or the job fails.
