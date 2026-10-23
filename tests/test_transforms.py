"""Offline tests for energy_lakehouse.transforms (no Spark)."""

import re
from datetime import UTC, date, datetime

import pytest

from energy_lakehouse import transforms, weather
from energy_lakehouse.smard import output_path
from energy_lakehouse.smard_config import SERIES, SmardSeries
from energy_lakehouse.weather_config import (
    HOURLY_VARIABLES,
    LOCATIONS,
    RUN_TS_FORMAT,
    WeatherLocation,
)

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


def test_parse_weather_landing_path_run_ts_roundtrips_extractor_output():
    loc = WeatherLocation("north_sea", "North Sea", 54.5, 6.5, "offshore_wind")
    run_ts = datetime(2026, 10, 19, 5, 17, tzinfo=UTC)
    path = weather.output_path("/Volumes/workspace/energy_dev/landing", loc, run_ts)
    assert transforms.parse_weather_landing_path(path) == ("north_sea", run_ts, False)
    assert re.search(transforms.RUN_TS_REGEX, path).group(1) == "20261019T0517Z"
    assert not re.search(transforms.RUN_DATE_REGEX, path)


def test_parse_weather_landing_path_old_run_date_is_midnight_utc():
    path = r"data\landing\weather\location=essen\run_date=2026-01-02.json"
    midnight = datetime(2026, 1, 2, tzinfo=UTC)
    assert transforms.parse_weather_landing_path(path) == ("essen", midnight, False)
    assert not re.search(transforms.RUN_TS_REGEX, path)


def test_parse_weather_landing_path_orders_old_before_new_naming_same_day():
    old = transforms.parse_weather_landing_path("w/location=essen/run_date=2026-10-19.json")
    new = transforms.parse_weather_landing_path("w/location=essen/run_ts=20261019T0517Z.json")
    later = transforms.parse_weather_landing_path("w/location=essen/run_ts=20261019T1130Z.json")
    assert old[1] < new[1] < later[1]


def test_parse_weather_landing_path_archive_is_day_after_end():
    loc = WeatherLocation("essen", "Essen", 51.46, 7.01, "load")
    path = weather.archive_output_path("data/landing", loc, date(2026, 8, 1), date(2026, 10, 15))
    run_ts = datetime(2026, 10, 16, tzinfo=UTC)
    assert transforms.parse_weather_landing_path(path) == ("essen", run_ts, True)
    assert re.search(transforms.ARCHIVE_RUN_REGEX, path).groups() == ("2026-08-01", "2026-10-15")
    assert not re.search(transforms.RUN_DATE_REGEX, path)
    assert not re.search(transforms.RUN_TS_REGEX, path)


def test_run_ts_spark_format_matches_python_format():
    # yyyyMMdd'T'HHmm'Z' (Java) <-> %Y%m%dT%H%MZ (Python): same fields, same literals.
    assert RUN_TS_FORMAT == "%Y%m%dT%H%MZ"
    assert transforms.RUN_TS_SPARK_FORMAT == "yyyyMMdd'T'HHmm'Z'"


def test_parse_weather_landing_path_rejects_other_files():
    with pytest.raises(ValueError):
        transforms.parse_weather_landing_path("/Volumes/x/landing/weather/location=essen/a.json")


def test_weather_raw_schema_has_time_and_all_variables():
    ddl = transforms.weather_raw_schema()
    assert "hourly STRUCT<time: ARRAY<BIGINT>, temperature_2m: ARRAY<DOUBLE>," in ddl
    for var in HOURLY_VARIABLES:
        assert f"{var}: ARRAY<DOUBLE>" in ddl


def test_weather_location_rows():
    rows = transforms.weather_location_rows()
    assert len(rows) == len(LOCATIONS)
    assert rows[1] == ("north_sea", "North Sea", "offshore_wind")


