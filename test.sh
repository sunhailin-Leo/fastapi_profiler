#!/usr/bin/env bash
set -e

echo "==> Lint with ruff..."
uv run ruff check fastapi_profiler test

echo "==> Lint with flake8..."
uv run flake8 fastapi_profiler test

echo "==> Type check with ty..."
uv run ty check fastapi_profiler

echo "==> Run tests..."
uv run pytest