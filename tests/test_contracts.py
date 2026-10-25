"""Offline tests for energy_lakehouse.contracts and contracts/*.yaml (no Spark).

The column test is the breaking-change guard: a pipeline change that adds, removes, renames or
retypes a contracted column fails here until the contract (and its version) is updated too.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from energy_lakehouse import contracts, transforms
from energy_lakehouse.contracts import DataContract, QualityRule

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs" / "data-products"
CONTRACTS = contracts.load_contracts()
BY_TABLE = {c.table: c for c in CONTRACTS}
HOURLY = BY_TABLE["gold_energy_hourly"]
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

ids = [c.table for c in CONTRACTS]
FORECAST_TABLES = ("gold_forecast_hourly", "gold_forecast_accuracy")
DQ_TABLES = ("gold_dq_anomalies", "gold_dq_daily_summary")
# Plain Delta tables written by notebooks after verify_contracts; their contracts start as draft.
NOTEBOOK_TABLES = (*FORECAST_TABLES, *DQ_TABLES)


def _minimal(**overrides) -> dict:
    data = {
        "apiVersion": "v1",
        "kind": "DataContract",
        "id": "energy.t",
        "version": "1.0.0",
        "status": "active",
        "owner": {"name": "Owner", "email": "owner@example.com"},
        "description": "Test table.",
        "table": "t",
        "grain": "one row per k",
        "primary_key": ["k"],
        "sla": {"freshness_hours": 24, "freshness_column": "k", "schedule": "daily"},
        "columns": [{"name": "k", "type": "timestamp", "description": "Key."}],
    }
    return {**data, **overrides}


# --- contracts/*.yaml ------------------------------------------------------------------------


def test_one_contract_per_contracted_table():
    assert sorted(BY_TABLE) == sorted(transforms.CONTRACT_TABLE_COLUMNS)
    assert set(transforms.GOLD_TABLE_COLUMNS) <= set(BY_TABLE)


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids)
def test_version_is_semver(contract):
    assert re.fullmatch(r"\d+\.\d+\.\d+", contract.version)


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids)
def test_primary_key_subset_of_columns(contract):
    assert set(contract.primary_key) <= set(contract.column_names)


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids)
def test_columns_match_transforms(contract):
    """Breaking-change guard: contract columns == the pipeline's column constant."""
    expected = transforms.CONTRACT_TABLE_COLUMNS[contract.table]
    assert sorted(contract.column_names) == sorted(name for name, _, _ in expected)
    assert {c.name: c.type for c in contract.columns} == {n: t for n, t, _ in expected}


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids)
def test_descriptions_match_pipeline_comments(contract):
    """Pipeline and apply_contracts set the same comments, so neither keeps overwriting them."""
    expected = transforms.CONTRACT_TABLE_COLUMNS[contract.table]
    assert {c.name: c.description for c in contract.columns} == {n: d for n, _, d in expected}
    assert contract.description == transforms.CONTRACT_TABLE_COMMENTS[contract.table]


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids)
def test_contract_metadata(contract):
    assert contract.id == f"energy.{contract.table}"
    # Pipeline tables are materialized views; forecast and DQ tables are written by notebooks.
    expected = "table" if contract.table in NOTEBOOK_TABLES else "materialized_view"
    assert contract.object_type == expected
    assert contract.consumers and contract.quality
    assert contract.tags["layer"] == contract.table.split("_")[0]  # gold / silver


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids)
def test_primary_key_is_unique_and_required(contract):
    rules = contracts.quality_rules(contract)
    assert any(r.type == "unique" and r.columns == contract.primary_key for r in rules)
    required = next(r for r in rules if r.name == contracts.NOT_NULL_RULE)
    assert set(contract.primary_key) <= set(required.columns)


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids)
def test_docs_page_up_to_date(contract):
    path = DOCS_DIR / f"{contract.table}.md"
    assert path.exists(), "run: uv run python scripts/render_contract_docs.py"
    assert path.read_text(encoding="utf-8") == contracts.render_markdown(contract), (
        "docs out of date, run: uv run python scripts/render_contract_docs.py"
    )


