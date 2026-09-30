import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import pytest
from shapely.geometry import Point, box

import register

BOM = "﻿"
MOLLWEIDE = "ESRI:54009"

CENTRES = [
    # ucdb_id, name, country, lon, lat
    (1, "Tirana", "Albania", 19.8, 41.3),
    (2, "Hengshan", "China", 112.9, 27.2),
    (3, "Hengshan", "China", 113.1, 27.4),
    (4, "", "China", 114.0, 28.0),
    (5, "落原", "China", 115.0, 29.0),
    (6, "락원1동", "China", 116.0, 30.0),
]


def ucdb_frame(centres: list[tuple]) -> pd.DataFrame:
    """Return centres in the shape `read_ucdb` returns, before `iso3`."""
    return pd.DataFrame(
        {
            "ucdb_id": [centre[0] for centre in centres],
            "name": [centre[1] or None for centre in centres],
            "country": [centre[2] for centre in centres],
        }
    )


@pytest.fixture
def iso3() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "country_gad": ["Albania", "China"],
            "iso3": ["ALB", "CHN"],
            "country_unn": ["Albania", "China"],
        }
    )


@pytest.fixture
def ucdb_zip(tmp_path: Path) -> Path:
    """Write a tiny UCDB zip with the real BOMs, and no zip suffix like the bookshelf cache."""
    points = gpd.GeoSeries([Point(c[3], c[4]) for c in CENTRES], crs="EPSG:4326").to_crs(MOLLWEIDE)
    general = gpd.GeoDataFrame(
        {
            f"{BOM}ID_UC_G0": [c[0] for c in CENTRES],
            f"{BOM}GC_UCN_MAI_2025": [f"{BOM}{c[1]}" for c in CENTRES],
            f"{BOM}GC_UCN_LIS_2025": [f"{BOM}{c[1]}" for c in CENTRES],
            f"{BOM}GC_CNT_GAD_2025": [f"{BOM}{c[2]}" for c in CENTRES],
            f"{BOM}GC_CNT_UNN_2025": [f"{BOM}{c[2]}" for c in CENTRES],
            f"{BOM}GC_UCA_KM2_2025": [10] * len(CENTRES),
            f"{BOM}GC_POP_TOT_2025": [1000.5] * len(CENTRES),
            f"{BOM}GC_DEV_WIG_2025": [f"{BOM}High income"] * len(CENTRES),
            f"{BOM}GC_DEV_USR_2025": [f"{BOM}Europe"] * len(CENTRES),
            f"{BOM}GC_PLS_SCR_2025": [f"{BOM}High"] * len(CENTRES),
            f"{BOM}GC_UCB_YOB_2025": [1975] * len(CENTRES),
            f"{BOM}GC_UCB_YOD_2025": [2030] * len(CENTRES),
            f"{BOM}GC_UCM_CAP": [1, 0, 0, 0, 0, 0],
        },
        geometry=[box(p.x - 1000, p.y - 1000, p.x + 1000, p.y + 1000) for p in points],
        crs=MOLLWEIDE,
    )
    centroids = gpd.GeoDataFrame(
        {
            "ID_UC_G0": [c[0] for c in CENTRES],
            "GC_UCC_LON_2025": points.x,
            "GC_UCC_LAT_2025": points.y,
        },
        geometry=points,
        crs=MOLLWEIDE,
    )
    gpkg = tmp_path / register.GPKG_MEMBER
    pyogrio.write_dataframe(general, gpkg, layer=register.GENERAL_LAYER)
    pyogrio.write_dataframe(centroids, gpkg, layer=register.CENTROID_LAYER)
    archive = tmp_path / "cached-ucdb"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(gpkg, register.GPKG_MEMBER)
    return archive


def empty_ids() -> pd.DataFrame:
    return pd.DataFrame(columns=register.CITY_IDS_COLUMNS)


def test_read_ucdb_strips_bom_and_reprojects(ucdb_zip: Path) -> None:
    ucdb = register.read_ucdb(ucdb_zip)

    assert list(ucdb.columns) == [*register.UCDB_COLUMNS.values(), "lon", "lat"]
    assert ucdb.loc[0, "name"] == "Tirana"
    assert ucdb.loc[0, "country"] == "Albania"
    assert ucdb.loc[0, "income_group"] == "High income"
    assert pd.isna(ucdb.loc[3, "name"])
    assert ucdb.loc[0, "lon"] == pytest.approx(19.8)
    assert ucdb.loc[0, "lat"] == pytest.approx(41.3)


def test_duplicate_names_take_suffixes_in_ucdb_id_order(iso3: pd.DataFrame) -> None:
    ucdb = register.attach_iso3(ucdb_frame(CENTRES[2:0:-1]), iso3)

    minted = register.mint_ids(ucdb, empty_ids())

    assert minted.set_index("ucdb_id")["city_id"].to_dict() == {
        2: "chn-hengshan",
        3: "chn-hengshan-2",
    }


def test_existing_ids_are_kept_and_count_as_taken(iso3: pd.DataFrame) -> None:
    ucdb = register.attach_iso3(ucdb_frame(CENTRES[1:3]), iso3)
    existing = pd.DataFrame(
        [{"city_id": "chn-hengshan", "ucdb_id": 3, "minted_from": "k15-roster"}]
    )

    minted = register.mint_ids(ucdb, existing)

    assert minted.to_dict("records") == [
        {"city_id": "chn-hengshan", "ucdb_id": 3, "minted_from": "k15-roster"},
        {"city_id": "chn-hengshan-2", "ucdb_id": 2, "minted_from": "ucdb-name"},
    ]


