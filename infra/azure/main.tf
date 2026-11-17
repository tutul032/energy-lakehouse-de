data "azurerm_client_config" "current" {}

locals {
  tags = {
    project       = "energy-lakehouse-de"
    owner         = var.owner
    "cost-center" = "portfolio"
  }

  # Globally unique names without a random provider: a stable suffix from the subscription ID.
  suffix               = substr(sha1(var.subscription_id), 0, 8)
  storage_account_name = "stenergytf${local.suffix}" # 18 chars, [a-z0-9]
  key_vault_name       = "kv-energy-${local.suffix}" # 18 chars
}

resource "azurerm_resource_group" "platform" {
  name     = "rg-energy-platform"
  location = var.location
  tags     = local.tags
}
