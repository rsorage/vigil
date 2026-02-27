.DEFAULT_GOAL := help

.PHONY: help test run-hourly run-digest run lint inspect

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

test: ## Run the test suite
	uv run pytest

lint: ## Check code style (ruff)
	uv run ruff check .

inspect: ## Print all active error records to console
	uv run python show_errors.py

run-hourly: ## Collect logs and persist errors (ad-hoc)
	uv run python hourly.py

run-digest: ## Run LLM analysis and generate today's HTML report (ad-hoc)
	uv run python digest.py

run: run-hourly run-digest ## Full ad-hoc run: collect + analyze + report
