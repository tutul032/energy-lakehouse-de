"""Offline tests for energy_lakehouse.transforms (no Spark)."""

import pytest

from energy_lakehouse import transforms
from energy_lakehouse.smard import output_path
from energy_lakehouse.smard_config import SERIES, SmardSeries

LOAD = SmardSeries(410, "total_load", "DE", "MWh")


def test_parse_landing_path_volume():
    path = (
        "dbfs:/Volumes/workspace/energy_dev/landing/smard/"
        "filter=4169/resolution=hour/1759701600000.json"
    )
    assert transforms.parse_landing_path(path) == (4169, "hour", 1759701600000)


def test_parse_landing_path_roundtrips_extractor_output():
    path = output_path("/Volumes/workspace/energy_dev/landing", LOAD, "quarterhour", 123)
    assert transforms.parse_landing_path(path) == (410, "quarterhour", 123)


def test_parse_landing_path_windows_separators():
    path = r"data\landing\smard\filter=410\resolution=hour\1759701600000.json"
    assert transforms.parse_landing_path(path) == (410, "hour", 1759701600000)


def test_parse_landing_path_rejects_other_files():
    with pytest.raises(ValueError):
        transforms.parse_landing_path("/Volumes/x/landing/smard/filter=410/readme.txt")


def test_agg_rule_by_unit():
    assert transforms.agg_rule("MWh") == "sum"
    assert transforms.agg_rule("EUR/MWh") == "avg"
    with pytest.raises(ValueError):
        transforms.agg_rule("GW")


def test_pivot_column():
    assert transforms.pivot_column("day_ahead_price") == "price_eur_mwh"
    assert transforms.pivot_column("solar") == "solar_mwh"
    with pytest.raises(ValueError):
        transforms.pivot_column("nuclear")


def test_hourly_agg_specs_cover_all_series():
    specs = transforms.hourly_agg_specs()
    assert [col for _, col, _ in specs] == [
        "total_load_mwh",
        "price_eur_mwh",
        "solar_mwh",
        "wind_onshore_mwh",
        "wind_offshore_mwh",
        "lignite_mwh",
        "hard_coal_mwh",
        "natural_gas_mwh",
    ]
    assert ("day_ahead_price", "price_eur_mwh", "avg") in specs
    assert ("total_load", "total_load_mwh", "sum") in specs


def test_energy_columns_exclude_price_and_include_renewables():
    cols = transforms.energy_columns()
    assert "price_eur_mwh" not in cols
    assert set(transforms.RENEWABLE_COLUMNS) <= set(cols)


def test_series_metadata_rows():
    rows = transforms.series_metadata_rows()
    assert len(rows) == len(SERIES)
    assert rows[1] == (4169, "day_ahead_price", "EUR/MWh")


def test_ddl_schema_escapes_quotes():
    ddl = transforms.ddl_schema([("a", "DOUBLE", "it's"), ("b", "DATE", "plain")])
    assert ddl == "a DOUBLE COMMENT 'it\\'s', b DATE COMMENT 'plain'"
