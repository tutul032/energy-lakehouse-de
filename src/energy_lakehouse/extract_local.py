"""Run the SMARD extractor locally.

uv run python -m energy_lakehouse.extract_local --out data/landing --weeks 8
"""

import argparse
import logging
import sys

from energy_lakehouse.smard import extract
from energy_lakehouse.smard_config import DEFAULT_RESOLUTION, SERIES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download raw SMARD chunks to a local folder.")
    parser.add_argument("--out", default="data/landing", help="landing base directory")
    parser.add_argument("--weeks", type=int, default=8, help="latest weekly chunks per series")
    parser.add_argument("--resolution", default=DEFAULT_RESOLUTION)
    parser.add_argument("--overwrite", action="store_true", help="re-download existing files")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = extract(args.out, SERIES, args.resolution, args.weeks, overwrite=args.overwrite)
    print(summary)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
