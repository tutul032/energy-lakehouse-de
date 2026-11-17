# Azure foundation (`infra/azure/`, Lab 13)

Bootstrap Terraform root for the Azure part of this project. It creates what every later Azure
root needs, and nothing else:

| Resource | Name | Notes |
|---|---|---|
| Resource group | `rg-energy-platform` | `swedencentral`; tags `project=energy-lakehouse-de`, `owner`, `cost-center=portfolio` |
| Storage account | `stenergytf<8 hex>` | Terraform state for later roots. Standard_LRS, StorageV2, TLS 1.2, HTTPS only, no public blob access, **shared keys off** (Entra ID only), blob versioning + 7-day soft delete |
| Blob container | `tfstate` | private |
| Key Vault | `kv-energy-<8 hex>` | RBAC authorization, soft delete 7 days, purge protection **off** (easy teardown). Terraform never writes secrets |
| Entra app + service principal | `gh-energy-lakehouse` | No client secret. Federated credentials for GitHub OIDC: `repo:tutul032/energy-lakehouse-de:pull_request` and `…:ref:refs/heads/master` |
| Role assignments | | SP: *Storage Blob Data Contributor* on the `tfstate` container, *Key Vault Secrets User* on the vault. You (the operator): *Storage Blob Data Contributor* on the container, *Key Vault Secrets Officer* on the vault |
| Budget | `budget-energy-lakehouse-monthly` | Subscription, 10 USD/month, e-mail at 50 / 80 / 100 % (actual cost). Alerts only, it never stops spending |

`<8 hex>` = the first 8 characters of `sha1(subscription_id)`. The suffix is stable and
globally unique enough, and needs no `random` provider.

