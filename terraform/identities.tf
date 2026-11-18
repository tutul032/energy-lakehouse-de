# Workspace identities. OAuth secrets are never managed here (see docs/runbook.md "Secret
# rotation"). The only group membership managed here is sp-energy-terraform in admins (ADR 0011).
resource "databricks_service_principal" "refresh" {
  display_name               = "sp-energy-refresh"
  workspace_access           = true
  databricks_sql_access      = true
  allow_cluster_create       = false
  allow_instance_pool_create = false

  lifecycle {
    prevent_destroy = true
    # The workspace also grants the workspace-consume entitlement, which the provider refuses
    # together with workspace_access; keep it as it is instead of removing it.
    ignore_changes = [workspace_consume]
  }
}

resource "databricks_service_principal" "cicd" {
  display_name               = "sp-energy-cicd"
  workspace_access           = true
  databricks_sql_access      = true
  allow_cluster_create       = false
  allow_instance_pool_create = false

  lifecycle {
    prevent_destroy = true
    # The workspace also grants the workspace-consume entitlement, which the provider refuses
    # together with workspace_access; keep it as it is instead of removing it.
    ignore_changes = [workspace_consume]
  }
}

resource "databricks_group" "consumers" {
  display_name               = var.consumers_group
  workspace_access           = true
  databricks_sql_access      = true
  allow_cluster_create       = false
  allow_instance_pool_create = false

  lifecycle {
    prevent_destroy = true
    # The workspace also grants the workspace-consume entitlement, which the provider refuses
    # together with workspace_access; keep it as it is instead of removing it.
    ignore_changes = [workspace_consume]
  }
}

# Identity of terraform-plan.yml (ADR 0011). Created by Terraform, so no import block. Its OAuth
# secret is created by hand with the Databricks CLI and stored only in Key Vault
# (databricks-terraform-client-id / databricks-terraform-client-secret), never in Terraform.
resource "databricks_service_principal" "terraform" {
  display_name               = "sp-energy-terraform"
  workspace_access           = true
  databricks_sql_access      = true
  allow_cluster_create       = false
  allow_instance_pool_create = false

  lifecycle {
    prevent_destroy = true
    # Same as the other SPs: the workspace may add workspace-consume, which the provider refuses
    # together with workspace_access; keep it as it is instead of removing it.
    ignore_changes = [workspace_consume]
  }
}

# The built-in workspace admins group: read only, never managed.
data "databricks_group" "admins" {
  display_name = "admins"
}

# Workspace admin: reading and managing service principals and groups needs it (ADR 0011).
resource "databricks_group_member" "terraform_admin" {
  group_id  = data.databricks_group.admins.id
  member_id = databricks_service_principal.terraform.id

  lifecycle {
    prevent_destroy = true
  }
}
