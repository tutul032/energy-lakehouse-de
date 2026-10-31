# Schemas: the shared raw zone plus one schema per bundle target. The bundle never creates them;
# staging/prod are owned by the CI/CD SP, which deploys and runs those targets.
locals {
  # Set by Unity Catalog on every schema of this workspace; declared so the plan has no diff.
  schema_properties = { owner = "root" }

  target_schemas = {
    dev     = { name = "energy_dev", owner = var.platform_owner }
    staging = { name = "energy_staging", owner = databricks_service_principal.cicd.application_id }
    prod    = { name = "energy_prod", owner = databricks_service_principal.cicd.application_id }
  }
}

resource "databricks_schema" "raw" {
  catalog_name = var.catalog
  name         = var.raw_schema
  owner        = var.platform_owner
  comment      = "Raw landing zone shared by all environments"
  properties   = local.schema_properties

  lifecycle {
    prevent_destroy = true
  }
}

resource "databricks_schema" "target" {
  for_each = local.target_schemas

  catalog_name = var.catalog
  name         = each.value.name
  owner        = each.value.owner
  properties   = local.schema_properties

  lifecycle {
    prevent_destroy = true
  }
}
