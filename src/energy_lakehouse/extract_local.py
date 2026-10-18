"""Run the SMARD and/or Open-Meteo weather extractors locally.

uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 8 --past-days 63
uv run python -m energy_lakehouse.extract_local --source weather
"""

import argparse
import logging
import sys

from energy_lakehouse import smard, weather
from energy_lakehouse.smard_config import DEFAULT_RESOLUTION, SERIES
from energy_lakehouse.weather_config import DEFAULT_PAST_DAYS, LOCATIONS

SOURCES = ("smard", "weather", "all")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download raw SMARD/weather data locally.")
    parser.add_argument("--source", choices=SOURCES, default="all", help="which extractor(s)")
    parser.add_argument("--out", default="data/landing", help="landing base directory")
    parser.add_argument("--weeks", type=int, default=8, help="SMARD: latest weekly chunks")
    parser.add_argument("--resolution", default=DEFAULT_RESOLUTION, help="SMARD resolution")
    parser.add_argument(
        "--refresh-latest",
        type=int,
        default=smard.DEFAULT_REFRESH_LATEST,
        help="SMARD: newest chunks per series that are always re-downloaded",
    )
    parser.add_argument(
        "--past-days", type=int, default=DEFAULT_PAST_DAYS, help="weather: days back (max 92)"
    )
    parser.add_argument("--overwrite", action="store_true", help="re-download existing files")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    failed = 0
    if args.source in ("smard", "all"):
        summary = smard.extract(
            args.out,
            SERIES,
            args.resolution,
            args.weeks,
            overwrite=args.overwrite,
            refresh_latest=args.refresh_latest,
        )
        print("smard:", summary)
        failed += summary["failed"]
    if args.source in ("weather", "all"):
        summary = weather.extract(args.out, LOCATIONS, args.past_days, overwrite=args.overwrite)
        print("weather:", summary)
        failed += summary["failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
