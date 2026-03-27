.PHONY: help dev test lint format typecheck migrate docker clean evaluate ui

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

dev: ## Start Docker stack
	docker-compose up -d

ui: ## Run Streamlit chat UI (set RAG_API_BASE if API is not on localhost:8000)
	RAG_API_BASE=$${RAG_API_BASE:-http://localhost:8000/api/v1} streamlit run frontend/app.py

docker: ## Build and start Docker containers
	docker-compose up --build -d

test: ## Run tests
	python -m pytest -q

lint: ## Run lint and format checks
	python -m ruff check .
	python -m ruff format --check .

format: ## Auto-format code
	python -m ruff format .

typecheck: ## Run mypy type checking
	python -m mypy app/ --ignore-missing-imports

migrate: ## Apply Alembic migrations
	python -m alembic upgrade head

evaluate: ## Run evaluation pipeline (offline retrieval + guardrails; optional --with-llm)
	PYTHONPATH=. python scripts/evaluate.py

clean: ## Remove local caches and test artifacts
	rm -rf .mypy_cache .pytest_cache .ruff_cache htmlcov .coverage

