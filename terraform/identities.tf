# Workspace identities. OAuth secrets are never managed here (see docs/runbook.md "Secret
# rotation"); group membership is not managed either.
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
