# Shared landing volume: daily-refresh.yml writes here (refresh SP), all pipelines read.
resource "databricks_volume" "landing" {
  catalog_name = var.catalog
  schema_name  = databricks_schema.raw.name
  name         = var.landing_volume
  volume_type  = "MANAGED"
  owner        = var.platform_owner

  lifecycle {
    prevent_destroy = true
  }
}
