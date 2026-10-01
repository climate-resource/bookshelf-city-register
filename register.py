"""
Identity rules for the city register, shared by `build.py` and the scripts.
"""

import re
import unicodedata
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

GPKG_MEMBER = "GHS_UCDB_GLOBE_R2024A.gpkg"
GENERAL_LAYER = "GHS_UCDB_THEME_GENERAL_CHARACTERISTICS_GLOBE_R2024A"
CENTROID_LAYER = "UC_centroids"
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,199}$")

# Both match the riskatlas K16 Z81 source scope, so SUHI stays at parity with the served stores.
BUFFER_METRES = 5_000.0
REPAIR_AREA_TOLERANCE = 1.0e-9

DATA = Path(__file__).parent / "data"
CITY_IDS = DATA / "city_ids.csv"
ISO3 = DATA / "iso3.csv"
ROSTER_IDS = DATA / "roster_ids.csv"
CROSSWALK_URBCLIM = DATA / "crosswalk-urbclim.csv"

CITY_IDS_COLUMNS = ["city_id", "ucdb_id", "minted_from"]
CROSSWALK_COLUMNS = [
    "city_id",
    "provider",
    "provider_id",
    "provider_name",
    "match_type",
    "alias_rationale",
]

UCDB_COLUMNS = {
    "ID_UC_G0": "ucdb_id",
    "GC_UCN_MAI_2025": "name",
    "GC_UCN_LIS_2025": "alternative_names",
    "GC_CNT_GAD_2025": "country",
    "GC_CNT_UNN_2025": "country_un",
    "GC_UCA_KM2_2025": "area_km2",
    "GC_POP_TOT_2025": "population_2025",
    "GC_DEV_WIG_2025": "income_group",
    "GC_DEV_USR_2025": "region",
    "GC_PLS_SCR_2025": "name_quality",
    "GC_UCB_YOB_2025": "first_year",
    "GC_UCB_YOD_2025": "last_year",
    "GC_UCM_CAP": "capital",
}

CITY_COLUMNS = [
    "city_id",
    "ucdb_id",
    "name",
    "alternative_names",
    "iso3",
    "country",
    "country_un",
    "lon",
    "lat",
    "area_km2",
    "population_2025",
    "income_group",
    "region",
    "name_quality",
    "first_year",
    "last_year",
    "capital",
]

BOUNDARY_COLUMNS = ["city_id", "ucdb_id", "kind", "repair", "geometry"]
BOUNDARY_KINDS = ("ucdb", "ucdb-buffer-5km")


class RegisterError(ValueError):
    """Raised when the register would break one of its identity rules."""


def slug(value: str) -> str:
    """
    Return the ASCII slug riskatlas used for the frozen UrbClim ids.

    NFKD to ASCII, lower case, and every run of other characters becomes `_`.
    """
    normal = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", normal.lower()).strip("_")


