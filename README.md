# City register

One stable `city_id` for every GHS-UCDB R2024A urban centre,
with crosswalks from the city lists other providers publish.
City-level datasets key on `city_id`, so they join without re-deriving city identity.

This repository is a feedstock containing code that turns upstream data into books on the
[bookshelf](https://github.com/climate-resource/bookshelf).
The book is `city-register`, public and CC-BY-4.0.

## What the book holds

`cities` has one row per UCDB urban centre.

| Column              | Meaning                                                                  |
| ------------------- | ------------------------------------------------------------------------ |
| `city_id`           | The register's id. See [id rules](#id-rules).                            |
| `ucdb_id`           | UCDB `ID_UC_G0`.                                                         |
| `name`              | UCDB main name, empty for 14 centres.                                    |
| `alternative_names` | UCDB name list, `; ` separated, as UCDB gives it.                        |
| `iso3`              | ISO 3166-1 alpha-3, from `data/iso3.csv`.                                |
| `country`           | UCDB country name (`GC_CNT_GAD_2025`).                                   |
| `country_un`        | UN country name (`GC_CNT_UNN_2025`).                                     |
| `lon`, `lat`        | UCDB centroid (`UC_centroids`), reprojected from Mollweide to WGS84.     |
| `area_km2`          | Urban centre area in 2025 (`GC_UCA_KM2_2025`).                           |
| `population_2025`   | Total population in 2025 (`GC_POP_TOT_2025`).                            |
| `income_group`      | Income group (`GC_DEV_WIG_2025`).                                        |
| `region`            | Region (`GC_DEV_USR_2025`).                                              |
| `name_quality`      | UCDB name score, `High`, `Medium` or `Low` (`GC_PLS_SCR_2025`).          |
| `first_year`        | `GC_UCB_YOB_2025`, an epoch year from 1975.                              |
| `last_year`         | `GC_UCB_YOD_2025`, 2025 or 2030.                                         |
| `capital`           | UCDB capital flag (`GC_UCM_CAP`).                                        |

`crosswalks` maps provider city ids to `city_id`.
Its columns are `city_id, provider, provider_id, provider_name, match_type, alias_rationale`.
The first provider is `urbclim`, the 142 VITO UrbClim cities served in the climate risk atlas.

## Id rules

Ids are frozen in `data/city_ids.csv`.
A rebuild reads them and never re-derives them, so an id never changes once published.
`minted_from` records which rule minted each one:

- `k15-roster`: the 142 UrbClim cities keep the ids riskatlas already serves.
  `data/roster_ids.csv` pins each to its UCDB centre, and the build refuses any change.
- `ucdb-name`: `<iso3>-<slug>`, where the slug is the main name in NFKD ASCII, lower case,
  with every run of other characters replaced by `_`.
  A slug that is taken gets `-2`, `-3` and so on, in ascending `ucdb_id` order.
- `ucdb-id`: `<iso3>-uc<ucdb_id>`, for a centre with no name or with a slug holding no Latin letter.

Every id matches `^[a-z0-9][a-z0-9._-]{0,199}$`.
The build refuses a UCDB centre without an id, an id whose centre UCDB no longer has,
a duplicate, a malformed id, and a country missing from `data/iso3.csv`.

`data/iso3.csv` maps UCDB country names to ISO3 through `pycountry`,
by the UCDB name, then the UN name, then four explicit overrides.
Northern Cyprus takes `CYP`, following its UN name.

`scripts/seed.py` wrote the first `data/` on fressnapf from the riskatlas roster and the VITO urban areas.
It overwrites every CSV, so it is kept as a record and not rerun.

## Adding a provider crosswalk

1. Match the provider's cities to `city_id` in a script under `scripts/`,
   with explicit rules and a reviewed alias table, never fuzzy matching.
2. Write `data/crosswalk-<provider>.csv` with the crosswalk columns above.
   `match_type` names the rule, and `alias_rationale` explains each alias.
3. Concatenate it into `crosswalks` in `build.py`.
   The build refuses a `city_id` outside the register and a provider id listed twice.

## Moving to a new UCDB release

UCDB ids are not stable across releases, so a new release needs its centres matched to the old ones.

1. Add a book for the new release in `bookshelf.yaml`, pinning its zip by `uri:` and `sha256:`.
2. Match each old centre to the new release, spatially and within the same country,
   and rewrite `ucdb_id` in `data/city_ids.csv` while keeping `city_id`.
3. Run `make mint UCDB=<path to the new zip>` to mint ids for centres that are new.
4. Decide what happens to centres the release drops before the build will pass,
   because a retired id must never be reused.

## Getting started

Install the local virtual environment:

```bash
make virtual-environment
```

Then build and validate the book:

```bash
make run VERSION=v2024a.1.1
make test
```

The UCDB zip is fetched from the JRC and checked against the recipe's `sha256`.
The result lands in `bundle/v2024a.1.1` and can be inspected locally.

## Publishing

Each pull request builds a preview for each version declared in `bookshelf.yaml`.
A URL to review the diff between the published versions and the built version is commented on the pull request.
Merging publishes that preview to the bookshelf.

Everything else about this feedstock lives in the README of
[copier-bookshelf-dataset](https://github.com/climate-resource/copier-bookshelf-dataset).
