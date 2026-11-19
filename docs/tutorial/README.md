# Tutorial

Step-by-step labs that build the project from an empty repository. Each lab follows one template:
Goal, Concepts, Prerequisites, Steps with expected results, Checkpoint, Troubleshooting (problems I
actually hit, with logs and fixes), What you learned, Tested on. Most labs end at a git tag, so you can
compare your work with the reference state.

For the big picture (architectures, glossary, lab lifecycle, prompting Claude Code, exam), read the
[handbook](../handbook/README.md) first.

| Lab | Topic | Tutorial | Reference state |
|---|---|---|---|
| 0 | Environment: Windows tools, uv, Databricks CLI, GitHub, bundle with dev/staging/prod, first job, CI | [lab0-environment-setup.md](lab0-environment-setup.md) | — |
| 1 | SMARD extraction into a Unity Catalog volume (idempotent, offline-tested) | [lab1-smard-extraction.md](lab1-smard-extraction.md) | — |
| 2 | Lakeflow declarative pipeline: Auto Loader bronze, silver with expectations, gold | [lab2-declarative-pipeline.md](lab2-declarative-pipeline.md) | [`lab2-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab2-complete) |
| 3 | Open-Meteo weather, upload script with verification, energy × weather gold, merit-order effect | [lab3-weather-ingestion.md](lab3-weather-ingestion.md) | [`lab3a-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab3a-complete) |
| 4 | Automated daily refresh: GitHub Actions, service principal, Lakeflow Job with quality checks | [lab4-automated-daily-refresh.md](lab4-automated-daily-refresh.md) | [`lab4-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab4-complete) |
| 5 | CI/CD dev → staging → prod: shared raw zone, approval-gated releases, branch protection | [lab5-cicd-dev-staging-prod.md](lab5-cicd-dev-staging-prod.md) | [`lab5-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab5-complete) |
| 6 | Data contracts: YAML contracts, CI breaking-change guard, runtime verification | [lab6-data-contracts.md](lab6-data-contracts.md) | [`lab6-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab6-complete) |
| 7 | Day-ahead forecast: leakage-safe features, MLflow, UC model registry, batch scoring | [lab7-day-ahead-forecast.md](lab7-day-ahead-forecast.md) | [`lab7-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab7-complete) |
| 8 | ML-assisted data quality: anomaly detectors, severity design, LLM daily report | [lab8-ml-data-quality.md](lab8-ml-data-quality.md) | [`lab8-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab8-complete) |
| 9 | AI/BI dashboard as code per environment, Genie agent with benchmarks | [lab9-dashboard-genie.md](lab9-dashboard-genie.md) | [`lab9-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab9-complete) |
| 10 | Wheel packaging, cleanup, portfolio documentation | [lab10-docs-packaging-cleanup.md](lab10-docs-packaging-cleanup.md) | [`lab10-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab10-complete) |
| 11 | Docker: extractor image, GHCR, exact-digest promotion, dev container | [lab11-docker.md](lab11-docker.md) | [`lab11-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab11-complete) |
| 12 | Terraform: adopt the Unity Catalog setup (imports, zero diff, drift test), first reviewed change | [lab12-terraform.md](lab12-terraform.md) | [`lab12-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab12-complete) |
| 13 | Azure foundation: remote Terraform state, GitHub OIDC, Key Vault, plan on PRs, weekly drift alarm | [lab13-azure-foundation.md](lab13-azure-foundation.md) | [`lab13-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab13-complete) |

Short early notes (superseded by Lab 0): [lab0-setup.md](lab0-setup.md), [lab1-hello-job.md](lab1-hello-job.md).

Related: [handbook](../handbook/README.md), [architecture decisions](../adr/), [runbook](../runbook.md),
[data products](../data-products/).
