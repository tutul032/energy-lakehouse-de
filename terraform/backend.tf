# Remote state (ADR 0011): azurerm backend in the tfstate container of infra/azure, partial
# configuration. The settings live in backend.azure.hcl (no secrets):
#   terraform init -backend-config=backend.azure.hcl
# Entra ID auth only (use_azuread_auth; the storage account has shared keys disabled): locally the
# Azure CLI login (az login), in terraform-plan.yml GitHub OIDC (ARM_USE_OIDC, ARM_CLIENT_ID,
# ARM_TENANT_ID, ARM_SUBSCRIPTION_ID). ci.yml keeps `init -backend=false` and never reads state.
terraform {
  backend "azurerm" {}
}
