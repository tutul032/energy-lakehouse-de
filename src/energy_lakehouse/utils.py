"""Small, pure helpers shared by notebooks and pipelines."""


def quote_identifier(identifier: str) -> str:
    """Quote one identifier with backticks, escaping embedded backticks by doubling them."""
    if not identifier or not identifier.strip():
        raise ValueError("identifier must be a non-empty string")
    return "`" + identifier.replace("`", "``") + "`"


def table_name(catalog: str, schema: str, table: str | None = None) -> str:
    """Return a backtick-quoted Unity Catalog name.

    ``table_name("workspace", "energy_dev", "load")`` -> ``"`workspace`.`energy_dev`.`load`"``.
    Omit ``table`` to get the 2-part schema name (e.g. for ``CREATE SCHEMA``).
    """
    parts = [catalog, schema] if table is None else [catalog, schema, table]
    return ".".join(quote_identifier(p) for p in parts)
