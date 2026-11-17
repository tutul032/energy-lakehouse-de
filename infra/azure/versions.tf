# Azure bootstrap root (Lab 13). Same Terraform version as terraform/, CI and the dev container;
# .terraform.lock.hcl pins the provider hashes.
#
# No backend block on purpose: this root creates the state storage account, so its own state is
# local (infra/azure/terraform.tfstate, gitignored). Every later Azure root uses the azurerm
# backend in the "tfstate" container created here (see README.md).
terraform {
  required_version = "~> 1.16.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "5.12.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "3.10.0"
    }
  }
}
