# GitHub Actions -> Azure via OIDC (workload identity federation): no client secret exists.
# GitHub sends the immutable subject format repo:<owner>@<owner_id>/<repo>@<repo_id>:...
# (AADSTS700213 if the credential still uses repo:<owner>/<repo>:...).
locals {
  github_subjects = {
    pull-request  = "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:pull_request"
    branch-master = "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:ref:refs/heads/${var.github_branch}"
  }
}

resource "azuread_application" "github" {
  display_name = "gh-energy-lakehouse"
  owners       = [data.azurerm_client_config.current.object_id]
}

resource "azuread_service_principal" "github" {
  client_id = azuread_application.github.client_id
  owners    = [data.azurerm_client_config.current.object_id]
}

resource "azuread_application_federated_identity_credential" "github" {
  for_each = local.github_subjects

  application_id = azuread_application.github.id
  display_name   = "github-${each.key}"
  description    = "GitHub Actions OIDC: ${each.value}"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = each.value
}
