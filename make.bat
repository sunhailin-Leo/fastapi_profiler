cd test
pytest . --cov=fastapi_profiler --cov=test && cd .. && flake8 --exclude build --max-line-length 89 --ignore=F401