# --- models / loader -------------------------------------------------------------------------


def test_minimal_contract_parses_and_normalizes_type():
    contract = DataContract.model_validate(_minimal())
    assert contract.columns[0].type == "TIMESTAMP"
    assert contract.object_keyword == "TABLE"


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": "1.0"},
        {"version": "v1.0.0"},
        {"primary_key": ["missing"]},
        {"sla": {"freshness_hours": 0, "freshness_column": "k", "schedule": "daily"}},
        {"sla": {"freshness_hours": 1, "freshness_column": "nope", "schedule": "daily"}},
        {"tags": {"bad-key": "x"}},
        {"tags": {"contract_version": "x"}},
        {"owner": {"name": "O", "email": "not-an-email"}},
        {"unexpected": True},
        {"columns": [{"name": "k", "type": "INT", "description": "a"}] * 2},
        {"quality": [{"name": "r", "type": "unique", "columns": ["nope"]}]},
        {"quality": [{"name": "r", "type": "row_condition", "columns": ["k"]}]},
        {"quality": [{"name": "r", "type": "unique", "columns": ["k"], "max_violation_pct": 101}]},
        {"quality": [{"name": contracts.NOT_NULL_RULE, "type": "not_null", "columns": ["k"]}]},
    ],
)
def test_invalid_contracts_rejected(overrides):
    with pytest.raises(ValidationError):
        DataContract.model_validate(_minimal(**overrides))


