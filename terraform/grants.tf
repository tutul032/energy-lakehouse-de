# Platform grants, one databricks_grant per securable and principal: authoritative only for that
# principal on that securable, so grants made elsewhere stay untouched. The refresh SP gets no
# grant on the target schemas: it only writes to the landing volume and triggers the prod job,
# which runs as the CI/CD SP. Not managed here (see
# docs/adr/0010-terraform-scope.md): the _workspace_users catalog grant, energy-consumers
# USE_SCHEMA on the target schemas and all table grants (apply_contracts), bundle resources.
locals {
  service_principals = {
    refresh = databricks_service_principal.refresh.application_id
    cicd    = databricks_service_principal.cicd.application_id
  }
}

resource "databricks_grant" "catalog_use" {
  for_each = local.service_principals

  catalog    = var.catalog
  principal  = each.value
  privileges = ["USE_CATALOG"]
}

resource "databricks_grant" "raw_schema_use" {
  for_each = local.service_principals

  schema     = databricks_schema.raw.id
  principal  = each.value
  privileges = ["USE_SCHEMA"]
}

# Read access for the platform owner to the schemas owned by the CI/CD SP.
resource "databricks_grant" "target_schema_owner_read" {
  for_each = toset(["staging", "prod"])

  schema     = databricks_schema.target[each.key].id
  principal  = var.platform_owner
  privileges = ["SELECT", "USE_SCHEMA"]
}

resource "databricks_grant" "landing_volume" {
  for_each = {
    refresh = ["READ_VOLUME", "WRITE_VOLUME"]
    cicd    = ["READ_VOLUME"]
  }

  volume     = databricks_volume.landing.id
  principal  = local.service_principals[each.key]
  privileges = each.value
}
