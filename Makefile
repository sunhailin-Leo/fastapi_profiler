.PHONY: help install lint test typecheck build publish clean all

# Default target
help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  install    Install all dependencies (including dev group)"
	@echo "  lint       Run ruff and flake8 linters"
	@echo "  typecheck  Run ty type checker"
	@echo "  test       Run pytest with coverage"
	@echo "  check      Run lint + typecheck + test"
	@echo "  build      Build the distribution packages"
	@echo "  publish    Build and publish to PyPI via uv"
	@echo "  clean      Remove build artifacts"

install:
	uv sync --group dev

lint:
	uv run ruff check fastapi_profiler test
	uv run flake8 fastapi_profiler test

typecheck:
	uv run ty check fastapi_profiler

test:
	uv run pytest

check: lint typecheck test

build:
	uv build

publish: build
	uv publish

clean:
	rm -rf dist/ build/ *.egg-info .coverage htmlcov/ .pytest_cache/ __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
