variable "subscription_id" {
  description = "Azure subscription ID (free account). Not a secret, but kept out of git in terraform.tfvars."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.subscription_id))
    error_message = "The subscription_id must be a lowercase GUID."
  }
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "swedencentral"
}

variable "owner" {
  description = "Value of the owner tag (GitHub handle or name)."
  type        = string
}

variable "alert_email" {
  description = "E-mail address for the budget alerts. Kept out of git in terraform.tfvars."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$", var.alert_email))
    error_message = "The alert_email must be an e-mail address."
  }
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose workflows may log in via OIDC."
  type        = string
  default     = "tutul032/energy-lakehouse-de"
}

variable "github_branch" {
  description = "Branch whose push workflows may log in via OIDC."
  type        = string
  default     = "master"
}

variable "budget_amount" {
  description = "Monthly subscription budget in the billing currency (USD for this account)."
  type        = number
  default     = 10
}

variable "budget_start_date" {
  description = "Budget start: first day of a month (RFC 3339), not before the current month at creation."
  type        = string
  default     = "2026-11-01T00:00:00Z"

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-01T00:00:00Z$", var.budget_start_date))
    error_message = "The budget_start_date must be the first day of a month, e.g. 2026-11-01T00:00:00Z."
  }
}
