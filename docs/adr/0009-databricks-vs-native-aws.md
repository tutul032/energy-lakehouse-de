# 9. Databricks instead of native AWS services

- **Status:** Accepted
- **Date:** 2026-10-16 (recorded 2026-10-27)

## Context
The same lakehouse could be built from native AWS services. The project has to run at no cost, be
reproducible by a reviewer, and show one platform end to end, including governance.

| Concern | This project (Databricks) | Native AWS equivalent |
|---|---|---|
| Ingestion | Auto Loader on a UC volume | S3 events + Glue / Lambda |
| Transformation | Lakeflow Spark Declarative Pipelines with expectations | Glue jobs / EMR + Glue Data Quality |
| Orchestration | Lakeflow Jobs | Step Functions / MWAA |
| Governance | Unity Catalog (grants, tags, comments, lineage) | Lake Formation + Glue Data Catalog |
| ML | MLflow + UC model registry, batch scoring | SageMaker (training, registry, batch transform) |
| BI / NL query | AI/BI dashboard + Genie | QuickSight (+ Q) |
| Deployment | Databricks Asset Bundles | CloudFormation / CDK / Terraform |

## Decision
Build on Databricks Free Edition (serverless only, Unity Catalog), deployed with Databricks Asset
Bundles from GitHub Actions. Extraction runs in GitHub Actions (ADR 0002).

## Consequences
- One platform covers ingestion, governance, ML and BI; a single bundle describes all resources
  per environment.
- Free Edition constraints shape the design: no outbound internet (ADR 0002), no account-level
  APIs (OAuth secrets instead of OIDC), one workspace for all environments (ADR 0003), serverless
  only.
- Native AWS services and OIDC-based deployment are covered in a separate AWS chapter, not in
  this repository.