def test_load_contract_requires_matching_file_name(tmp_path):
    path = tmp_path / "other.yaml"
    path.write_text(
        (contracts.CONTRACTS_DIR / "gold_energy_hourly.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="file name"):
        contracts.load_contract(path)


def test_load_contract_rejects_non_mapping(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        contracts.load_contract(path)


def test_load_contracts_fails_on_empty_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        contracts.load_contracts(tmp_path)


@pytest.mark.parametrize(
    ("status", "enforced"),
    [("active", True), ("deprecated", True), ("draft", False), ("retired", False)],
)
def test_is_enforced_by_status(status, enforced):
    contract = DataContract.model_validate(_minimal(status=status))
    assert contracts.is_enforced(contract) is enforced


def test_pipeline_contracts_enforced_notebook_contracts_draft():
    # Forecast/DQ tables appear only after their first notebook run; contracts start as draft.
    for contract in CONTRACTS:
        assert contracts.is_enforced(contract) is (contract.table not in NOTEBOOK_TABLES)


# --- rendered SQL snapshots (gold_energy_hourly) ---------------------------------------------

FQN = "`workspace`.`energy_dev`.`gold_energy_hourly`"


def test_comment_on_table_sql_snapshot():
    assert contracts.comment_on_table_sql(HOURLY, "workspace", "energy_dev") == (
        f"COMMENT ON TABLE {FQN} IS 'Hourly German power load, day-ahead price and generation "
        "mix (SMARD, CC BY 4.0). One row per UTC hour.'"
    )


def test_column_comment_sql_snapshot():
    column = HOURLY.columns[0]
    assert contracts.column_comment_sql(HOURLY, column, "workspace", "energy_dev") == (
        f"ALTER MATERIALIZED VIEW {FQN} ALTER COLUMN `hour_utc` COMMENT 'Start of the hour (UTC).'"
    )


def test_set_tags_sql_snapshot():
    assert contracts.set_tags_sql(HOURLY, "workspace", "energy_dev") == (
        f"ALTER MATERIALIZED VIEW {FQN} SET TAGS ("
        "'contract_id' = 'energy.gold_energy_hourly', 'contract_version' = '1.0.0', "
        "'data_product' = 'energy_market_hourly', 'domain' = 'energy', 'layer' = 'gold', "
        "'license' = 'CC-BY-4.0', 'source' = 'smard')"
    )


def test_grant_sqls_snapshot():
    assert contracts.grant_sqls(HOURLY, "workspace", "energy_dev") == [
        "GRANT USE SCHEMA ON SCHEMA `workspace`.`energy_dev` TO `energy-consumers`",
        f"GRANT SELECT ON TABLE {FQN} TO `energy-consumers`",
    ]


def test_quality_sql_snapshots():
    rules = {r.name: r for r in contracts.quality_rules(HOURLY)}
    sql = {
        name: contracts.quality_sql(HOURLY, r, "workspace", "energy_dev")
        for name, r in rules.items()
    }
    assert sql["unique_hour"] == (
        f"SELECT (SELECT count(*) FROM {FQN}) AS total_rows, coalesce(sum(n), 0) AS violations "
        f"FROM (SELECT count(*) AS n FROM {FQN} GROUP BY `hour_utc` HAVING count(*) > 1) AS dup"
    )
    assert sql[contracts.NOT_NULL_RULE] == (
        "SELECT count(*) AS total_rows, count_if(`hour_utc` IS NULL OR `hour_local` IS NULL) "
        f"AS violations FROM {FQN}"
    )
    assert sql["share_in_range"] == (
        "SELECT count(*) AS total_rows, count_if(NOT coalesce((renewables_share IS NULL OR "
        f"renewables_share BETWEEN 0 AND 1.5), FALSE)) AS violations FROM {FQN}"
    )


def test_freshness_sql_snapshot():
    assert contracts.freshness_sql(HOURLY, "workspace", "energy_dev") == (
        f"SELECT CAST(max(`hour_utc`) AS TIMESTAMP) AS max_ts FROM {FQN}"
    )


def test_information_schema_queries_snapshot():
    assert contracts.columns_query("workspace", "energy_dev", "gold_energy_hourly") == (
        "SELECT column_name, full_data_type, comment "
        "FROM `workspace`.`information_schema`.`columns` "
        "WHERE table_schema = 'energy_dev' AND table_name = 'gold_energy_hourly' "
        "ORDER BY ordinal_position"
    )
    assert contracts.table_comment_query("workspace", "energy_dev", "gold_energy_hourly") == (
        "SELECT comment FROM `workspace`.`information_schema`.`tables` "
        "WHERE table_schema = 'energy_dev' AND table_name = 'gold_energy_hourly'"
    )
    assert contracts.table_tags_query("workspace", "energy_dev", "gold_energy_hourly") == (
        "SELECT tag_name, tag_value FROM `workspace`.`information_schema`.`table_tags` "
        "WHERE schema_name = 'energy_dev' AND table_name = 'gold_energy_hourly'"
    )


# --- SQL rendering details -------------------------------------------------------------------


def test_sql_string_escapes_quotes_and_backslashes():
    assert contracts.sql_string("it's a \\ test") == "'it\\'s a \\\\ test'"


def test_quality_sql_where_substitutes_table():
    daily = BY_TABLE["gold_energy_daily"]
    rule = next(r for r in daily.quality if r.name == "complete_days")
    sql = contracts.quality_sql(daily, rule, "workspace", "energy_prod")
    fqn = "`workspace`.`energy_prod`.`gold_energy_daily`"
    assert sql == (
        "SELECT count(*) AS total_rows, count_if(NOT coalesce((hour_count BETWEEN 23 AND 25), "
        f"FALSE)) AS violations FROM {fqn} WHERE (date_local < "
        f"(SELECT date_sub(max(date_local), 1) FROM {fqn}))"
    )


@pytest.fixture
def load_check_rule():
    daily = BY_TABLE["gold_energy_daily"]
    return next(r for r in daily.quality if r.name == "complete_day_load_matches_hourly")


def test_sql_check_sql_substitutes_catalog_and_schema(load_check_rule):
    assert load_check_rule.type == "sql_check"
    sql = contracts.sql_check_sql(load_check_rule, "workspace", "energy_prod")
    assert "{catalog}" not in sql and "{schema}" not in sql
    assert sql.startswith("SELECT count(*) AS violations\n")  # run as-is, not wrapped
    assert "FROM `workspace`.`energy_prod`.gold_energy_daily d" in sql
    assert (
        "FROM `workspace`.`energy_prod`.gold_energy_hourly GROUP BY 1) h USING (date_local)" in sql
    )
    assert sql.endswith("> 0.001 * h.hourly_sum")


def test_sql_check_result_fails_above_max_violations(load_check_rule):
    daily = BY_TABLE["gold_energy_daily"]
    failed = contracts.sql_check_result(daily, load_check_rule, 3)
    assert not failed.passed
    assert failed.detail == "3 violations; limit 0"
    assert contracts.sql_check_result(daily, load_check_rule, 0).passed
    lenient = load_check_rule.model_copy(update={"max_violations": 5})
    assert contracts.sql_check_result(daily, lenient, 5).passed


def test_sql_check_is_not_wrapped_by_quality_sql(load_check_rule):
    daily = BY_TABLE["gold_energy_daily"]
    with pytest.raises(ValueError, match="run as-is"):
        contracts.quality_sql(daily, load_check_rule, "workspace", "energy_prod")
    row_rule = next(r for r in daily.quality if r.name == "complete_days")
    with pytest.raises(ValueError, match="not a sql_check"):
        contracts.sql_check_sql(row_rule, "workspace", "energy_prod")


@pytest.mark.parametrize(
    "extra",
    [{}, {"sql": "SELECT 0 AS violations", "where": "x"}, {"sql": "SELECT 1", "columns": ["a"]}],
)
def test_sql_check_shape_rejected(extra):
    with pytest.raises(ValueError, match="sql_check needs"):
        contracts.QualityRule(name="r", type="sql_check", **extra)


def test_quality_sql_latest_issue_complete():
    contract = BY_TABLE["gold_forecast_hourly"]
    rule = next(r for r in contract.quality if r.name == "latest_issue_complete")
    sql = contracts.quality_sql(contract, rule, "workspace", "energy_prod")
    fqn = "`workspace`.`energy_prod`.`gold_forecast_hourly`"
    latest = f"(SELECT max(issue_date) FROM {fqn})"
    assert sql == (
        "SELECT count(*) AS total_rows, count_if(NOT coalesce((target IN (SELECT target FROM "
        f"{fqn} WHERE issue_date = {latest} GROUP BY target HAVING count(*) BETWEEN 23 AND 25)), "
        f"FALSE)) AS violations FROM {fqn} WHERE (issue_date = {latest})"
    )


def test_set_tags_sql_subset_and_empty():
    sql = contracts.set_tags_sql(HOURLY, "c", "s", {"layer": "gold"})
    assert sql == "ALTER MATERIALIZED VIEW `c`.`s`.`gold_energy_hourly` SET TAGS ('layer' = 'gold')"
    with pytest.raises(ValueError):
        contracts.set_tags_sql(HOURLY, "c", "s", {})


def test_contract_tags_add_id_and_version():
    tags = contracts.contract_tags(HOURLY)
    assert tags["contract_id"] == "energy.gold_energy_hourly"
    assert tags["contract_version"] == HOURLY.version
    assert tags["license"] == "CC-BY-4.0"


def test_quality_rules_without_required_columns():
    contract = DataContract.model_validate(_minimal())
    assert contracts.quality_rules(contract) == []


# --- apply plan --------------------------------------------------------------------------------


def _in_sync() -> dict:
    return {
        "table_comment": HOURLY.description,
        "column_comments": {c.name: c.description for c in HOURLY.columns},
        "tags": contracts.contract_tags(HOURLY),
    }


def test_apply_statements_in_sync_only_grants():
    statements = contracts.apply_statements(HOURLY, "workspace", "energy_dev", **_in_sync())
    assert statements == contracts.grant_sqls(HOURLY, "workspace", "energy_dev")


def test_apply_statements_fix_drift():
    state = _in_sync()
    state["table_comment"] = None
    state["column_comments"] = {**state["column_comments"], "solar_mwh": "old", "hour_utc": None}
    del state["column_comments"]["lignite_mwh"]  # missing column: left to the schema check
    state["tags"] = {**state["tags"], "contract_version": "0.9.0"}
    statements = contracts.apply_statements(HOURLY, "workspace", "energy_dev", **state)
    assert statements[0].startswith("COMMENT ON TABLE")
    assert [s.split("ALTER COLUMN ")[1].split(" ")[0] for s in statements[1:3]] == [
        "`hour_utc`",
        "`solar_mwh`",
    ]
    assert statements[3].endswith("SET TAGS ('contract_version' = '1.0.0')")
    assert statements[4:] == contracts.grant_sqls(HOURLY, "workspace", "energy_dev")


# --- verify -------------------------------------------------------------------------------------


def _actual(contract: DataContract) -> list[tuple[str, str]]:
    # information_schema reports lower-case types, bigint as `bigint` (full_data_type).
    return [(c.name, c.type.lower()) for c in contract.columns]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("LONG", "bigint"),
        (" Timestamp_NTZ ", "timestamp_ntz"),
        ("double", "double"),
        ("integer", "int"),
    ],
)
def test_normalize_type(raw, normalized):
    assert contracts.normalize_type(raw) == normalized


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids)
def test_schema_check_passes_on_match(contract):
    result = contracts.schema_check(contract, _actual(contract))
    assert result.passed, result.detail


