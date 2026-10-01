"""
Append a `city_ids.csv` row for every UCDB centre that has none.

Existing rows are never changed, so every published id keeps its centre.

    uv run python scripts/mint.py --ucdb GHS_UCDB_GLOBE_R2024A_V1_1.zip
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import register


def main() -> None:
    """Mint the missing ids and rewrite `city_ids.csv` in `ucdb_id` order."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ucdb", type=Path, required=True, help="the UCDB zip the recipe pins")
    args = parser.parse_args()

    ucdb = register.attach_iso3(register.read_ucdb(args.ucdb), register.read_csv(register.ISO3))
    existing = register.read_csv(register.CITY_IDS)
    city_ids = register.mint_ids(ucdb, existing)
    city_ids.sort_values("ucdb_id").to_csv(register.CITY_IDS, index=False)
    print(f"minted {len(city_ids) - len(existing)} ids")


if __name__ == "__main__":
    main()
