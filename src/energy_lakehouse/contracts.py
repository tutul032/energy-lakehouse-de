"""Data contracts for the gold tables: pydantic models, YAML loader and pure SQL renderers.

``contracts/<table>.yaml`` is the contract of one gold table. ``04_apply_contracts`` applies its
comments, tags and grants; ``05_verify_contracts`` checks schema, quality rules and freshness;
``scripts/render_contract_docs.py`` turns it into ``docs/data-products/<table>.md``.

Nothing here talks to Spark: every renderer takes ``catalog``/``schema`` (decided by the target)
and returns SQL with backtick-quoted identifiers; the notebooks run it and feed results back.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from energy_lakehouse.quality import CheckResult, freshness_check
from energy_lakehouse.utils import quote_identifier, table_name

# Repo root /contracts: the single source, used directly only from a source checkout (tests,
# scripts). The wheel ships a copy as package data (pyproject.toml: hatch force-include).
CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
CONTRACTS_PACKAGE_DATA = "contracts_data"

SEMVER_REGEX = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
IDENTIFIER_REGEX = r"^[a-z_][a-z0-9_]*$"
# Unity Catalog tag keys must not contain . , - = / : so keep them to plain identifiers.
TAG_KEY_REGEX = r"^[A-Za-z_][A-Za-z0-9_]*$"
# Tags added to every table from the contract itself (not allowed in the contract's `tags`).
RESERVED_TAG_KEYS = ("contract_id", "contract_version")
# Placeholder in a rule's `sql`/`where` for the quoted table name (e.g. for a subquery).
TABLE_PLACEHOLDER = "{table}"
# `{table:<name>}`: another table in the same catalog/schema (e.g. a daily table's hourly source).
OTHER_TABLE_REGEX = r"\{table:([a-z_][a-z0-9_]*)\}"

# Name of the implicit not-null rule generated from columns with `nullable: false`.
NOT_NULL_RULE = "required_columns_not_null"

# information_schema.columns.full_data_type spells some types differently from the DDL.
_TYPE_ALIASES = {
    "long": "bigint",
    "integer": "int",
    "short": "smallint",
    "byte": "tinyint",
    "real": "float",
}

_OBJECT_KEYWORDS = {"table": "TABLE", "materialized_view": "MATERIALIZED VIEW"}

# Contracts apply_contracts/verify_contracts act on. `draft` tables may not exist or be filled yet
# (e.g. forecast tables before the first champion model); `retired` tables are gone.
ENFORCED_STATUSES = ("active", "deprecated")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Owner(_Model):
    name: str = Field(min_length=1)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Sla(_Model):
    freshness_hours: int = Field(gt=0)
    # Column whose max() must be at most `freshness_hours` old (DATE columns count from 00:00 UTC).
    freshness_column: str
    schedule: str = Field(min_length=1)


class Consumer(_Model):
    group: str = Field(min_length=1)
    access: Literal["SELECT"] = "SELECT"


class Column(_Model):
    name: str = Field(pattern=IDENTIFIER_REGEX)
    type: str = Field(min_length=1)
    nullable: bool = True
    description: str = Field(min_length=1)

    @field_validator("type")
    @classmethod
    def _upper_type(cls, value: str) -> str:
        return " ".join(value.upper().split())


class QualityRule(_Model):
    """One check; violations / rows (after ``where``) must stay <= ``max_violation_pct``.

    - ``unique``: rows whose ``columns`` combination occurs more than once count as violations.
    - ``not_null``: rows with NULL in any of ``columns``.
    - ``row_condition``: rows where ``sql`` is not TRUE (FALSE or NULL).
    - ``sql_check``: ``sql`` is a full SELECT returning one column ``violations``, run as-is
      (only ``{catalog}`` / ``{schema}`` substituted); fails if violations > ``max_violations``.
    """

    name: str = Field(pattern=IDENTIFIER_REGEX)
    type: Literal["unique", "not_null", "row_condition", "sql_check"]
    columns: list[str] = []
    sql: str | None = None
    where: str | None = None
    max_violation_pct: float = Field(default=0, ge=0, le=100)
    max_violations: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_shape(self) -> "QualityRule":
        if self.type == "sql_check":
            if not self.sql or self.columns or self.where:
                raise ValueError(f"{self.name}: sql_check needs `sql` and no `columns`/`where`")
        elif self.type == "row_condition":
            if not self.sql or self.columns:
                raise ValueError(f"{self.name}: row_condition needs `sql` and no `columns`")
        elif not self.columns or self.sql:
            raise ValueError(f"{self.name}: {self.type} needs `columns` and no `sql`")
        return self


class DataContract(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    api_version: Literal["v1"] = Field(alias="apiVersion")
    kind: Literal["DataContract"]
    id: str = Field(min_length=1)
    version: str = Field(pattern=SEMVER_REGEX)
    status: Literal["draft", "active", "deprecated", "retired"]
    owner: Owner
    description: str = Field(min_length=1)
    table: str = Field(pattern=IDENTIFIER_REGEX)
    # Pipeline materialized views need ALTER MATERIALIZED VIEW instead of ALTER TABLE.
    object_type: Literal["table", "materialized_view"] = "table"
    grain: str = Field(min_length=1)
    primary_key: list[str] = Field(min_length=1)
    tags: dict[str, str] = {}
    sla: Sla
    consumers: list[Consumer] = []
    columns: list[Column] = Field(min_length=1)
    quality: list[QualityRule] = []

    @model_validator(mode="after")
    def _check_references(self) -> "DataContract":
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.table}: duplicate column names")
        known = set(names)
        if missing := [c for c in self.primary_key if c not in known]:
            raise ValueError(f"{self.table}: primary_key columns not in columns: {missing}")
        if self.sla.freshness_column not in known:
            raise ValueError(f"{self.table}: unknown sla.freshness_column")
        rule_names = [r.name for r in self.quality]
        if len(set(rule_names)) != len(rule_names) or NOT_NULL_RULE in rule_names:
            raise ValueError(f"{self.table}: duplicate or reserved quality rule name")
        for rule in self.quality:
            if missing := [c for c in rule.columns if c not in known]:
                raise ValueError(f"{self.table}: rule {rule.name} uses unknown columns {missing}")
        for key in self.tags:
            if not re.match(TAG_KEY_REGEX, key) or key in RESERVED_TAG_KEYS:
                raise ValueError(f"{self.table}: invalid or reserved tag key {key!r}")
        return self

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def object_keyword(self) -> str:
        """``TABLE`` or ``MATERIALIZED VIEW`` for ALTER statements."""
        return _OBJECT_KEYWORDS[self.object_type]


# --- loading -----------------------------------------------------------------------------------


def default_contracts_dir() -> Traversable:
    """Contracts packaged in the installed wheel, else the repo's contracts/ (source checkout)."""
    packaged = resources.files("energy_lakehouse") / CONTRACTS_PACKAGE_DATA
    if packaged.is_dir():
        return packaged
    if CONTRACTS_DIR.is_dir():
        return CONTRACTS_DIR
    raise FileNotFoundError(
        f"no contracts: neither package data energy_lakehouse/{CONTRACTS_PACKAGE_DATA} "
        f"nor {CONTRACTS_DIR}"
    )


