# Identifiers only, no secrets. Used for the GitHub variables AZURE_CLIENT_ID, AZURE_TENANT_ID,
# AZURE_SUBSCRIPTION_ID and for the backend config of later roots.

output "client_id" {
  description = "Client (application) ID of gh-energy-lakehouse (GitHub variable AZURE_CLIENT_ID)."
  value       = azuread_application.github.client_id
}

output "tenant_id" {
  description = "Entra tenant ID (GitHub variable AZURE_TENANT_ID)."
  value       = data.azurerm_client_config.current.tenant_id
}

output "subscription_id" {
  description = "Subscription ID (GitHub variable AZURE_SUBSCRIPTION_ID)."
  value       = var.subscription_id
}

output "resource_group_name" {
  description = "Resource group of the platform resources."
  value       = azurerm_resource_group.platform.name
}

output "storage_account_name" {
  description = "Storage account that holds Terraform state."
  value       = azurerm_storage_account.tfstate.name
}

output "container_name" {
  description = "Blob container for Terraform state."
  value       = azurerm_storage_container.tfstate.name
}

output "key_vault_name" {
  description = "Key Vault name (az keyvault secret set --vault-name ...)."
  value       = azurerm_key_vault.platform.name
}

output "key_vault_uri" {
  description = "Key Vault URI."
  value       = azurerm_key_vault.platform.vault_uri
}
