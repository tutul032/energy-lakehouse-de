"""Per-target AI/BI dashboard files (stdlib only, so CI can run it without installing the package).

The dashboard is edited in the dev UI, whose dataset SQL needs fully qualified names, so the
exported source (src/energy_market_de.lvdash.json) reads ``workspace.energy_dev.<table>``. The
build swaps that prefix for the target's schema; scripts/build_dashboard.py writes the results to
build/energy_market_de.<target>.lvdash.json, which the bundle deploys per target.
"""

from pathlib import Path

CATALOG = "workspace"
SOURCE_SCHEMA = "energy_dev"
# Must match targets.<target>.variables.schema in databricks.yml (tests/test_dashboard.py).
TARGET_SCHEMAS = {"dev": "energy_dev", "staging": "energy_staging", "prod": "energy_prod"}
DASHBOARD_NAME = "energy_market_de"


def schema_prefix(schema: str) -> str:
    """``workspace.<schema>.``: the qualifier in front of every table name in dataset SQL."""
    return f"{CATALOG}.{schema}."


def build_dashboard(source_text: str, target: str) -> str:
    """Source dashboard JSON text with ``workspace.energy_dev.`` replaced for ``target``."""
    if target not in TARGET_SCHEMAS:
        raise ValueError(f"unknown target {target!r}, expected one of {sorted(TARGET_SCHEMAS)}")
    return source_text.replace(schema_prefix(SOURCE_SCHEMA), schema_prefix(TARGET_SCHEMAS[target]))


def built_path(build_dir: Path | str, target: str) -> Path:
    """``<build_dir>/energy_market_de.<target>.lvdash.json``."""
    return Path(build_dir) / f"{DASHBOARD_NAME}.{target}.lvdash.json"
