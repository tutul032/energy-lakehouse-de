# 3. One shared raw zone, one schema per environment

- **Status:** Accepted
- **Date:** 2026-10-21 (recorded 2026-10-27)

## Context
dev, staging and prod all live in one Free Edition workspace and one Unity Catalog catalog
(`workspace`). The source data is identical for every environment, and extraction runs once a
day outside Databricks (ADR 0002). Running three extractions would triple API calls and could give
each environment slightly different raw data.

## Decision
- Raw files land once, in the volume `workspace.energy_raw.landing` (bundle variable
  `landing_root`). It is created manually, not by the bundle, so a `bundle destroy` cannot delete
  raw history.
- Each target builds its own tables in its own schema: `energy_dev`, `energy_staging`,
  `energy_prod` (bundle variable `schema`). Code never hardcodes a schema; notebooks and the
  pipeline get catalog/schema as parameters.
- staging/prod are deployed only by CI as the CI/CD service principal, which owns their schemas.
  Resource names get a per-target prefix (`[staging] `, `[prod] `) because all targets share one
  workspace, and staging/prod use a bundle root private to the deploying identity.

## Consequences
- Every environment processes the same raw files, so differences between environments come from
  code, not data.
- Readers need `READ VOLUME` and writers `WRITE VOLUME` on the shared volume; these grants are
  managed outside the bundle.
- A broken upload affects all environments at once. The upload is verified per source, and silver
  deduplicates overlapping files.
- With one workspace per environment, the name prefix would not be needed and each workspace could
  have its own landing volume.
