"""Customise the memory dashboard path and discuss access protection.

The default mount path is ``/__memory_profiler__``.  In real deployments
you typically want either a hard-to-guess path or to gate access at the
reverse-proxy layer — the middleware itself ships **without** built-in
authentication on purpose, to keep its footprint tiny and to avoid
imposing an opinion on auth backends.

This example shows three things:

1. Mounting the memory dashboard on a custom prefix
   (``/_internal/mem-debug``).
2. Persisting captures to a non-default directory.
3. Combining with ``PyInstrumentProfilerMiddleware`` and using
   ``filter_paths`` to keep the memory dashboard out of CPU stats.

Recommended deployment patterns
-------------------------------
- **Bind to localhost only** when running behind a reverse proxy and
  let the proxy expose only the public routes.
- **Block the dashboard prefix at the proxy layer** (nginx ``location``
  / ALB rule) for everyone except a trusted CIDR, e.g.::

      location /_internal/ {
          allow 10.0.0.0/8;
          deny  all;
          proxy_pass http://app;
      }

- **Add an auth dependency in front of the mount** by wrapping
  ``MemoryProfilerMiddleware`` behind your own router/middleware that
  enforces a shared secret header.

Usage::

    uv run python example/fastapi_memory_custom_path_example.py

    curl http://localhost:8080/_internal/mem-debug/status
    curl -XPOST http://localhost:8080/_internal/mem-debug/tracemalloc/start
    curl http://localhost:8080/alloc/leak
    curl -XPOST http://localhost:8080/_internal/mem-debug/tracemalloc/snapshot
    curl -XPOST http://localhost:8080/_internal/mem-debug/tracemalloc/stop
"""

import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_profiler import (
    MemoryProfilerMiddleware,
    PyInstrumentProfilerMiddleware,
)

CUSTOM_MEM_PATH = "/_internal/mem-debug"
CUSTOM_SNAPSHOT_DIR = "./var/mem-snapshots"

app = FastAPI()

app.add_middleware(
    MemoryProfilerMiddleware,
    server_app=app,
    memory_dashboard_path=CUSTOM_MEM_PATH,
    snapshot_dir=CUSTOM_SNAPSHOT_DIR,
    autostart_tracemalloc=False,
)

# Hide both dashboards from the CPU profiler stats.
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    enable_dashboard=True,
    dashboard_path="/__profiler__",
    profiler_sample_rate=1.0,
    is_print_each_request=False,
    filter_paths=["/__profiler__", CUSTOM_MEM_PATH],
    enabled=True,
)

_LEAK_BUCKET: list = []


@app.get("/")
async def index():
    return JSONResponse({
        "retMsg": "ok",
        "memory_dashboard": CUSTOM_MEM_PATH,
        "snapshot_dir": os.path.abspath(CUSTOM_SNAPSHOT_DIR),
    })


@app.get("/alloc/leak")
async def alloc_leak():
    _LEAK_BUCKET.append(["x" * 64 for _ in range(10000)])
    return JSONResponse({
        "retMsg": "leak step recorded",
        "leak_bucket_size": len(_LEAK_BUCKET),
    })


# Or you can use the console with command "uvicorn" to run this example.
# Command:
#   uvicorn fastapi_memory_custom_path_example:app \
#       --host=127.0.0.1 --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    # Bind to localhost-only by default — pair with a reverse proxy
    # to expose only the public routes outside the host.
    uvicorn.run(app=f"{app_name}:app", host="127.0.0.1", port=8080, workers=1)