def load_contract(path: Traversable | str) -> DataContract:
    """Parse and validate one contract YAML file (a path or a package resource)."""
    if isinstance(path, str):
        path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    contract = DataContract.model_validate(data)
    if Path(path.name).stem != contract.table:
        raise ValueError(f"{path}: file name must be <table>.yaml ({contract.table}.yaml)")
    return contract


def load_contracts(directory: Traversable | str | None = None) -> list[DataContract]:
    """All ``*.yaml`` contracts in ``directory`` (default: ``default_contracts_dir()``), sorted by
    file name; error if there are none."""
    if directory is None:
        directory = default_contracts_dir()
    elif isinstance(directory, str):
        directory = Path(directory)
    paths = sorted(
        (p for p in directory.iterdir() if p.is_file() and p.name.endswith(".yaml")),
        key=lambda p: p.name,
    )
    if not paths:
        raise FileNotFoundError(f"no contracts (*.yaml) in {directory}")
    return [load_contract(p) for p in paths]


def is_enforced(contract: DataContract) -> bool:
    """True if apply/verify should act on the contract (status ``active`` or ``deprecated``)."""
    return contract.status in ENFORCED_STATUSES


# --- SQL helpers -------------------------------------------------------------------------------


def sql_string(text: str) -> str:
    """Single-quoted SQL string literal (backslashes and quotes escaped, as in ddl_schema)."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def table_fqn(contract: DataContract, catalog: str, schema: str) -> str:
    return table_name(catalog, schema, contract.table)


def _fill_table(sql: str, fqn: str, catalog: str, schema: str) -> str:
    """Replace ``{table}`` with ``fqn`` and ``{table:<name>}`` with that table in catalog.schema."""
    sql = sql.replace(TABLE_PLACEHOLDER, fqn)
    return re.sub(OTHER_TABLE_REGEX, lambda m: table_name(catalog, schema, m[1]), sql)


# --- apply: comments, tags, grants ------------------------------------------------------------


def comment_on_table_sql(contract: DataContract, catalog: str, schema: str) -> str:
    fqn = table_fqn(contract, catalog, schema)
    return f"COMMENT ON TABLE {fqn} IS {sql_string(contract.description)}"


def column_comment_sql(contract: DataContract, column: Column, catalog: str, schema: str) -> str:
    fqn = table_fqn(contract, catalog, schema)
    return (
        f"ALTER {contract.object_keyword} {fqn} ALTER COLUMN {quote_identifier(column.name)} "
        f"COMMENT {sql_string(column.description)}"
    )


def contract_tags(contract: DataContract) -> dict[str, str]:
    """The contract's tags plus ``contract_id`` / ``contract_version``."""
    return {**contract.tags, "contract_id": contract.id, "contract_version": contract.version}


