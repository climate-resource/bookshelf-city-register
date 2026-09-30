# %% [markdown]
# # City register
#
# One stable `city_id` per GHS-UCDB R2024A urban centre,
# with crosswalks from the city lists other providers publish.
#
# The ids live in `data/city_ids.csv`, so a rebuild never renames a city.
# This notebook joins them to the UCDB attributes and refuses anything that breaks an id rule.

# %%
import bookshelf

import register

# %%
build = bookshelf.setup()

# %% [markdown]
# # Fetch

# %%
source = build.use("ucdb")
ucdb = register.read_ucdb(source.path)
ucdb.head()

# %% [markdown]
# # Process

# %%
crosswalks = register.read_csv(register.CROSSWALK_URBCLIM)[register.CROSSWALK_COLUMNS]
city_ids = register.read_csv(register.CITY_IDS)
cities = register.build_cities(
    ucdb,
    city_ids,
    register.read_csv(register.ISO3),
    register.read_csv(register.ROSTER_IDS),
    crosswalks,
)
register.check_crosswalks(crosswalks, city_ids)
crosswalks = crosswalks.sort_values(["provider", "city_id"]).reset_index(drop=True)
cities.head()

# %% [markdown]
# # Publish
#
# `cities` takes the book's UCDB citation. The UrbClim crosswalk also credits the VITO city list.

# %%
URBCLIM_CITATION = (
    "Souverijns, N., Lauwaet, D., Lejeune, Q., Kropf, C. M., Yeung, K. L., Nath, S. "
    "and Schleussner, C. F. (2024). "
    "100m climate and heat stress information up to 2100 for 142 cities around the globe. "
    "Zenodo. doi:10.5281/zenodo.13361538"
)

# %%
build.book.write(
    "cities",
    cities,
    type="tabular",
    used=[source],
    description="One row per GHS-UCDB R2024A urban centre, with its WGS84 centroid.",
    doi="10.2905/1a338be6-7eaf-480c-9664-3a8ade88cbcd",
)
build.book.write(
    "crosswalks",
    crosswalks,
    type="tabular",
    description="Provider city ids mapped to city_id, with how each match was made.",
    citation=URBCLIM_CITATION,
    doi="10.5281/zenodo.13361538",
)
build.book.publish()
