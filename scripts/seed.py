"""
Seed `data/` from the riskatlas UrbClim roster and GHS-UCDB R2024A.

This ran once, on fressnapf, to freeze the first ids.
It overwrites every CSV in `data/`, so later releases use `make mint` instead.

    uv run python scripts/seed.py \
        --roster <riskatlas checkout>/ingest_contracts/records/zh/receipts/k15-city-roster.json \
        --archive /mnt/fressnapf/datasets/RISKATLAS
"""

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pycountry
import pyogrio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import register

UCDB_ZIP = Path("T0_source/ghsl/ucdb_r2024a_v1_1/GHS_UCDB_GLOBE_R2024A_V1_1.zip")
VITO_GEOMETRY = Path("T0_source/urban_heat/urbclim_published/zenodo-13361538/vito-ftp-geometry")
VITO_GEOMETRY_FILE = "urban_area_epsg4326.geojson"
EXPECTED_EXACT = 131
EXPECTED_ALIASES = 11

# pycountry knows these UCDB country names under no name, official name or common name.
ISO3_OVERRIDES = {
    "Democratic Republic of the Congo": "COD",
    "Kosovo": "XKX",
    "Palestine": "PSE",
    "Turkey": "TUR",
}

# Ported from riskatlas_dm.k16.z81_join, where UrbClim and UCDB spell the country differently.
COUNTRY_NAME_EXCEPTIONS: dict[str, frozenset[str]] = {
    "BIH": frozenset({"bosniaandherzegovina"}),
    "COL": frozenset({"colombia"}),
    "CZE": frozenset({"czechia"}),
    "MEX": frozenset({"mexico"}),
}


@dataclass(frozen=True)
class ReviewedAlias:
    """An exact provider-to-provider alias, never a fuzzy-name result."""

    ucdb_id: int
    ucdb_name: str
    rationale: str


REVIEWED_CITY_ALIASES: dict[str, ReviewedAlias] = {
    "deu-frankfurt_am_main": ReviewedAlias(3400, "Frankfurt", "qualified city name"),
    "esp-sevilla": ReviewedAlias(2024, "Seville", "Spanish/English exonym"),
    "eth-addis_abeba": ReviewedAlias(5862, "Addis Ababa", "provider spelling variant"),
    "gbr-newcastle": ReviewedAlias(4446, "Newcastle upon Tyne", "qualified city name"),
    "irn-teheran": ReviewedAlias(5174, "Tehran", "provider transliteration variant"),
    "nld-rotterdam": ReviewedAlias(244, "Rotterdam [The Hague]", "UCDB combined label"),
    "srb-belgrado": ReviewedAlias(573, "Belgrade", "Spanish/English exonym"),
    "swe-goteborg": ReviewedAlias(75, "Gothenburg", "Swedish/English exonym"),
    "usa-new_york": ReviewedAlias(8099, "New York City", "qualified city name"),
    "vnm-ho_chi_minh": ReviewedAlias(5239, "Ho Chi Minh City", "qualified city name"),
    "zaf-tschwane": ReviewedAlias(3943, "Pretoria", "municipality/core-city naming"),
}

_TRANSLITERATION = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D"})


def normalize_name(value: str) -> str:
    """Conservatively normalise names without approximate matching."""
    translated = str(value).strip().lstrip("﻿").translate(_TRANSLITERATION)
    ascii_value = unicodedata.normalize("NFKD", translated).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_value.lower())


def country_matches(*, iso3: str, urbclim_country: str, ucdb_country: str) -> bool:
    """Return whether a UCDB country is the UrbClim city's country."""
    actual = normalize_name(ucdb_country)
    if actual == normalize_name(urbclim_country):
        return True
    return actual in COUNTRY_NAME_EXCEPTIONS.get(iso3, frozenset())


def iso3_table(ucdb: pd.DataFrame) -> pd.DataFrame:
    """Map every UCDB country to ISO3 by an override, its pycountry name, then its UN name."""
    rows = []
    pairs = ucdb[["country", "country_un"]].drop_duplicates().sort_values("country")
    if pairs["country"].duplicated().any():
        raise register.RegisterError("a UCDB country carries more than one UN name")
    for gad, unn in pairs.itertuples(index=False):
        code = ISO3_OVERRIDES.get(gad)
        for name in (gad, unn):
            if code is not None:
                break
            try:
                code = pycountry.countries.lookup(name).alpha_3
            except LookupError:
                continue
        if code is None:
            raise register.RegisterError(f"no ISO3 for UCDB country {gad!r} ({unn!r})")
        rows.append({"country_gad": gad, "iso3": code, "country_unn": unn})
    return pd.DataFrame(rows)


