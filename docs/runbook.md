# Runbook

Operational fixes for energy-lakehouse-de. Commands are PowerShell-compatible.

## Dashboard modified remotely

**Symptom.** `deploy-prod` (or `deploy-staging`) fails in `databricks bundle deploy` with:

```
Error: dashboard "energy_market_de" has been modified remotely
This dashboard has been modified remotely since the last bundle deployment.
```

**Cause.** Someone edited the deployed dashboard in the workspace UI (e.g. `[prod] Energy Market
DE`), so its remote state no longer matches what the last deploy wrote. The bundle refuses to
overwrite those edits without `--force`.

**Decision rule: the repo wins.** The deployed dashboard is a build output of
`src/energy_market_de.lvdash.json`; edits in the staging/prod UI are never kept.

1. If the remote change is **not wanted** (an experiment or an accident): redeploy with `--force`
   (below). It overwrites the remote edits.
2. If the change **is wanted**: do not export from prod. Port it through the normal dashboard flow
   (CLAUDE.md, "AI/BI dashboard"): make the same change in the **dev** UI, export the source JSON,
   run `uv run python scripts/build_dashboard.py` and `uv run pytest`, then open a PR, merge and
   cut a release. Then redeploy prod with `--force`, because the remote edit is still there and
   blocks the deploy either way.

**Force deploy (prod).** GitHub → Actions → `deploy-prod` → *Run workflow*: `tag` = the release
to deploy (e.g. `v1.2.0`), check `force`. The `production` environment approval still applies.
Release-triggered runs never use `--force`. The workflow runs:

```powershell
databricks bundle deploy -t prod --force
```

Never run it locally: staging/prod are deployed only by CI as the CI/CD service principal.

**Prevent it.** Consumers (`energy-consumers`) get only `CAN_READ` on the dashboard. `CAN_MANAGE`
is limited to the CI/CD SP and the `alert_email` owner (target permissions in `databricks.yml`);
edit in dev only.
