# %% [markdown]
# # City register
#
# Canonical register of cities, built on GHS-UCDB R2024A, that city-level datasets key on.
#
# The recipe in `bookshelf.yaml` names the version, the licence, the discovery metadata
# and the inputs.
#
# This file contains the code that processes the data into a form used by the bookshelf.

# %%
import bookshelf
import pandas as pd

# %%
build = bookshelf.setup()

# %% [markdown]
# # Fetch
#
# `build.use` resolves a resource named in the recipe,
# and registers them as an input of this build.

# %%
raw = build.use("raw")
raw_data = pd.read_csv(raw.path)
raw_data.head()

# %% [markdown]
# # Process
#
# TODO: Replace this with the real transform.

# %%
# A timeseries is stored wide, one column per year, so the long input is pivoted.
processed_data = (
    raw_data.assign(value=raw_data["value"] * 2)
    .pivot(index=["region", "dataset"], columns="year", values="value")
    .reset_index()
)
processed_data.columns = [str(column) for column in processed_data.columns]

# %% [markdown]
# # Publish
#

# %%
build.book.write("data", processed_data, type="timeseries", used=[raw])
build.book.publish()
