# 10. Terraform scope: platform only, adopted before changed

- **Status:** Accepted
- **Date:** 2026-10-31

## Context
The Unity Catalog platform (the schemas `energy_raw`, `energy_dev`, `energy_staging`,
`energy_prod`, the landing volume, the two service principals, the group `energy-consumers` and
their grants) was created by hand in the UI and SQL editor (runbook, README setup). Nothing
records it as code, so it cannot be reviewed, reproduced or checked for drift.

Three tools already write to Unity Catalog, and a grant written by two of them would be reset by
whichever runs last:

| Tool | Owns |
|---|---|
| Databricks Asset Bundle (`databricks.yml`, `resources/`) | jobs, pipeline, dashboard, their permissions; the tables the pipeline builds |
| `apply_contracts` (`04_apply_contracts.py`) | table comments and tags; `GRANT USE SCHEMA` on the target schema and `GRANT SELECT` on each table for every consumer group |
| Terraform (`terraform/`, this ADR) | everything below |

## Decision
- **Terraform manages the platform:** schemas, the landing volume, service principals and the
  consumer group (display name and entitlements), and the catalog, schema and volume grants of
  those principals. Bundles keep the data products; `apply_contracts` keeps the table grants and
  the consumer groups' `USE SCHEMA`.
- **Grants are additive:** only `databricks_grant` (one principal per securable), which is
  authoritative just for that principal. Never `databricks_grants` (authoritative for every
  principal on a securable) or `databricks_permissions`, so the `_workspace_users` catalog grant,
  grants by `apply_contracts` and grants made later by hand are left alone.
- **Adopt first, change later:** PR A only adds `import` blocks for the existing objects. The
  first plan must be zero-diff (17 to import, 0 to add/change/destroy); the config describes what
  exists, including system values (schema property `owner = root`). Changes go into later PRs,
  each with its own reviewed plan.
- **Safety rails:** `prevent_destroy` on schemas, the volume and the identities. No OAuth
  secrets or tokens in Terraform: secrets stay manual (runbook, "Secret rotation") and auth comes
  from the environment (`DATABRICKS_CONFIG_PROFILE`). Exact provider pin plus a committed
  `.terraform.lock.hcl`.
- **Local state for now; CI runs no plan.** The ci.yml `terraform` job runs `fmt -check`,
  `init -backend=false`, `validate` and tflint without credentials. Plan and apply run locally
  in the dev container by the workspace admin.

## Consequences
- The platform is reviewable as code, and `terraform plan` shows drift from the UI.
- Not managed: group membership (e.g. the refresh SP is a direct member of `admins`), the
  `workspace-consume` entitlement (the provider refuses it next to `workspace_access`, so it is in
  `ignore_changes`), the catalog itself, and OAuth secrets.
- Local state lives on one machine and is not shared or locked; a remote backend is a later step.
  Losing it is recoverable: the import blocks adopt everything again.
- A new environment schema or principal is added in `terraform/` (with `prevent_destroy`), never
  in the bundle; its consumer grants still come from the contracts.
