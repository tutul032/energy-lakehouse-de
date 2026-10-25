"""AI/BI dashboard guard: src/energy_market_de.lvdash.json is deployable to every target.

The dashboard is exported from dev (`bundle generate dashboard`) with SQL qualified as
workspace.energy_dev.<table>; scripts/build_dashboard.py rewrites that per target into
build/energy_market_de.<target>.lvdash.json. These tests catch what an export can bring along:
staging/prod schemas in the source, references the build would miss, datasets no widget uses,
and tables energy-consumers cannot read directly.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

from energy_lakehouse.contracts import load_contracts
from energy_lakehouse.dashboard import (
    SOURCE_SCHEMA,
    TARGET_SCHEMAS,
    build_dashboard,
    built_path,
    schema_prefix,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = REPO_ROOT / "src" / "energy_market_de.lvdash.json"
RESOURCE_PATH = REPO_ROOT / "resources" / "energy_market_de.dashboard.yml"
CONSUMER_GROUP = "energy-consumers"
# Read by the dashboard but without a contract, so apply_contracts never grants SELECT on it.
# Viewers still see it (embed_credentials); see resources/energy_market_de.dashboard.yml.
UNCONTRACTED_TABLES = {"gold_dq_daily_summary_text"}

TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+([`\w.]+)", re.IGNORECASE)
CTE_RE = re.compile(r"(?:\bWITH|,)\s*(\w+)\s+AS\s*\(", re.IGNORECASE)


def dataset_sql(dataset: dict) -> str:
    """Dataset query text (exports use `queryLines`, older ones `query`)."""
    return "".join(dataset.get("queryLines", [])) or dataset.get("query", "")


def used_dataset_names(dashboard: dict) -> set[str]:
    """Dataset names referenced by any widget query (charts, tables, filters) on any page."""
    return {
        query["query"]["datasetName"]
        for page in dashboard.get("pages", [])
        for item in page.get("layout", [])
        for query in item.get("widget", {}).get("queries", [])
        if "datasetName" in query.get("query", {})
    }


def referenced_tables(sql: str) -> set[str]:
    """Table names after FROM/JOIN (last name part, backticks removed), minus CTE names."""
    ctes = {name.lower() for name in CTE_RE.findall(sql)}
    tables = {ref.replace("`", "").split(".")[-1].lower() for ref in TABLE_REF_RE.findall(sql)}
    return tables - ctes


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))


def test_dashboard_parses(dashboard):
    assert dashboard["datasets"], "no datasets"
    assert dashboard["pages"], "no pages"
    names = [ds["name"] for ds in dashboard["datasets"]]
    assert len(names) == len(set(names)), "duplicate dataset names"


def all_sql(dashboard: dict) -> str:
    """SQL text of every dataset. The dataset-level "catalog" / "schema" fields (UI defaults in
    the export) are ignored: dataset_catalog / dataset_schema in the resource override them.
    """
    return "\n".join(dataset_sql(ds) for ds in dashboard["datasets"])


def schema_mentions(sql: str, schema: str) -> int:
    """How often ``schema`` appears as a whole word in ``sql``."""
    return len(re.findall(rf"\b{re.escape(schema)}\b", sql))


def test_source_sql_references_only_workspace_energy_dev(dashboard):
    sql = all_sql(dashboard)
    for schema in set(TARGET_SCHEMAS.values()) - {SOURCE_SCHEMA}:
        assert schema_mentions(sql, schema) == 0, f"source SQL must not name {schema}"
    # Every energy_dev reference must be the exact prefix the build replaces.
    assert schema_mentions(sql, SOURCE_SCHEMA) == sql.count(schema_prefix(SOURCE_SCHEMA)), (
        f"qualify tables as {schema_prefix(SOURCE_SCHEMA)}<table> (no backticks)"
    )


@pytest.mark.parametrize("target", sorted(TARGET_SCHEMAS))
def test_built_sql_references_only_its_target_schema(target):
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    sql = all_sql(json.loads(build_dashboard(source, target)))
    own = TARGET_SCHEMAS[target]
    for schema in set(TARGET_SCHEMAS.values()) - {own}:
        assert schema_mentions(sql, schema) == 0, f"{target} build names {schema}"
    source_refs = all_sql(json.loads(source)).count(schema_prefix(SOURCE_SCHEMA))
    assert sql.count(schema_prefix(own)) == source_refs


def test_all_sql_ignores_dataset_fields():
    ds = {"displayName": "d", "catalog": "workspace", "schema": "energy_dev"}
    dashboard = {"datasets": [{**ds, "queryLines": ["SELECT * FROM t"]}]}
    assert all_sql(dashboard) == "SELECT * FROM t"


def test_schema_mentions():
    sql = "FROM workspace.energy_dev.a JOIN `energy_dev`.b JOIN energy_dev_old.c"
    assert schema_mentions(sql, "energy_dev") == 2


def test_build_dashboard_replaces_prefix():
    source = '"FROM workspace.energy_dev.gold_energy_hourly", "schema": "energy_dev"'
    assert build_dashboard(source, "staging") == (
        '"FROM workspace.energy_staging.gold_energy_hourly", "schema": "energy_dev"'
    )
    assert build_dashboard(source, "dev") == source


def test_build_dashboard_unknown_target():
    with pytest.raises(ValueError, match="unknown target"):
        build_dashboard("{}", "qa")


def test_built_path():
    assert built_path("build", "prod") == Path("build") / "energy_market_de.prod.lvdash.json"


def test_target_schemas_match_databricks_yml():
    bundle = yaml.safe_load((REPO_ROOT / "databricks.yml").read_text(encoding="utf-8"))
    schemas = {name: t["variables"]["schema"] for name, t in bundle["targets"].items()}
    assert schemas == TARGET_SCHEMAS


def test_targets_deploy_their_built_dashboard():
    bundle = yaml.safe_load((REPO_ROOT / "databricks.yml").read_text(encoding="utf-8"))
    for name, target in bundle["targets"].items():
        dash = target["resources"]["dashboards"]["energy_market_de"]
        assert dash["file_path"] == built_path("build", name).as_posix()


def test_every_dataset_is_used_by_a_widget(dashboard):
    used = used_dataset_names(dashboard)
    unused = [ds["displayName"] for ds in dashboard["datasets"] if ds["name"] not in used]
    assert not unused, f"datasets not used by any widget: {unused}"


def test_dashboard_tables_are_contracted_for_consumers(dashboard):
    granted = {
        c.table for c in load_contracts() if any(x.group == CONSUMER_GROUP for x in c.consumers)
    }
    tables = set().union(*(referenced_tables(dataset_sql(ds)) for ds in dashboard["datasets"]))
    missing = tables - granted - UNCONTRACTED_TABLES
    assert not missing, f"no contract granting {CONSUMER_GROUP} SELECT: {sorted(missing)}"


def dashboard_resource() -> dict:
    resource = yaml.safe_load(RESOURCE_PATH.read_text(encoding="utf-8"))
    return resource["resources"]["dashboards"]["energy_market_de"]


def test_dashboard_resource_sets_dataset_catalog_and_schema():
    dash = dashboard_resource()
    assert dash.get("dataset_catalog") == "${var.catalog}", "set dataset_catalog: ${var.catalog}"
    assert dash.get("dataset_schema") == "${var.schema}", "set dataset_schema: ${var.schema}"


def test_dashboard_resource_is_per_target():
    dash = dashboard_resource()
    assert dash["warehouse_id"] == "${var.warehouse_id}"
    assert dash["embed_credentials"] is True
    assert {"group_name": CONSUMER_GROUP, "level": "CAN_READ"} in dash["permissions"]


def test_used_dataset_names():
    dashboard = {
        "pages": [
            {"layout": [{"widget": {"queries": [{"query": {"datasetName": "a"}}]}}]},
            {"layout": [{"widget": {"textbox_spec": "hi"}}]},
            {"name": "empty page"},
        ]
    }
    assert used_dataset_names(dashboard) == {"a"}


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM gold_energy_hourly", {"gold_energy_hourly"}),
        ("select * from workspace.energy_dev.t1 x join `t2` y on x.a = y.a", {"t1", "t2"}),
        ("WITH f AS (SELECT 1 FROM t1), g AS (SELECT 2) SELECT * FROM f JOIN g", {"t1"}),
    ],
)
def test_referenced_tables(sql, expected):
    assert referenced_tables(sql) == expected


def test_dataset_sql_query_lines_and_query():
    assert dataset_sql({"queryLines": ["SELECT 1\r\n", "FROM t"]}) == "SELECT 1\r\nFROM t"
    assert dataset_sql({"query": "SELECT 2"}) == "SELECT 2"
