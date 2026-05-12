"""Run CPU and memory profilers side-by-side in the same FastAPI app.

This pattern is what you typically want in production-like environments:

- ``PyInstrumentProfilerMiddleware`` records *per-request* CPU/wall-time
  profiles (sampling-based, low overhead).
- ``MemoryProfilerMiddleware`` exposes an HTTP control plane to drive
  ``tracemalloc`` and ``memray`` *process-globally* on demand.

Both middlewares mount their own dashboards on independent paths and
ignore each other thanks to ``filter_paths``:

- CPU dashboard    : ``/__profiler__``
- Memory dashboard : ``/__memory_profiler__``

Usage::

    uv run python example/fastapi_cpu_and_memory_profiler_example.py

    # CPU dashboard (HTML UI)
    open http://localhost:8080/__profiler__

    # Memory control plane (JSON API)
    curl http://localhost:8080/__memory_profiler__/status
"""

import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_profiler import (
    MemoryProfilerMiddleware,
    PyInstrumentProfilerMiddleware,
)

CPU_DASHBOARD = "/__profiler__"
MEMORY_DASHBOARD = "/__memory_profiler__"

app = FastAPI()

# Memory profiler — mount first so its router is registered before the
# CPU middleware wraps the ASGI stack.  Order does not affect correctness
# (the memory dashboard is mounted on the host app), but keeping it first
# makes the intent explicit.
app.add_middleware(
    MemoryProfilerMiddleware,
    server_app=app,
    memory_dashboard_path=MEMORY_DASHBOARD,
    snapshot_dir="./mem-snapshots",
    autostart_tracemalloc=False,
)

# CPU/wall-time profiler.  ``filter_paths`` excludes both dashboards so
# their internal traffic never appears in the per-route CPU stats.
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    enable_dashboard=True,
    dashboard_path=CPU_DASHBOARD,
    profiler_sample_rate=1.0,
    slow_request_threshold_ms=0,
    is_print_each_request=True,
    filter_paths=[CPU_DASHBOARD, MEMORY_DASHBOARD],
    enabled=True,
)

_LEAK_BUCKET: list = []


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


@app.get("/cpu/heavy")
async def cpu_heavy():
    """Spend ~50 ms of CPU so PyInstrument has something to show."""
    total = 0
    for i in range(200_000):
        total += i * i
    return JSONResponse({"retMsg": "cpu work done", "checksum": total % 9973})


@app.get("/alloc/leak")
async def alloc_leak():
    """Accumulate ~640 KB per call so memory profilers have something to show."""
    _LEAK_BUCKET.append(["x" * 64 for _ in range(10000)])
    return JSONResponse({
        "retMsg": "leak step recorded",
        "leak_bucket_size": len(_LEAK_BUCKET),
    })


@app.post("/alloc/clear")
async def alloc_clear():
    dropped = len(_LEAK_BUCKET)
    _LEAK_BUCKET.clear()
    return JSONResponse({"retMsg": "cleared", "dropped": dropped})


# Or you can use the console with command "uvicorn" to run this example.
# Command:
#   uvicorn fastapi_cpu_and_memory_profiler_example:app \
#       --host=0.0.0.0 --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
