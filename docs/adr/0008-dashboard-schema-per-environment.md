# 8. One dashboard source, schema set per environment

- **Status:** Accepted
- **Date:** 2026-10-25 (recorded 2026-10-27)

## Context
The AI/BI dashboard is edited in the workspace UI, which needs fully qualified table names in
dataset SQL. The same dashboard must show dev, staging or prod data depending on the target, and
an edit must never point prod at dev tables.

## Decision
- The dashboard is edited in dev only, exported with `databricks bundle generate dashboard`, and
  committed as `src/energy_market_de.lvdash.json` (tables as `workspace.energy_dev.<table>`).
- `scripts/build_dashboard.py` writes one file per target to `build/`, replacing
  `workspace.energy_dev.` with `workspace.<schema>.` of that target. Each target points the
  dashboard resource's `file_path` at its built file; the resource also sets `dataset_catalog` /
  `dataset_schema` from the bundle variables.
- `tests/test_dashboard.py` checks that the source names only `workspace.energy_dev.`, that each
  built file names only its own schema, and that every dataset is used by a widget. CI builds the
  files before `bundle validate` and `bundle deploy`.

## Consequences
- One reviewed source, three dashboards that each read only their own schema.
- Edits made directly in the staging/prod UI are overwritten (the repo wins) and block deploys
  until forced (`docs/runbook.md`, "Dashboard modified remotely").
- The Genie agent is not a bundle resource; it is recreated per environment from
  `docs/genie/ask-the-energy-lakehouse.md`.