def clean_ucdb_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Strip the leading BOM UCDB puts on column names and text values."""
    frame = frame.rename(columns={column: str(column).lstrip("﻿") for column in frame.columns})
    for column in frame.columns:
        if not pd.api.types.is_numeric_dtype(frame[column]):
            frame[column] = frame[column].map(
                lambda value: value.strip().lstrip("﻿").strip() if isinstance(value, str) else value
            )
    return frame


def read_csv(path: Path) -> pd.DataFrame:
    """Read a committed CSV, keeping empty cells as empty strings."""
    return pd.read_csv(path, keep_default_na=False, dtype={"ucdb_id": "int64"})


def gpkg_path(archive: Path) -> str:
    """Return the GDAL path of the gpkg inside the UCDB zip, which may have no `.zip` suffix."""
    return f"/vsizip/{{{Path(archive).resolve()}}}/{GPKG_MEMBER}"


def read_ucdb(archive: Path) -> pd.DataFrame:
    """
    Read one row per UCDB centre with its WGS84 centroid, without geometries.

    Empty names become missing values.
    """
    path = gpkg_path(archive)
    general = clean_ucdb_columns(
        pyogrio.read_dataframe(path, layer=GENERAL_LAYER, read_geometry=False)
    )
    missing = set(UCDB_COLUMNS) - set(general.columns)
    if missing:
        raise RegisterError(f"UCDB general layer lacks columns {sorted(missing)}")
    general = general[list(UCDB_COLUMNS)].rename(columns=UCDB_COLUMNS)
    for column in ("name", "alternative_names"):
        general[column] = general[column].where(general[column].fillna("") != "", None)

    centroids = clean_ucdb_columns(
        pyogrio.read_dataframe(path, layer=CENTROID_LAYER, read_geometry=False)
    )
    crs = pyogrio.read_info(path, layer=CENTROID_LAYER)["crs"]
    to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon, lat = to_wgs84.transform(
        centroids["GC_UCC_LON_2025"].to_numpy(), centroids["GC_UCC_LAT_2025"].to_numpy()
    )
    centroids = pd.DataFrame({"ucdb_id": centroids["ID_UC_G0"], "lon": lon, "lat": lat})

    frame = general.merge(centroids, on="ucdb_id", how="left", validate="one_to_one")
    if frame["lon"].isna().any():
        missing_ids = frame.loc[frame["lon"].isna(), "ucdb_id"].tolist()
        raise RegisterError(f"UCDB centres without a centroid: {missing_ids[:10]}")
    return frame


def attach_iso3(ucdb: pd.DataFrame, iso3: pd.DataFrame) -> pd.DataFrame:
    """Add `iso3` from the `country` column, refusing a country the table does not map."""
    mapping = dict(zip(iso3["country_gad"], iso3["iso3"], strict=True))
    unmapped = sorted(set(ucdb["country"].dropna()) - set(mapping))
    if unmapped or ucdb["country"].isna().any():
        raise RegisterError(f"UCDB countries missing from iso3.csv: {unmapped or ['<missing>']}")
    return ucdb.assign(iso3=ucdb["country"].map(mapping))


def mint_ids(ucdb: pd.DataFrame, existing: pd.DataFrame) -> pd.DataFrame:
    """
    Return `existing` plus a new row for every UCDB centre it does not hold.

    `ucdb` needs `ucdb_id`, `name` and `iso3`.
    New centres are minted in ascending `ucdb_id` order, so the lower id keeps the bare name.
    A name whose slug holds no letter mints `<iso3>-uc<ucdb_id>`.
    """
    taken = set(existing["city_id"])
    held = set(existing["ucdb_id"])
    rows = []
    for centre in ucdb.sort_values("ucdb_id").itertuples():
        if centre.ucdb_id in held:
            continue
        prefix = str(centre.iso3).lower()
        name_slug = slug(centre.name) if isinstance(centre.name, str) else ""
        if re.search("[a-z]", name_slug):
            base = f"{prefix}-{name_slug}"
            city_id, suffix = base, 1
            while city_id in taken:
                suffix += 1
                city_id = f"{base}-{suffix}"
            minted_from = "ucdb-name"
        else:
            city_id = f"{prefix}-uc{centre.ucdb_id}"
            minted_from = "ucdb-id"
            if city_id in taken:
                raise RegisterError(f"{city_id} is already taken")
        taken.add(city_id)
        rows.append(
            {
                "city_id": city_id,
                "ucdb_id": int(centre.ucdb_id),
                "minted_from": minted_from,
            }
        )
    minted = pd.DataFrame(rows, columns=CITY_IDS_COLUMNS)
    return pd.concat([existing[CITY_IDS_COLUMNS], minted], ignore_index=True)


def check_city_ids(city_ids: pd.DataFrame, ucdb_ids: pd.Series) -> None:
    """Refuse a register that does not give every UCDB centre exactly one valid id."""
    duplicated = city_ids.loc[city_ids["city_id"].duplicated(), "city_id"].tolist()
    if duplicated:
        raise RegisterError(f"duplicate city_id: {duplicated[:10]}")
    duplicated = city_ids.loc[city_ids["ucdb_id"].duplicated(), "ucdb_id"].tolist()
    if duplicated:
        raise RegisterError(f"UCDB id with more than one city_id: {duplicated[:10]}")
    invalid = [city_id for city_id in city_ids["city_id"] if not ID_PATTERN.match(str(city_id))]
    if invalid:
        raise RegisterError(f"city_id not matching {ID_PATTERN.pattern}: {invalid[:10]}")
    unminted = sorted(set(ucdb_ids) - set(city_ids["ucdb_id"]))
    if unminted:
        raise RegisterError(f"UCDB ids without a city_id, run `make mint`: {unminted[:10]}")
    retired = sorted(set(city_ids["ucdb_id"]) - set(ucdb_ids))
    if retired:
        raise RegisterError(f"city_ids.csv holds UCDB ids the source no longer has: {retired[:10]}")


def check_frozen(city_ids: pd.DataFrame, roster_ids: pd.DataFrame, crosswalk: pd.DataFrame) -> None:
    """Refuse a register that drops one of the frozen UrbClim ids or moves it to another centre."""
    held = set(zip(city_ids["city_id"], city_ids["ucdb_id"], strict=True))
    changed = sorted(
        city_id
        for city_id, ucdb_id in zip(roster_ids["city_id"], roster_ids["ucdb_id"], strict=True)
        if (city_id, ucdb_id) not in held
    )
    if changed:
        raise RegisterError(f"frozen roster ids changed in city_ids.csv: {changed[:10]}")
    urbclim = crosswalk.loc[crosswalk["provider"] == "urbclim"]
    disagree = sorted(set(urbclim["city_id"]) ^ set(roster_ids["city_id"]))
    if disagree:
        raise RegisterError(f"UrbClim crosswalk and roster ids disagree: {disagree[:10]}")


def check_crosswalks(crosswalks: pd.DataFrame, city_ids: pd.DataFrame) -> None:
    """Refuse a crosswalk row that points outside the register or repeats a provider id."""
    unknown = sorted(set(crosswalks["city_id"]) - set(city_ids["city_id"]))
    if unknown:
        raise RegisterError(f"crosswalk city_id not in the register: {unknown[:10]}")
    duplicated = crosswalks.loc[
        crosswalks.duplicated(["provider", "provider_id"]), "provider_id"
    ].tolist()
    if duplicated:
        raise RegisterError(f"crosswalk provider id listed twice: {duplicated[:10]}")


def build_cities(
    ucdb: pd.DataFrame,
    city_ids: pd.DataFrame,
    iso3: pd.DataFrame,
    roster_ids: pd.DataFrame,
    crosswalk: pd.DataFrame,
) -> pd.DataFrame:
    """Return the validated `cities` table, one row per UCDB centre."""
    ucdb = attach_iso3(ucdb, iso3)
    check_city_ids(city_ids, ucdb["ucdb_id"])
    check_frozen(city_ids, roster_ids, crosswalk)
    cities = city_ids[["city_id", "ucdb_id"]].merge(
        ucdb, on="ucdb_id", how="inner", validate="one_to_one"
    )
    if not (cities["lon"].between(-180, 180) & cities["lat"].between(-90, 90)).all():
        raise RegisterError("a centroid falls outside WGS84 bounds")
    cities["capital"] = cities["capital"].astype(bool)
    return cities[CITY_COLUMNS].sort_values("ucdb_id").reset_index(drop=True)


def read_ucdb_geometries(archive: Path) -> gpd.GeoDataFrame:
    """Return `ucdb_id` and the urban centre polygon in the UCDB's own CRS, World Mollweide."""
    general = clean_ucdb_columns(pyogrio.read_dataframe(gpkg_path(archive), layer=GENERAL_LAYER))
    return general.rename(columns={"ID_UC_G0": "ucdb_id"})[["ucdb_id", "geometry"]]


