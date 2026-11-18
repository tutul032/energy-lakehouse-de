# 11. Remote Terraform state, a Terraform identity and an OIDC plan workflow

- **Status:** Accepted
- **Date:** 2026-11-17
- **Amends:** [ADR 0010](0010-terraform-scope.md) ("Local state for now; CI runs no plan";
  "group membership is not managed")

## Context
ADR 0010 left two gaps. The state of `terraform/` was a local file on one machine (not shared,
not locked, lost with the machine). Drift showed up only when someone happened to run
`terraform plan`. Lab 13 PR A ([`infra/azure/`](../../infra/azure/README.md)) created what is
needed to close both gaps: a storage account with shared keys disabled and a `tfstate`
container, a Key Vault with RBAC, and an Entra app `gh-energy-lakehouse` that GitHub Actions can
use through OIDC (no client secret). That app has data-plane roles only: *Storage Blob Data
Contributor* on `tfstate` and *Key Vault Secrets User* on the vault.

Databricks Free Edition has no account-level APIs, so GitHub cannot log in to Databricks through
OIDC federation (the same reason `daily-refresh.yml` uses an OAuth secret). Terraform needs a
Databricks identity that can read service principals, groups and every platform grant.

## Decision
- **Remote state:** `azurerm` backend, partial configuration. `terraform/backend.tf` holds only
  `backend "azurerm" {}`, and `terraform/backend.azure.hcl` (committed, identifiers only) holds
  `rg-energy-platform` / `stenergytf0cd70240` / `tfstate` /
  `energy-lakehouse/databricks.tfstate` and `use_azuread_auth = true`. Entra ID auth only:
  locally the Azure CLI login, in CI GitHub OIDC (`ARM_USE_OIDC` plus `ARM_CLIENT_ID`,
  `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID` from repository variables). With Entra ID auth the backend
  calls only the blob data plane, so the container role is enough. Locking uses blob leases.
  Blob versioning and 7-day soft delete (PR A) keep older state versions.
- **Terraform identity:** `sp-energy-terraform`, created by Terraform (no import block), member
  of the workspace `admins` group through `databricks_group_member` (the group is read with a
  data source and never managed). This is the only group membership Terraform manages. Its OAuth
  secret and client ID are stored only in Key Vault (`databricks-terraform-client-id`,
  `databricks-terraform-client-secret`), never in Terraform, its state or GitHub secrets.
- **Plan-only workflow** `.github/workflows/terraform-plan.yml`: on pull requests that touch
  `terraform/**` or `infra/azure/**`, weekly on Mondays at 05:41 UTC, and on demand. Permissions
  `contents: read`, `id-token: write` (the only workflow with `id-token: write`). Steps:
  `azure/login` (OIDC), read the two Key Vault secrets and mask them, `terraform init
  -backend-config=backend.azure.hcl`, `terraform plan -input=false -lock=false
  -detailed-exitcode`. On a PR the plan goes into the job summary and exit code 2 (changes)
  passes. On schedule or dispatch exit code 2 is **drift** and fails the job, so GitHub sends an
  e-mail.
- **CI never applies.** Apply stays with a person in the dev container, as in ADR 0010.
  `-lock=false` is safe because a plan never writes state, and the plan never holds a lease that
  could block a local apply. `tests/test_terraform_plan_workflow.py` enforces the rules (no
  apply, no `secrets.` in the workflow, credentials only from the Key Vault step, pinned actions).

## Alternatives considered
- **Terraform Cloud / HCP state:** one more account and token. The Azure foundation already
  exists for later labs.
- **Storage account keys or a SAS token for the backend:** a long-lived secret. Shared keys are
  disabled on purpose.
- **Reuse the CI/CD SP for Terraform:** it would then manage its own grants and its own
  identity, and anyone who can trigger a deploy could plan with admin rights. A separate
  identity keeps the deploy and platform blast radius apart.
- **Narrower UC privileges instead of `admins`:** creating and reading service principals and
  groups needs workspace admin. The UC grants alone would not cover the identities.
- **Apply on merge:** faster, but an admin credential with write intent in CI. The project is
  small, so the human apply step costs little.

## Consequences
- State is shared, locked and versioned. Losing a laptop no longer matters. The import blocks
  still re-adopt the platform if the state blob were lost (except the Terraform SP and its
  membership, which would have to be imported by hand).
- Drift becomes visible within a week without anyone running a plan.
- `sp-energy-terraform` is a workspace admin and its secret can read and change the whole
  workspace. Mitigations: the secret exists only in Key Vault, only the OIDC identity of this
  repository (pull requests and master) can read it, and the workflow only plans. Rotate it like
  the other SP secrets (runbook, "Secret rotation"), then update Key Vault instead of GitHub.
- `terraform/` now depends on Azure. On the Azure exit (credit ends around 2026-12-15, see
  `infra/azure/README.md`) the state must be moved back to a local file first (runbook,
  "Terraform state and drift").
- Pull requests from forks get no OIDC token, so the plan job fails there. That is acceptable
  for a single-owner repository. `workflow_dispatch` works only on `master`, because the
  federated credentials trust `pull_request` and `refs/heads/master` only.
