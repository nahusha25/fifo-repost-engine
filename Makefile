.PHONY: test lint typecheck demo

test:
	pytest -q

lint:
	ruff check .

typecheck:
	mypy src

demo:
	python -m stock_repost.cli