def set_tags_sql(
    contract: DataContract, catalog: str, schema: str, tags: Mapping[str, str] | None = None
) -> str:
    """``ALTER ... SET TAGS`` for ``tags`` (default: all of ``contract_tags``)."""
    tags = contract_tags(contract) if tags is None else tags
    if not tags:
        raise ValueError("no tags to set")
    pairs = ", ".join(f"{sql_string(k)} = {sql_string(v)}" for k, v in sorted(tags.items()))
    return (
        f"ALTER {contract.object_keyword} {table_fqn(contract, catalog, schema)} SET TAGS ({pairs})"
    )


def grant_sqls(contract: DataContract, catalog: str, schema: str) -> list[str]:
    """Per consumer group: ``USE SCHEMA`` on the schema and the contract's access on the table."""
    fqn = table_fqn(contract, catalog, schema)
    statements = []
    for consumer in contract.consumers:
        group = quote_identifier(consumer.group)
        statements.append(f"GRANT USE SCHEMA ON SCHEMA {table_name(catalog, schema)} TO {group}")
        statements.append(f"GRANT {consumer.access} ON TABLE {fqn} TO {group}")
    return statements


def apply_statements(
    contract: DataContract,
    catalog: str,
    schema: str,
    *,
    table_comment: str | None,
    column_comments: Mapping[str, str | None],
    tags: Mapping[str, str],
) -> list[str]:
    """Statements that bring the table in line with the contract, given its current metadata.

    Comments and tags are only written where they differ (the pipeline already sets the same
    comments from transforms.py); columns missing from ``column_comments`` are skipped (the
    schema check reports them). Grants are always included: GRANT is idempotent.
    """
    statements = []
    if table_comment != contract.description:
        statements.append(comment_on_table_sql(contract, catalog, schema))
    for column in contract.columns:
        if column.name in column_comments and column_comments[column.name] != column.description:
            statements.append(column_comment_sql(contract, column, catalog, schema))
    drift = {k: v for k, v in contract_tags(contract).items() if tags.get(k) != v}
    if drift:
        statements.append(set_tags_sql(contract, catalog, schema, drift))
    return [*statements, *grant_sqls(contract, catalog, schema)]


# --- information_schema lookups ---------------------------------------------------------------


def _info_schema(catalog: str, view: str) -> str:
    return table_name(catalog, "information_schema", view)


def columns_query(catalog: str, schema: str, table: str) -> str:
    """``column_name, full_data_type, comment`` of ``table`` in column order."""
    return (
        f"SELECT column_name, full_data_type, comment FROM {_info_schema(catalog, 'columns')} "
        f"WHERE table_schema = {sql_string(schema.lower())} "
        f"AND table_name = {sql_string(table.lower())} ORDER BY ordinal_position"
    )


def table_comment_query(catalog: str, schema: str, table: str) -> str:
    return (
        f"SELECT comment FROM {_info_schema(catalog, 'tables')} "
        f"WHERE table_schema = {sql_string(schema.lower())} "
        f"AND table_name = {sql_string(table.lower())}"
    )


def table_tags_query(catalog: str, schema: str, table: str) -> str:
    return (
        f"SELECT tag_name, tag_value FROM {_info_schema(catalog, 'table_tags')} "
        f"WHERE schema_name = {sql_string(schema.lower())} "
        f"AND table_name = {sql_string(table.lower())}"
    )


