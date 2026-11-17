# Authentication comes from the environment only: `az login --use-device-code` in the dev
# container (Azure CLI auth). No client secrets, no credentials in the repo.
provider "azurerm" {
  subscription_id = var.subscription_id

  # Shared keys are disabled on the state account, so data-plane calls use Entra ID.
  storage_use_azuread = true

  features {
    key_vault {
      # Purge protection is off (easy teardown), so `terraform destroy` purges the vault and
      # frees its name at once instead of keeping it soft-deleted for 7 days.
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = false
    }
    resource_group {
      # Refuse to delete the resource group while it still holds resources this root does not
      # manage (e.g. created by a later root or in the portal).
      prevent_deletion_if_contains_resources = true
    }
  }
}

# Tenant from the Azure CLI login (own tenant of the free account).
provider "azuread" {}