def test_schema_check_reports_missing_extra_and_type():
    actual = [(n, t) for n, t in _actual(HOURLY) if n != "solar_mwh"]
    actual = [(n, "string" if n == "price_eur_mwh" else t) for n, t in actual] + [("x", "int")]
    result = contracts.schema_check(HOURLY, actual)
    assert not result.passed
    assert "missing columns: solar_mwh" in result.detail
    assert "extra columns: x" in result.detail
    assert "price_eur_mwh is string, contract says double" in result.detail


def test_schema_check_fails_when_table_missing():
    result = contracts.schema_check(HOURLY, [])
    assert not result.passed
    assert "not found" in result.detail


def test_quality_result_thresholds():
    rule = QualityRule(name="r", type="row_condition", sql="x > 0", max_violation_pct=1)
    assert contracts.quality_result(HOURLY, rule, 1000, 10).passed
    failed = contracts.quality_result(HOURLY, rule, 1000, 11)
    assert not failed.passed
    assert failed.name == "gold_energy_hourly r"
    assert failed.detail == "11 of 1000 rows (1.10%) violate; limit 1%"
    assert contracts.quality_result(HOURLY, rule, 0, 0).passed


def test_freshness_result_uses_sla_hours():
    daily = BY_TABLE["gold_energy_daily"]
    assert contracts.freshness_result(daily, NOW - timedelta(hours=95), NOW).passed
    assert not contracts.freshness_result(daily, NOW - timedelta(hours=97), NOW).passed
    assert not contracts.freshness_result(HOURLY, None, NOW).passed


def test_short_error_first_line_and_truncation():
    assert contracts.short_error(RuntimeError("boom\ntrace")) == "boom"
    assert contracts.short_error(ValueError()) == "ValueError"
    assert len(contracts.short_error(RuntimeError("x" * 500), limit=50)) == 50


# --- docs --------------------------------------------------------------------------------------


def test_render_markdown_sections():
    text = contracts.render_markdown(HOURLY)
    assert text.startswith("# gold_energy_hourly\n")
    for heading in ("## Columns", "## Quality rules", "## Request access"):
        assert heading in text
    assert "| `hour_utc` | TIMESTAMP | no | Start of the hour (UTC). |" in text
    assert "`workspace.energy_prod.gold_energy_hourly`" in text
    assert "`energy-consumers` (SELECT)" in text