**State.** This root creates the state storage, so its own state is **local**
(`infra/azure/terraform.tfstate`, gitignored, like every `*.tfstate`). That holds only for this
bootstrap root. Back the file up (see [Back up the local state](#back-up-the-local-state)).
Every later Azure root uses the `azurerm` backend in the `tfstate` container.

**Auth.** Locally: Azure CLI (`az login --use-device-code`) in the dev container. No client
secrets, no credentials in the repo or in Terraform state. GitHub Actions: OIDC with the outputs
`client_id` / `tenant_id` / `subscription_id`.

Versions: Terraform `~> 1.16.6` (same as `terraform/`, CI and the dev container), `azurerm`
5.12.0, `azuread` 3.10.0, locked in `.terraform.lock.hcl` (hashes for linux/windows/darwin).
CI (`ci.yml`, job `terraform`, matrix root `infra/azure`) runs `fmt -check`,
`init -backend=false`, `validate` and `tflint` without credentials. It never runs plan or apply.

## Prerequisites

- Dev container with the `azure-cli` feature (`.devcontainer/README.md`, "Log in to Azure").
- Your account owns the subscription (Owner): role assignments need
  `Microsoft.Authorization/roleAssignments/write`.
- Your account may create app registrations in the tenant (default in your own tenant).

## Bootstrap

All commands run in the dev container (bash), from the repo root.

```bash
az login --use-device-code
az account set --subscription <subscription-id>
az account show --query "{subscription:id, tenant:tenantId, user:user.name}" -o table

# Providers the resources need (once per subscription; a no-op if already registered)
for ns in Microsoft.Storage Microsoft.KeyVault Microsoft.Consumption Microsoft.CostManagement; do
  az provider register --namespace "$ns" --wait
done

cd infra/azure
cp terraform.tfvars.example terraform.tfvars   # then fill in subscription_id, owner, alert_email
terraform init -lockfile=readonly
terraform fmt -check && terraform validate
terraform plan -out bootstrap.tfplan          # review: 13 to add, 0 to change, 0 to destroy
terraform apply bootstrap.tfplan
terraform output
```

Expected output names: `client_id`, `tenant_id`, `subscription_id`, `resource_group_name`,
`storage_account_name`, `container_name` (`tfstate`), `key_vault_name`, `key_vault_uri`. None of
them is a secret.

### Check

```bash
SA=$(terraform output -raw storage_account_name)
az storage account show -n "$SA" \
  --query "{tls:minimumTlsVersion, publicBlob:allowBlobPublicAccess, sharedKey:allowSharedKeyAccess}" -o table
az storage account blob-service-properties show -n "$SA" -g rg-energy-platform \
  --query "{versioning:isVersioningEnabled}" -o table
az keyvault show -n "$(terraform output -raw key_vault_name)" \
  --query "{rbac:properties.enableRbacAuthorization, purgeProtection:properties.enablePurgeProtection}" -o table
az ad app federated-credential list --id "$(terraform output -raw client_id)" --query "[].subject" -o tsv
az consumption budget list --query "[].{name:name, amount:amount}" -o table
```

The role assignments for you can take a few minutes to propagate. Until then,
`--auth-mode login` blob calls answer `AuthorizationPermissionMismatch`; wait and retry.

### Run a second plan

A second `terraform plan` must show **no changes**. If it does not, find out why before you go on.

### Back up the local state

The local `terraform.tfstate` is the only record of this root. Copy it out of the repo folder
(e.g. into the `tfstate` container itself; versioning keeps every copy):

```bash
az storage blob upload --auth-mode login --account-name "$SA" -c tfstate \
  -n bootstrap/infra-azure.tfstate -f terraform.tfstate --overwrite
```

To restore it, download that blob to `infra/azure/terraform.tfstate`. You can also import the
resources again (`terraform import`): every name is deterministic.

## GitHub Actions (OIDC)

Set repository **variables** (not secrets; none of these values is a secret):
`AZURE_CLIENT_ID` = `client_id`, `AZURE_TENANT_ID` = `tenant_id`,
`AZURE_SUBSCRIPTION_ID` = `subscription_id`. A workflow then needs `permissions: id-token: write`
and `azure/login` with these three inputs (no `creds`, no client secret).

Subjects that are trusted: pull requests (`repo:tutul032/energy-lakehouse-de:pull_request`) and
pushes to master (`…:ref:refs/heads/master`). A job with `environment:` sends the subject
`repo:…:environment:<name>` instead. That subject needs its own federated credential (add it to
`local.github_subjects` in `identity.tf`).

The SP has **no subscription role**. It can read and write state blobs in `tfstate` and read
secrets from the vault, nothing else. Later labs grant more on their own resource groups.

## Using the state container from a later root

```hcl
terraform {
  backend "azurerm" {
    resource_group_name  = "rg-energy-platform"
    storage_account_name = "<storage_account_name output>"
    container_name       = "tfstate"
    key                  = "<root-name>.tfstate"
    use_azuread_auth     = true # shared keys are disabled
  }
}
```

In GitHub Actions also set `use_oidc = true` (or `ARM_USE_OIDC=true`) and `ARM_CLIENT_ID` /
`ARM_TENANT_ID` / `ARM_SUBSCRIPTION_ID` from the variables above.

## Secrets

Secrets go into the vault with the CLI, never through Terraform (they would land in the state):

```bash
az keyvault secret set --vault-name "$(terraform output -raw key_vault_name)" -n <name> --file <path>
```

`--file` keeps the value out of the shell history.

## Teardown

Order: first every root that uses the `tfstate` container (`terraform destroy` there), then this
root. The resource group refuses to delete while it still holds resources this root does not
manage (`prevent_deletion_if_contains_resources`). That is a guard, not an error to work around.

```bash
cd infra/azure
terraform plan -destroy -out destroy.tfplan   # review: 13 to destroy
terraform apply destroy.tfplan
```

- The Key Vault is purged on destroy (`purge_soft_delete_on_destroy`; purge protection is off),
  so its name is free again at once. If a purge fails, run
  `az keyvault purge -n <name> -l swedencentral`.
- The storage account is deleted with all state blobs and versions. Download what you want to
  keep first.
- The app registration goes to the Entra recycle bin for 30 days. To remove it for good, use
  the portal (*App registrations → Deleted applications → Delete permanently*), or note the app's
  object ID before the destroy (`az ad app list --display-name gh-energy-lakehouse --query
  "[].id" -o tsv`) and run
  `az rest --method DELETE --url https://graph.microsoft.com/v1.0/directory/deletedItems/<object-id>`.
- Then check that nothing is left: `az group list -o table`,
  `az keyvault list-deleted -o table`, `az ad app list --display-name gh-energy-lakehouse`.

## Exit plan (free credit ends around 2026-12-15)

The free account's credit ends around **2026-12-15** (check *Cost Management → Credits* in the
portal). After that, the subscription is disabled unless you upgrade it to pay-as-you-go. Disabled
means resources become unreachable, and data is deleted after the retention period.

| When | Action |
|---|---|
| Weekly until then | Check the budget alerts and *Cost analysis* for `rg-energy-platform` and the later roots. This foundation costs cents per month (LRS storage, vault operations); the budget is meant for later labs. |
| **By 2026-12-08** (one week of buffer) | Decide: **tear down** or **upgrade**. |
| Tear down | Save what the portfolio needs (screenshots for `docs/img/`, `terraform output`, plan outputs, state copies outside Azure), then follow [Teardown](#teardown) for every root, this one last. Remove the repo variables `AZURE_*` and disable the workflows that log in to Azure. |
| Upgrade | Upgrade to pay-as-you-go *before* the credit ends. Keep the budget, and lower `budget_amount` if needed (`terraform apply`). Stop or delete compute-heavy resources of later labs. A budget only alerts; it never caps costs. |
| After the end date | If nothing was decided: the subscription is disabled. Reactivate it (upgrade) to delete resources cleanly, or open a support request to cancel. Delete the app registration in Entra (it is not billed, but it keeps a trusted OIDC entry). |

The Databricks parts of the project (Free Edition, `terraform/`, the bundle) do not depend on
Azure. They keep working after the exit.
