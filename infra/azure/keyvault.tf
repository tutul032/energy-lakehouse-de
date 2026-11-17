# Secrets are added later with `az keyvault secret set`, never through Terraform (never in state).
resource "azurerm_key_vault" "platform" {
  name                = local.key_vault_name
  resource_group_name = azurerm_resource_group.platform.name
  location            = azurerm_resource_group.platform.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  # Off for easy teardown (purge right after destroy). Turn it on for anything that must survive.
  purge_protection_enabled = false

  tags = local.tags
}