def test_weather_role_agg_specs_mapping():
    specs = transforms.weather_role_agg_specs()
    assert [(s.column, s.role, s.variable, s.func) for s in specs] == [
        ("temperature_load_avg_c", "load", "temperature_2m", "avg"),
        ("wind_speed_100m_onshore_avg_ms", "onshore_wind", "wind_speed_100m", "avg"),
        ("wind_speed_100m_offshore_ms", "offshore_wind", "wind_speed_100m", "avg"),
        ("shortwave_radiation_solar_avg_wm2", "solar", "shortwave_radiation", "avg"),
    ]


def test_weather_role_agg_specs_every_role_has_locations():
    roles = {loc.role for loc in LOCATIONS}
    assert {s.role for s in transforms.weather_role_agg_specs()} <= roles


def test_weather_role_agg_specs_rejects_role_without_location():
    only_load = [WeatherLocation("berlin", "Berlin", 52.52, 13.41, "load")]
    with pytest.raises(ValueError, match="onshore_wind"):
        transforms.weather_role_agg_specs(locations=only_load)


def test_weather_role_agg_specs_rejects_unknown_variable_and_func():
    bad_var = transforms.WeatherRoleAgg("x", "load", "humidity", "avg", "")
    with pytest.raises(ValueError, match="humidity"):
        transforms.weather_role_agg_specs(aggs=[bad_var])
    bad_func = transforms.WeatherRoleAgg("x", "load", "temperature_2m", "sum", "")
    with pytest.raises(ValueError, match="sum"):
        transforms.weather_role_agg_specs(aggs=[bad_func])


def test_weather_role_agg_specs_rejects_duplicate_columns():
    agg = transforms.WEATHER_ROLE_AGGS[0]
    with pytest.raises(ValueError, match="duplicate"):
        transforms.weather_role_agg_specs(aggs=[agg, agg])


def test_gold_weather_columns_extend_hourly_columns():
    cols = transforms.gold_weather_columns()
    names = [name for name, _, _ in cols]
    assert names[: len(transforms.GOLD_ENERGY_HOURLY_COLUMNS)] == [
        name for name, _, _ in transforms.GOLD_ENERGY_HOURLY_COLUMNS
    ]
    assert names[-4:] == [a.column for a in transforms.WEATHER_ROLE_AGGS]
    assert ("hour_local", "TIMESTAMP_NTZ") in [(n, t) for n, t, _ in cols]
    assert all(comment for _, _, comment in cols)
    assert len(set(names)) == len(names)


def test_gold_weather_forecast_columns_reuse_role_aggs():
    cols = transforms.gold_weather_forecast_columns()
    names = [name for name, _, _ in cols]
    assert names == [
        "hour_utc",
        "hour_local",
        *[a.column for a in transforms.WEATHER_ROLE_AGGS],
        "issued_at",
    ]
    observed = {n: (t, c) for n, t, c in transforms.GOLD_ENERGY_WEATHER_HOURLY_COLUMNS}
    assert all(observed[n] == (t, c) for n, t, c in cols if n != "issued_at")
    assert transforms.GOLD_TABLE_COLUMNS["gold_weather_forecast_hourly"] == cols


def test_silver_weather_forecast_columns_cover_all_variables():
    cols = transforms.SILVER_WEATHER_FORECAST_HOURLY_COLUMNS
    names = [name for name, _, _ in cols]
    assert set(HOURLY_VARIABLES) <= set(names)
    assert {"location_id", "timestamp_utc", "run_date", "run_ts_utc", "ingested_at"} <= set(names)
    assert len(set(names)) == len(names)
    assert all(comment for _, _, comment in cols)


def test_contract_tables_are_gold_plus_forecast_silver():
    assert set(transforms.CONTRACT_TABLE_COLUMNS) == {
        *transforms.GOLD_TABLE_COLUMNS,
        "silver_weather_forecast_hourly",
    }
    assert set(transforms.CONTRACT_TABLE_COMMENTS) == set(transforms.CONTRACT_TABLE_COLUMNS)