def test_names_without_a_latin_letter_mint_from_the_ucdb_id(iso3: pd.DataFrame) -> None:
    ucdb = register.attach_iso3(ucdb_frame(CENTRES[3:]), iso3)

    minted = register.mint_ids(ucdb, empty_ids())

    assert minted["city_id"].tolist() == ["chn-uc4", "chn-uc5", "chn-uc6"]
    assert set(minted["minted_from"]) == {"ucdb-id"}


def test_unmapped_country_is_refused(iso3: pd.DataFrame) -> None:
    ucdb = ucdb_frame([(7, "Paris", "France", 2.3, 48.9)])

    with pytest.raises(register.RegisterError, match="France"):
        register.attach_iso3(ucdb, iso3)


def build(ucdb_zip: Path, iso3: pd.DataFrame, **changes: pd.DataFrame) -> pd.DataFrame:
    ucdb = register.read_ucdb(ucdb_zip)
    roster_ids = pd.DataFrame([{"city_id": "alb-tirana", "ucdb_id": 1}])
    city_ids = register.mint_ids(
        register.attach_iso3(ucdb, iso3), roster_ids.assign(minted_from="k15-roster")
    )
    crosswalk = pd.DataFrame(
        [
            {
                "city_id": "alb-tirana",
                "provider": "urbclim",
                "provider_id": "alb-tirana",
                "provider_name": "Tirana",
                "match_type": "exact_normalized_name",
                "alias_rationale": "",
            }
        ]
    )
    inputs = {
        "city_ids": city_ids,
        "roster_ids": roster_ids,
        "crosswalk": crosswalk,
    } | changes
    return register.build_cities(
        ucdb, inputs["city_ids"], iso3, inputs["roster_ids"], inputs["crosswalk"]
    )


def test_build_cities(ucdb_zip: Path, iso3: pd.DataFrame) -> None:
    cities = build(ucdb_zip, iso3)

    assert list(cities.columns) == register.CITY_COLUMNS
    assert cities["city_id"].tolist() == [
        "alb-tirana",
        "chn-hengshan",
        "chn-hengshan-2",
        "chn-uc4",
        "chn-uc5",
        "chn-uc6",
    ]
    assert cities["capital"].tolist() == [True, False, False, False, False, False]


def test_a_changed_frozen_id_is_refused(ucdb_zip: Path, iso3: pd.DataFrame) -> None:
    city_ids = register.mint_ids(
        register.attach_iso3(register.read_ucdb(ucdb_zip), iso3),
        pd.DataFrame([{"city_id": "alb-tirane", "ucdb_id": 1, "minted_from": "k15-roster"}]),
    )

    with pytest.raises(register.RegisterError, match="alb-tirana"):
        build(ucdb_zip, iso3, city_ids=city_ids)


def test_a_frozen_id_moved_to_another_centre_is_refused(ucdb_zip: Path, iso3: pd.DataFrame) -> None:
    roster_ids = pd.DataFrame([{"city_id": "alb-tirana", "ucdb_id": 2}])

    with pytest.raises(register.RegisterError, match="frozen roster ids changed"):
        build(ucdb_zip, iso3, roster_ids=roster_ids)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"city_id": "chn-hengshan", "ucdb_id": 99}, "duplicate city_id"),
        ({"city_id": "chn-other", "ucdb_id": 2}, "more than one city_id"),
        ({"city_id": "Chn Bad", "ucdb_id": 99}, "not matching"),
        ({"city_id": "chn-retired", "ucdb_id": 99}, "no longer has"),
    ],
)
def test_broken_city_ids_are_refused(
    ucdb_zip: Path, iso3: pd.DataFrame, row: dict, message: str
) -> None:
    ucdb = register.read_ucdb(ucdb_zip)
    city_ids = register.mint_ids(register.attach_iso3(ucdb, iso3), empty_ids())
    city_ids = pd.concat(
        [city_ids, pd.DataFrame([row | {"minted_from": "ucdb-name"}])],
        ignore_index=True,
    )

    with pytest.raises(register.RegisterError, match=message):
        register.check_city_ids(city_ids, ucdb["ucdb_id"])


def test_an_unminted_centre_is_refused(ucdb_zip: Path, iso3: pd.DataFrame) -> None:
    ucdb = register.read_ucdb(ucdb_zip)
    city_ids = register.mint_ids(register.attach_iso3(ucdb, iso3), empty_ids()).iloc[1:]

    with pytest.raises(register.RegisterError, match="make mint"):
        register.check_city_ids(city_ids, ucdb["ucdb_id"])


def test_committed_data_is_consistent() -> None:
    city_ids = register.read_csv(register.CITY_IDS)
    crosswalks = register.read_csv(register.CROSSWALK_URBCLIM)

    assert len(city_ids) == 11_422
    register.check_city_ids(city_ids, city_ids["ucdb_id"])
    register.check_frozen(city_ids, register.read_csv(register.ROSTER_IDS), crosswalks)
    register.check_crosswalks(crosswalks, city_ids)
    assert crosswalks["match_type"].value_counts().to_dict() == {
        "exact_normalized_name": 131,
        "reviewed_exact_alias": 11,
    }