# --- verify: schema, quality, freshness ------------------------------------------------------


def normalize_type(data_type: str) -> str:
    """Lower-case type name with information_schema aliases mapped (``long`` -> ``bigint``)."""
    normalized = " ".join(data_type.strip().lower().split())
    return _TYPE_ALIASES.get(normalized, normalized)


def schema_check(contract: DataContract, actual: Sequence[tuple[str, str]]) -> CheckResult:
    """Compare ``(column_name, data_type)`` rows from information_schema with the contract."""
    name = f"{contract.table} schema"
    if not actual:
        return CheckResult(name, False, "table not found (no columns in information_schema)")
    found = {col.lower(): normalize_type(dtype) for col, dtype in actual}
    expected = {c.name: normalize_type(c.type) for c in contract.columns}
    problems = []
    if missing := sorted(expected.keys() - found.keys()):
        problems.append(f"missing columns: {', '.join(missing)}")
    if extra := sorted(found.keys() - expected.keys()):
        problems.append(f"extra columns: {', '.join(extra)}")
    mismatched = [
        f"{col} is {found[col]}, contract says {dtype}"
        for col, dtype in expected.items()
        if col in found and found[col] != dtype
    ]
    if mismatched:
        problems.append(f"type mismatch: {'; '.join(mismatched)}")
    detail = "; ".join(problems) or f"{len(expected)} columns match"
    return CheckResult(name, not problems, detail)


def quality_rules(contract: DataContract) -> list[QualityRule]:
    """The contract's rules, preceded by a not-null rule for columns with ``nullable: false``."""
    required = [c.name for c in contract.columns if not c.nullable]
    if not required:
        return list(contract.quality)
    implicit = QualityRule(name=NOT_NULL_RULE, type="not_null", columns=required)
    return [implicit, *contract.quality]


def quality_sql(contract: DataContract, rule: QualityRule, catalog: str, schema: str) -> str:
    """SQL returning one row ``total_rows, violations`` for ``rule`` (not for ``sql_check``)."""
    if rule.type == "sql_check":
        raise ValueError(f"{rule.name}: sql_check rules run as-is, use sql_check_sql()")
    fqn = table_fqn(contract, catalog, schema)
    source = f"{fqn} WHERE ({_fill_table(rule.where, fqn, catalog, schema)})" if rule.where else fqn
    if rule.type == "unique":
        keys = ", ".join(quote_identifier(c) for c in rule.columns)
        return (
            f"SELECT (SELECT count(*) FROM {source}) AS total_rows, "
            f"coalesce(sum(n), 0) AS violations "
            f"FROM (SELECT count(*) AS n FROM {source} GROUP BY {keys} HAVING count(*) > 1) AS dup"
        )
    if rule.type == "not_null":
        condition = " OR ".join(f"{quote_identifier(c)} IS NULL" for c in rule.columns)
    else:
        condition = f"NOT coalesce(({_fill_table(rule.sql, fqn, catalog, schema)}), FALSE)"
    return f"SELECT count(*) AS total_rows, count_if({condition}) AS violations FROM {source}"


def quality_result(
    contract: DataContract, rule: QualityRule, total_rows: int, violations: int
) -> CheckResult:
    """Pass if ``violations / total_rows`` (in %) is at most ``rule.max_violation_pct``."""
    pct = 100 * violations / total_rows if total_rows else 0.0
    detail = (
        f"{violations} of {total_rows} rows ({pct:.2f}%) violate; limit {rule.max_violation_pct:g}%"
    )
    return CheckResult(f"{contract.table} {rule.name}", pct <= rule.max_violation_pct, detail)


def sql_check_sql(rule: QualityRule, catalog: str, schema: str) -> str:
    """The ``sql_check`` SELECT with ``{catalog}`` / ``{schema}`` replaced by quoted names."""
    if rule.type != "sql_check":
        raise ValueError(f"{rule.name}: not a sql_check rule")
    sql = rule.sql.strip().replace("{catalog}", quote_identifier(catalog))
    return sql.replace("{schema}", quote_identifier(schema))


def sql_check_result(contract: DataContract, rule: QualityRule, violations: int) -> CheckResult:
    """Pass if the ``sql_check`` query's ``violations`` is at most ``rule.max_violations``."""
    detail = f"{violations} violations; limit {rule.max_violations}"
    return CheckResult(f"{contract.table} {rule.name}", violations <= rule.max_violations, detail)


