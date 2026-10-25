"""Build build/energy_market_de.<target>.lvdash.json from the dev source dashboard.

    uv run python scripts/build_dashboard.py                  # all targets (dev, staging, prod)
    uv run python scripts/build_dashboard.py --target staging # one target

Replaces "workspace.energy_dev." in src/energy_market_de.lvdash.json with "workspace.<schema>."
(see energy_lakehouse.dashboard). Run it before `databricks bundle validate/deploy`: each target
overrides the dashboard's file_path with its built file (databricks.yml). Stdlib only, so CI can
run it without the package installed: PYTHONPATH=src python3 scripts/build_dashboard.py
"""

import argparse
import sys
from pathlib import Path

from energy_lakehouse.dashboard import DASHBOARD_NAME, TARGET_SCHEMAS, build_dashboard, built_path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = REPO_ROOT / "src" / f"{DASHBOARD_NAME}.lvdash.json"
BUILD_DIR = REPO_ROOT / "build"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=sorted(TARGET_SCHEMAS), help="default: all targets")
    args = parser.parse_args(argv)

    source = SOURCE_PATH.read_text(encoding="utf-8")
    BUILD_DIR.mkdir(exist_ok=True)
    for target in [args.target] if args.target else list(TARGET_SCHEMAS):
        path = built_path(BUILD_DIR, target)
        path.write_text(build_dashboard(source, target), encoding="utf-8", newline="")
        print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
