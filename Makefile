# Makefile to help automate key steps

.DEFAULT_GOAL := help

# A bundle holds one book, so the version selects both what is recorded and where it lands.
# CI records every version the recipe declares. Locally you pick one.
VERSION ?= v0.1.0
BUNDLE := bundle/$(VERSION)

# A helper script to get short descriptions of each target in the Makefile
define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([\$$\(\)a-zA-Z_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("%-30s %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT

.PHONY: help
help:  ## print short description of each target
	@python3 -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)

.PHONY: checks
checks:  ## run all the linting checks of the codebase
	@echo "=== pre-commit ==="; uvx pre-commit run --all-files || echo "--- pre-commit failed ---" >&2; \
		echo "======"

.PHONY: ruff-fixes
ruff-fixes:  ## fix the code using ruff
    # format before and after checking so that the formatted stuff is checked and
    # the fixed stuff is formatted
	uvx ruff@0.15.22 format
	uvx ruff@0.15.22 check --fix
	uvx ruff@0.15.22 format

.PHONY: test
test:  ## run the tests
	uv run pytest

.PHONY: mint
mint:  ## append city_ids.csv rows for UCDB centres without one, from UCDB=<path to the zip>
	@[ -n "$(UCDB)" ] || { echo "set UCDB to the UCDB zip the recipe pins" >&2; exit 1; }
	uv run python scripts/mint.py --ucdb $(UCDB)

.PHONY: virtual-environment
virtual-environment:  ## update virtual environment, create a new one if it doesn't already exist
	uv sync
	uvx pre-commit install

.PHONY: initial-setup
initial-setup:  ## prepare a freshly generated feedstock, safe to re-run
    # Recording derives provenance from git, so it needs an origin remote and a commit
    # to point at. CI syncs with `--locked`, so it needs a lock file alongside them.
	@[ -d .git ] || git init
	@git remote get-url origin >/dev/null 2>&1 || git remote add origin https://github.com/climate-resource/bookshelf-city-register
	@[ -f uv.lock ] || uv lock
	@git add .
	@git rev-parse HEAD >/dev/null 2>&1 \
		|| git -c user.name="Jared Lewis" -c user.email="jared.lewis@climate-resource.com" \
		commit -q -m "Initial scaffold from copier-bookshelf-dataset"

run:  ## Record and validate one book, selected with VERSION=vX.Y.Z
    # --force because rebuilding is the point of this target.
    # A bare `bookshelf record` refuses to replace a bundle that is under review.
	uv run bookshelf record --force --version $(VERSION) --bundle $(BUNDLE)
	uv run bookshelf validate $(BUNDLE)
