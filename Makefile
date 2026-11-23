.PHONY: demo check gate site build-fabric

# `make demo MONTHS=2024-01..2024-06` lands other months, a list or a range as `--months` takes it;
# without MONTHS the pinned default months apply. Months the source marks as large (hundreds of MB
# each) also need `ALLOW_LARGE=1`, which passes `--allow-large`; only the value 1 turns it on.
MONTHS ?=
ALLOW_LARGE ?=

demo:
	uv run demo $(if $(MONTHS),--months $(MONTHS)) $(if $(filter 1,$(ALLOW_LARGE)),--allow-large)

# Run `make demo` once first: several checks read the database the demo builds.
check:
	uv run --extra fabric pytest tests -q -m "not network"

# The pull request checks, exactly as the GitHub workflow runs them. Builds its own database
# under target/gate/ from an invented fixture, so it needs no download and leaves the demo
# database alone.
gate:
	uv run --locked --extra fabric gate

# Assembles site/ (the root page and the dbt docs) from the static docs the demo writes.
# Run `make demo` first.
site:
	uv run site

# Builds the project on a Fabric Lakehouse from this machine or CI. Needs the FABRIC_* variables
# (FABRIC_WORKSPACE_ID, FABRIC_LAKEHOUSE_ID, FABRIC_LAKEHOUSE), `az login` and one schema-enabled
# Lakehouse. It runs dbt deps, then dbt build on the fabric target, and stops at the first failure.
build-fabric:
	uv run --locked --extra fabric build-fabric
