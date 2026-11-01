# Tutorial

Step-by-step labs that build the project from an empty repository. Each lab ends at a git tag
(`labN-complete`), so you can compare your work with the reference state. Labs follow one
template: Goal, Prerequisites, Steps, Checkpoint, Troubleshooting, What you learned, Tested on.

| Lab | Topic | Write-up | Reference state |
|---|---|---|---|
| 0 | Local setup: uv, Python 3.12, Databricks CLI profile `free`, `bundle validate` | [lab0-setup.md](lab0-setup.md) | — |
| 1 | First deploy: run a smoke-test job on serverless compute | [lab1-hello-job.md](lab1-hello-job.md) | — |
| 2 | SMARD extractor and Lakeflow pipeline bronze/silver/gold with expectations | pending | [`lab2-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab2-complete) |
| 3 | Open-Meteo weather ingestion, energy + weather gold table, upload script with verification | pending | [`lab3a-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab3a-complete) |
| 4 | Daily refresh: GitHub Actions extraction, service principal auth, Lakeflow Job with quality checks | pending | [`lab4-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab4-complete) |
| 5 | Environments and CI/CD: dev/staging/prod, shared raw zone, approval-gated prod, branch protection | pending | [`lab5-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab5-complete) |
| 6 | Data contracts: YAML contracts, CI guard, apply/verify tasks, generated product pages | pending | [`lab6-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab6-complete) |
| 7 | Day-ahead forecast: leakage-safe features, MLflow, UC model registry, batch scoring, accuracy | pending | [`lab7-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab7-complete) |
| 8 | ML-assisted data quality: anomaly detectors, severity design, injection test, LLM summary | pending | [`lab8-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab8-complete) |
| 9 | AI/BI dashboard per environment and Genie agent with benchmarks | pending | [`lab9-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab9-complete) |
| 11 | Dev container: pinned Python, uv, Databricks CLI and GitHub CLI, logins inside, tests in CI | pending | [`lab11-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab11-complete) |
| 12 | Terraform: adopt the Unity Catalog platform with imports (zero-diff plan), additive grants, first reviewed change | pending | [`lab12-complete`](https://github.com/tutul032/energy-lakehouse-de/tree/lab12-complete) |

Related: [architecture decisions](../adr/), [runbook](../runbook.md),
[data products](../data-products/).
