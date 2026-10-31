variable "databricks_host" {
  description = "Workspace URL (same as workspace.host in databricks.yml)."
  type        = string
}

variable "catalog" {
  description = "Unity Catalog catalog that holds all schemas (Free Edition: workspace)."
  type        = string
  default     = "workspace"
}

variable "raw_schema" {
  description = "Schema of the shared raw landing zone (all targets read from it)."
  type        = string
  default     = "energy_raw"
}

variable "landing_volume" {
  description = "Managed volume with the raw SMARD and Open-Meteo files."
  type        = string
  default     = "landing"
}

variable "platform_owner" {
  description = "User who owns energy_raw, energy_dev and the landing volume (alert_email in databricks.yml)."
  type        = string
  default     = "rezaul.tutul@yahoo.com"
}

variable "consumers_group" {
  description = "Consumer group of the data contracts; its schema/table grants come from apply_contracts."
  type        = string
  default     = "energy-consumers"
}
