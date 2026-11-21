.PHONY: demo check

demo:
	uv run demo

# Run `make demo` once first: several checks read the database the demo builds.
check:
	uv run --extra fabric pytest tests -q -m "not network"