def repair_geometry(geometry: BaseGeometry, ucdb_id: int) -> tuple[BaseGeometry, str]:
    """
    Return a valid polygon and the repair applied, `none` or `make_valid`.

    Refuses a repair that is not polygonal or changes the area by more than
    `REPAIR_AREA_TOLERANCE` as a fraction.
    """
    if geometry.is_valid:
        return geometry, "none"
    repaired = make_valid(geometry)
    if repaired.is_empty or repaired.geom_type not in {"Polygon", "MultiPolygon"}:
        raise RegisterError(f"UCDB {ucdb_id} repairs to a {repaired.geom_type}")
    delta = abs(repaired.area - geometry.area) / geometry.area if geometry.area else float("inf")
    if not repaired.is_valid or delta > REPAIR_AREA_TOLERANCE:
        raise RegisterError(f"UCDB {ucdb_id} repair changes its area by {delta:.3g}")
    return repaired, "make_valid"


def build_boundaries(geometries: gpd.GeoDataFrame, city_ids: pd.DataFrame) -> gpd.GeoDataFrame:
    """
    Return two EPSG:4326 rows per city, its UCDB polygon and that polygon buffered by 5 km.

    `geometries` is `read_ucdb_geometries` output, in a metric CRS.
    """
    if not geometries.crs or not geometries.crs.is_projected:
        raise RegisterError("UCDB geometries must be in a projected CRS to buffer in metres")
    check_city_ids(city_ids, geometries["ucdb_id"])
    frame = city_ids[["city_id", "ucdb_id"]].merge(
        geometries, on="ucdb_id", how="inner", validate="one_to_one"
    )
    repaired, repairs = zip(
        *(
            repair_geometry(geometry, ucdb_id)
            for geometry, ucdb_id in zip(frame["geometry"], frame["ucdb_id"], strict=True)
        ),
        strict=True,
    )
    core = gpd.GeoDataFrame(
        frame[["city_id", "ucdb_id"]].assign(kind=BOUNDARY_KINDS[0], repair=list(repairs)),
        geometry=list(repaired),
        crs=geometries.crs,
    )
    ring = core.assign(kind=BOUNDARY_KINDS[1], geometry=core.geometry.buffer(BUFFER_METRES))
    boundaries = pd.concat([core, ring], ignore_index=True).to_crs("EPSG:4326")
    if boundaries.geometry.is_empty.any() or not boundaries.geometry.is_valid.all():
        raise RegisterError("a boundary is empty or invalid after reprojection")
    if not boundaries.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise RegisterError("a boundary is not a polygon")
    return boundaries[BOUNDARY_COLUMNS].sort_values(["ucdb_id", "kind"]).reset_index(drop=True)
