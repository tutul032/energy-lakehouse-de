"""Render contracts/*.yaml into docs/data-products/<table>.md (one data product page each).

    uv run python scripts/render_contract_docs.py           # (re)write the pages
    uv run python scripts/render_contract_docs.py --check   # exit 1 if a page is out of date

tests/test_contracts.py fails while a page differs from its contract, so re-run this after every
contract change.
"""

import argparse
import sys
from pathlib import Path

from energy_lakehouse.contracts import CONTRACTS_DIR, load_contracts, render_markdown

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs" / "data-products"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report out-of-date pages")
    args = parser.parse_args(argv)

    stale = []
    for contract in load_contracts(CONTRACTS_DIR):
        path = DOCS_DIR / f"{contract.table}.md"
        text = render_markdown(contract)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        stale.append(path)
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(DOCS_DIR.parents[1])}")

    if args.check and stale:
        print("out of date (run scripts/render_contract_docs.py):", *map(str, stale), sep="\n  ")
        return 1
    if not stale:
        print("data product pages up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
