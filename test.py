import os

os.system(
    "nosetests --with-coverage --cover-package fastapi_profiler --cover-package test"
)
os.system("flake8 --exclude build --max-line-length 89 --ignore=F401")