def freshness_sql(contract: DataContract, catalog: str, schema: str) -> str:
    """SQL returning ``max_ts``: max of ``sla.freshness_column`` as a TIMESTAMP."""
    column = quote_identifier(contract.sla.freshness_column)
    return (
        f"SELECT CAST(max({column}) AS TIMESTAMP) AS max_ts "
        f"FROM {table_fqn(contract, catalog, schema)}"
    )


def freshness_result(contract: DataContract, max_ts: datetime | None, now: datetime) -> CheckResult:
    """Pass if ``max_ts`` is at most ``sla.freshness_hours`` old (naive = UTC)."""
    return freshness_check(
        contract.table, max_ts, now, timedelta(hours=contract.sla.freshness_hours)
    )


def short_error(exc: BaseException, limit: int = 300) -> str:
    """First line of an exception message, truncated, for the result table."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text if len(text) <= limit else text[: limit - 3] + "..."


# --- docs --------------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _rule_check(rule: QualityRule) -> str:
    if rule.type == "unique":
        check = "unique: " + ", ".join(f"`{c}`" for c in rule.columns)
    elif rule.type == "not_null":
        check = "not NULL: " + ", ".join(f"`{c}`" for c in rule.columns)
    elif rule.type == "sql_check":
        check = "SQL check (violations): `" + " ".join(rule.sql.split()) + "`"
    else:
        check = f"`{rule.sql}`"
    if rule.where:
        check += f" (only rows where `{rule.where}`)"
    return _cell(check)


def _rule_limit(rule: QualityRule) -> str:
    if rule.type == "sql_check":
        return f"{rule.max_violations} rows"
    return f"{rule.max_violation_pct:g}%"


def render_markdown(
    contract: DataContract, catalog: str = "workspace", schema: str = "energy_prod"
) -> str:
    """Human-readable data product page for ``docs/data-products/<table>.md``."""
    full_name = f"{catalog}.{schema}.{contract.table}"
    tags = ", ".join(f"`{k}={v}`" for k, v in sorted(contract_tags(contract).items()))
    groups = ", ".join(f"`{c.group}` ({c.access})" for c in contract.consumers) or "none"
    lines = [
        f"# {contract.table}",
        "",
        f"<!-- Generated from contracts/{contract.table}.yaml by "
        "scripts/render_contract_docs.py. Do not edit by hand. -->",
        "",
        contract.description,
        "",
        "| | |",
        "|---|---|",
        f"| Contract | `{contract.id}` v{contract.version} ({contract.status}) |",
        f"| Owner | {contract.owner.name} ({contract.owner.email}) |",
        f"| Table (prod) | `{full_name}` |",
        f"| Grain | {_cell(contract.grain)} |",
        f"| Primary key | {', '.join(f'`{c}`' for c in contract.primary_key)} |",
        f"| Freshness SLA | newest `{contract.sla.freshness_column}` at most "
        f"{contract.sla.freshness_hours} h old |",
        f"| Refresh | {_cell(contract.sla.schedule)} |",
        f"| Consumers | {groups} |",
        f"| Tags | {tags} |",
        "",
        "## Columns",
        "",
        "| Column | Type | Nullable | Description |",
        "|---|---|---|---|",
        *[
            f"| `{c.name}` | {c.type} | {'yes' if c.nullable else 'no'} | {_cell(c.description)} |"
            for c in contract.columns
        ],
        "",
        "## Quality rules",
        "",
        "Checked after every refresh by `05_verify_contracts` (task `verify_contracts` of",
        "`daily_refresh_job`); the job fails if a rule exceeds its violation limit.",
        "",
        "| Rule | Check | Max violations |",
        "|---|---|---|",
        *[f"| `{r.name}` | {_rule_check(r)} | {_rule_limit(r)} |" for r in quality_rules(contract)],
        "",
        "## Request access",
        "",
        f"Read access goes to the consumer groups: {groups}.",
        "",
        f"1. Ask the owner ({contract.owner.email}) to add your user or service principal to "
        "the group.",
        "2. `apply_contracts` grants the group `USE SCHEMA` and `SELECT` after every refresh; "
        f"`USE CATALOG` on `{catalog}` is granted once by a workspace admin.",
        "3. Query it:",
        "",
        "```sql",
        f"SELECT * FROM {full_name} LIMIT 10;",
        "```",
        "",
        f"To change the contract, open a pull request against `contracts/{contract.table}.yaml` "
        "and bump `version` (see *Data products* in the README).",
        "",
    ]
    return "\n".join(lines)
