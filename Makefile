.PHONY: help install install-dev test lint format type-check pre-commit clean setup-dev

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install production dependencies
	pip install -r requirements.txt

install-dev:  ## Install development dependencies
	pip install -r requirements-dev.txt

setup-dev:  ## Setup development environment
	pip install -r requirements-dev.txt
	pre-commit install

test:  ## Run tests
	pytest

test-cov:  ## Run tests with coverage
	pytest --cov=alfen_driver --cov-report=html --cov-report=term

lint:  ## Run linting with ruff
	ruff check .

format:  ## Format code with black and ruff
	black .
	ruff check --fix .

type-check:  ## Run type checking with mypy
	mypy alfen_driver/

security:  ## Run security scanning with bandit
	bandit -r alfen_driver/ -f json -o bandit-report.json || bandit -r alfen_driver/

pre-commit:  ## Run pre-commit hooks
	pre-commit run --all-files

clean:  ## Clean up build artifacts and cache
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete

all:  ## Run all quality checks
	$(MAKE) format
	$(MAKE) lint
	$(MAKE) type-check
	$(MAKE) security
	$(MAKE) test
