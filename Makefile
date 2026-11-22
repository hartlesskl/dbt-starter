.PHONY: demo check gate site

demo:
	uv run demo

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
