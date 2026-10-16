"""SMARD series to extract (filter ids from https://www.smard.de/app/chart_data)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SmardSeries:
    filter_id: int
    name: str
    region: str
    unit: str


SERIES: list[SmardSeries] = [
    SmardSeries(410, "total_load", "DE", "MWh"),
    SmardSeries(4169, "day_ahead_price", "DE-LU", "EUR/MWh"),
    SmardSeries(4068, "solar", "DE", "MWh"),
    SmardSeries(4067, "wind_onshore", "DE", "MWh"),
    SmardSeries(1225, "wind_offshore", "DE", "MWh"),
    SmardSeries(1223, "lignite", "DE", "MWh"),
    SmardSeries(4069, "hard_coal", "DE", "MWh"),
    SmardSeries(4071, "natural_gas", "DE", "MWh"),
]

DEFAULT_RESOLUTION = "hour"
