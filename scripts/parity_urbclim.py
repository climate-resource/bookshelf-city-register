"""
Compare the `ucdb-buffer-5km` boundaries of the 142 UrbClim cities with the riskatlas source scope.

With `--legacy`, the reference is the K16 Z81 source-scope parquet riskatlas serves.
Without it, the reference is recomputed from the UCDB zip with the K16 Z81 rules,
copied from `riskatlas_dm/k16/z81_join.py` and `z81_campaign.py`.

    uv run python scripts/parity_urbclim.py --ucdb <zip> --boundaries <parquet> [--legacy <parquet>]
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
from shapely import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import register

TOLERANCE = 1.0e-6
# Copied rather than imported from `register`, so the reference cannot drift with the build.
LEGACY_REPAIR_TOLERANCE = 1.0e-9
LEGACY_BUFFER_METRES = 5_000.0


def legacy_scope(archive: Path, ucdb_ids: dict[int, str]) -> gpd.GeoDataFrame:
    """Recompute the K16 Z81 source scope for the given centres, keyed by `city_id`."""
    ucdb = register.read_ucdb_geometries(archive)
    ucdb = ucdb.loc[ucdb["ucdb_id"].isin(ucdb_ids)].copy()
    ucdb["city_id"] = ucdb["ucdb_id"].map(ucdb_ids)
    repaired = []
    for geometry in ucdb.geometry:
        output = geometry if geometry.is_valid else make_valid(geometry)
        delta = abs(float(output.area) - float(geometry.area)) / float(geometry.area)
        if not output.is_valid or delta > LEGACY_REPAIR_TOLERANCE:
            raise register.RegisterError(f"legacy repair refused, area delta {delta}")
        repaired.append(output)
    ucdb = ucdb.set_geometry(gpd.GeoSeries(repaired, index=ucdb.index, crs=ucdb.crs))
    ucdb.geometry = ucdb.geometry.buffer(LEGACY_BUFFER_METRES)
    return ucdb[["city_id", "geometry"]].to_crs("EPSG:4326")


def main() -> None:
    """Print the worst symmetric-difference fraction and every city above the tolerance."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ucdb", type=Path, required=True, help="the UCDB zip the recipe pins")
    parser.add_argument("--boundaries", type=Path, required=True, help="the built boundaries")
    parser.add_argument("--legacy", type=Path, help="the riskatlas source-scope parquet")
    args = parser.parse_args()

    roster = register.read_csv(register.ROSTER_IDS)
    if args.legacy:
        reference = gpd.read_parquet(args.legacy)[["city_id", "geometry"]]
        source = f"legacy source scope {args.legacy}"
    else:
        reference = legacy_scope(args.ucdb, dict(zip(roster["ucdb_id"], roster["city_id"])))
        source = "K16 Z81 rules recomputed from the UCDB zip"

    boundaries = gpd.read_parquet(args.boundaries)
    ours = boundaries.loc[boundaries["kind"] == "ucdb-buffer-5km", ["city_id", "geometry"]]
    paired = (
        roster[["city_id"]]
        .merge(ours, on="city_id", how="left", validate="one_to_one")
        .merge(
            reference.rename_geometry("reference").to_crs(ours.crs),
            on="city_id",
            how="left",
            validate="one_to_one",
        )
    )
    missing = paired["geometry"].isna() | paired["reference"].isna()
    unpaired = paired.loc[missing, "city_id"].tolist()
    paired = paired.loc[~missing]

    mollweide = "ESRI:54009"
    left = gpd.GeoSeries(paired["geometry"], crs=ours.crs).to_crs(mollweide)
    right = gpd.GeoSeries(paired["reference"], crs=ours.crs).to_crs(mollweide)
    paired["fraction"] = left.symmetric_difference(right).area / right.area
    failed = paired.loc[~(paired["fraction"] < TOLERANCE), ["city_id", "fraction"]]

    print(f"reference: {source}")
    print(f"cities compared: {len(paired)} of {len(roster)}")
    print(f"max symmetric-difference fraction: {paired['fraction'].max():.3e}")
    print(f"tolerance: {TOLERANCE:.0e}")
    if unpaired:
        print(f"missing from an input: {unpaired}")
    if not failed.empty:
        print(failed.to_string(index=False))
    if unpaired or not failed.empty:
        sys.exit(1)
    print("parity: pass")


if __name__ == "__main__":
    main()