def read_roster(path: Path, geometry_root: Path) -> gpd.GeoDataFrame:
    """Read the frozen UrbClim roster with the VITO urban area of each city."""
    cities = pd.DataFrame(json.loads(path.read_text(encoding="utf-8"))["cities"])
    directories = {child.name for child in geometry_root.iterdir() if child.is_dir()}
    unmatched = sorted(set(cities["city_id"]) ^ directories)
    if unmatched:
        raise register.RegisterError(
            f"roster ids and VITO geometry directories disagree: {unmatched}"
        )
    geometries = []
    for city_id in cities["city_id"]:
        area = gpd.read_file(geometry_root / city_id / VITO_GEOMETRY_FILE)
        geometries.append(area.to_crs("EPSG:4326").union_all())
    return gpd.GeoDataFrame(
        cities[["city_id", "city_name", "iso3", "country"]],
        geometry=geometries,
        crs="EPSG:4326",
    )


def join_urbclim(roster: gpd.GeoDataFrame, archive: Path) -> pd.DataFrame:
    """
    Match each UrbClim city to exactly one UCDB centre.

    A match needs a same-country spatial intersect and either one exact normalised name
    or a reviewed alias that pins the UCDB id and label.
    """
    ucdb = register.clean_ucdb_columns(
        pyogrio.read_dataframe(register.gpkg_path(archive), layer=register.GENERAL_LAYER)
    )
    cities = roster.to_crs(ucdb.crs)
    rows = []
    for city in cities.sort_values("city_id").itertuples():
        candidates = ucdb.loc[ucdb.geometry.intersects(city.geometry)]
        candidates = candidates.loc[
            candidates["GC_CNT_GAD_2025"].map(
                lambda value, iso3=city.iso3, country=city.country: country_matches(
                    iso3=iso3, urbclim_country=country, ucdb_country=str(value)
                )
            )
        ]
        if candidates.empty:
            raise register.RegisterError(f"no same-country spatial UCDB match for {city.city_id}")
        exact = candidates.loc[
            candidates["GC_UCN_MAI_2025"].map(
                lambda value, name=city.city_name: (
                    normalize_name(str(value)) == normalize_name(name)
                )
            )
        ]
        alias = REVIEWED_CITY_ALIASES.get(city.city_id)
        if len(exact) == 1:
            selected, match_type, rationale = exact.iloc[0], "exact_normalized_name", ""
        elif alias is not None:
            pinned = candidates.loc[
                (candidates["ID_UC_G0"] == alias.ucdb_id)
                & (candidates["GC_UCN_MAI_2025"] == alias.ucdb_name)
            ]
            if len(pinned) != 1:
                raise register.RegisterError(
                    f"reviewed alias no longer resolves for {city.city_id}: "
                    f"UCDB {alias.ucdb_id} {alias.ucdb_name!r}"
                )
            selected, match_type, rationale = (
                pinned.iloc[0],
                "reviewed_exact_alias",
                alias.rationale,
            )
        else:
            names = sorted(str(value) for value in candidates["GC_UCN_MAI_2025"])
            raise register.RegisterError(
                f"ambiguous or non-exact match for {city.city_id}, candidates {names}"
            )
        rows.append(
            {
                "city_id": city.city_id,
                "ucdb_id": int(selected["ID_UC_G0"]),
                "provider": "urbclim",
                "provider_id": city.city_id,
                "provider_name": city.city_name,
                "match_type": match_type,
                "alias_rationale": rationale,
            }
        )
    joined = pd.DataFrame(rows)
    counts = joined["match_type"].value_counts()
    if (
        joined["ucdb_id"].nunique() != len(roster)
        or counts.get("exact_normalized_name", 0) != EXPECTED_EXACT
        or counts.get("reviewed_exact_alias", 0) != EXPECTED_ALIASES
    ):
        raise register.RegisterError(
            f"UrbClim join is not the reviewed 1:1 result: {counts.to_dict()}"
        )
    return joined


def main() -> None:
    """Write iso3.csv, roster_ids.csv, crosswalk-urbclim.csv and city_ids.csv."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--roster", type=Path, required=True, help="riskatlas k15-city-roster.json")
    parser.add_argument("--archive", type=Path, required=True, help="RISKATLAS archive root")
    args = parser.parse_args()

    archive = args.archive / UCDB_ZIP
    ucdb = register.read_ucdb(archive)
    iso3 = iso3_table(ucdb)
    ucdb = register.attach_iso3(ucdb, iso3)

    joined = join_urbclim(read_roster(args.roster, args.archive / VITO_GEOMETRY), archive)
    roster_ids = joined[["city_id", "ucdb_id"]].sort_values("city_id")
    frozen = roster_ids.assign(minted_from="k15-roster")
    city_ids = register.mint_ids(ucdb, frozen)
    crosswalk = joined[register.CROSSWALK_COLUMNS].sort_values("city_id")
    register.build_cities(ucdb, city_ids, iso3, roster_ids, crosswalk)

    register.DATA.mkdir(exist_ok=True)
    iso3.to_csv(register.ISO3, index=False)
    roster_ids.to_csv(register.ROSTER_IDS, index=False)
    crosswalk.to_csv(register.CROSSWALK_URBCLIM, index=False)
    city_ids.sort_values("ucdb_id").to_csv(register.CITY_IDS, index=False)
    print(city_ids["minted_from"].value_counts().to_string())
    print(crosswalk["match_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
