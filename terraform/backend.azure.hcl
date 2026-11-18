# Partial backend configuration for backend.tf: terraform init -backend-config=backend.azure.hcl
# Identifiers only, no secrets (no access_key, sas_token or client secret). Created by infra/azure.
resource_group_name  = "rg-energy-platform"
storage_account_name = "stenergytf0cd70240"
container_name       = "tfstate"
key                  = "energy-lakehouse/databricks.tfstate"
use_azuread_auth     = true # shared keys are disabled on the storage account
