# Least privilege: data-plane roles on single resources, no subscription-wide role for the SP.

resource "azurerm_role_assignment" "github_state_blob" {
  scope                = azurerm_storage_container.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azuread_service_principal.github.object_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "github_kv_secrets_user" {
  scope                = azurerm_key_vault.platform.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azuread_service_principal.github.object_id
  principal_type       = "ServicePrincipal"
}

# The operator (whoever runs this root after `az login`) needs data-plane roles as well: shared
# keys are off and the vault uses RBAC, so subscription Owner alone can neither read state blobs
# nor set secrets.
resource "azurerm_role_assignment" "operator_state_blob" {
  scope                = azurerm_storage_container.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "operator_kv_secrets_officer" {
  scope                = azurerm_key_vault.platform.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}
