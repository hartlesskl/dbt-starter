.PHONY: demo check

demo:
	uv run demo

check:
	uv run --extra fabric pytest tests -q -m "not network"
