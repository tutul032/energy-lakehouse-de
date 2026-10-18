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

DEFAULT_PAST_DAYS = 63
MAX_PAST_DAYS = 92
