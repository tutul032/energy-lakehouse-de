"""Open-Meteo weather locations: proxies for German wind, solar and load centres."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WeatherLocation:
    location_id: str
    name: str
    lat: float
    lon: float
    role: str  # which SMARD series the location is a proxy for


LOCATIONS: list[WeatherLocation] = [
    WeatherLocation("hamburg", "Hamburg", 53.55, 9.99, "onshore_wind"),
    WeatherLocation("north_sea", "North Sea", 54.5, 6.5, "offshore_wind"),
    WeatherLocation("berlin", "Berlin", 52.52, 13.41, "load"),
    WeatherLocation("essen", "Essen", 51.46, 7.01, "load"),
    WeatherLocation("frankfurt", "Frankfurt", 50.11, 8.68, "load"),
    WeatherLocation("munich", "Munich", 48.14, 11.58, "solar"),
    WeatherLocation("stuttgart", "Stuttgart", 48.78, 9.18, "solar"),
]

HOURLY_VARIABLES: list[str] = [
    "temperature_2m",
    "wind_speed_10m",
    "wind_speed_100m",
    "shortwave_radiation",
    "cloud_cover",
]

# Daily mode (forecast API): one week back for overlap; today + 2 days ahead, a margin so the full
# local (Europe/Berlin) D+1 is covered whatever the UTC run time and DST. Longer history comes
# from the archive API (--mode archive).
DEFAULT_PAST_DAYS = 7
MAX_PAST_DAYS = 92
DEFAULT_FORECAST_DAYS = 3
MAX_FORECAST_DAYS = 16

# UTC run timestamp in daily file names: run_ts=20261022T0517Z.json (minute resolution).
RUN_TS_FORMAT = "%Y%m%dT%H%MZ"
