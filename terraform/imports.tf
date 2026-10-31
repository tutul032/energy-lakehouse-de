# Adopt the existing platform objects (created by hand, see docs/runbook.md) into state. ID formats
# from the provider docs (databricks/databricks 1.139.1, "Import"):
#   databricks_schema             <catalog>.<schema>
#   databricks_volume             <catalog>.<schema>.<volume>
#   databricks_service_principal  SCIM id of the service principal (workspace id, not application id)
#   databricks_group              SCIM id of the group
#   databricks_grant              <securable_type>/<securable_name>/<principal>
# The IDs are not secret.
#
# The blocks stay after the first apply on purpose: once a resource is in state, its import block
# is a no-op (plan shows nothing to import). Keeping them means a lost or fresh local state is
# re-adopted by the next plan instead of trying to create objects that already exist, and
# tests/test_terraform.py requires one per managed resource. Remove a block together with its
# resource, as for the refresh SP's former USE_SCHEMA grant on energy_dev.

import {
  to = databricks_service_principal.refresh
  id = "74312409388142"
}

import {
  to = databricks_service_principal.cicd
  id = "75104777449497"
}

import {
  to = databricks_group.consumers
  id = "85723784826267"
}

import {
  to = databricks_schema.raw
  id = "workspace.energy_raw"
}

import {
  to = databricks_schema.target["dev"]
  id = "workspace.energy_dev"
}

import {
  to = databricks_schema.target["staging"]
  id = "workspace.energy_staging"
}

import {
  to = databricks_schema.target["prod"]
  id = "workspace.energy_prod"
}

import {
  to = databricks_volume.landing
  id = "workspace.energy_raw.landing"
}

import {
  to = databricks_grant.catalog_use["refresh"]
  id = "catalog/workspace/e1370f9f-0819-48ba-92f0-365c18361518"
}

import {
  to = databricks_grant.catalog_use["cicd"]
  id = "catalog/workspace/7d0f1e32-4a86-42e2-b352-461dae1b3634"
}

import {
  to = databricks_grant.raw_schema_use["refresh"]
  id = "schema/workspace.energy_raw/e1370f9f-0819-48ba-92f0-365c18361518"
}

import {
  to = databricks_grant.raw_schema_use["cicd"]
  id = "schema/workspace.energy_raw/7d0f1e32-4a86-42e2-b352-461dae1b3634"
}

import {
  to = databricks_grant.target_schema_owner_read["staging"]
  id = "schema/workspace.energy_staging/rezaul.tutul@yahoo.com"
}

import {
  to = databricks_grant.target_schema_owner_read["prod"]
  id = "schema/workspace.energy_prod/rezaul.tutul@yahoo.com"
}

import {
  to = databricks_grant.landing_volume["refresh"]
  id = "volume/workspace.energy_raw.landing/e1370f9f-0819-48ba-92f0-365c18361518"
}

import {
  to = databricks_grant.landing_volume["cicd"]
  id = "volume/workspace.energy_raw.landing/7d0f1e32-4a86-42e2-b352-461dae1b3634"
}
