# City register

Canonical register of cities, built on GHS-UCDB R2024A, that city-level datasets key on.

This repository is a feedstock containing code that turns upstream data into books on the
[bookshelf](https://github.com/climate-resource/bookshelf).

The two key files for a bookshelf feedstock are:

- `bookshelf.yaml` is the recipe declaring the metadata, versions and required versions.
- `build.py` is the script to process a given version of a dataset into data ready for the bookshelf.

## Getting started

Install the local virtual environment:

```bash
   make virtual-environment
```

Then build one version of the dataset:

```bash
   make run VERSION=v0.1.0
```

`VERSION` picks one entry from `books:` in the recipe.
The result lands in `bundle/v0.1.0` and is then validated.
This does not upload the result, but can be inspected locally.

## Publishing

Each pull request builds a preview for each version declared in `bookshelf.yaml`.
A URL to review the diff between the published versions and the built version are commented to the pull request.
Merging publishes that preview to the bookshelf.

If the recipe names a `bookshelf://` input that only the organisation can read,
CI resolves it with the workflow's own GitHub Actions token rather than a stored secret.
That needs the Bookshelf GitHub App installed on this repository,
otherwise the build works locally but every record job fails in CI.

Everything else about this feedstock lives in the README of
[copier-bookshelf-dataset](https://github.com/climate-resource/copier-bookshelf-dataset).
