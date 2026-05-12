"""Auto-start tracemalloc as soon as the middleware is constructed.

Set ``autostart_tracemalloc=True`` to begin tracing during application
startup *before* any request is served.  This is invaluable when you
suspect a leak that happens during application boot (model loading,
warmup caches, framework initialisation, …).

The HTTP control plane stays available, so you can still call
``/tracemalloc/snapshot`` / ``/tracemalloc/stop`` later to inspect or
turn it off.

Usage::

    uv run python example/fastapi_memory_autostart_example.py

    # Right after startup — already tracing!
    curl http://localhost:8080/__memory_profiler__/tracemalloc/status

    # Capture a baseline snapshot
    curl -XPOST http://localhost:8080/__memory_profiler__/tracemalloc/snapshot

    # Drive the workload
    curl http://localhost:8080/alloc/leak
    curl http://localhost:8080/alloc/leak

    # Diff against the baseline
    curl -XPOST http://localhost:8080/__memory_profiler__/tracemalloc/snapshot
    curl -XPOST http://localhost:8080/__memory_profiler__/tracemalloc/compare
"""

import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_profiler import MemoryProfilerMiddleware

# Allocate something during *module import* — autostart_tracemalloc
# guarantees this allocation is recorded.
_STARTUP_CACHE = [bytearray(1024 * 1024) for _ in range(2)]  # ~2 MB

app = FastAPI()
app.add_middleware(
    MemoryProfilerMiddleware,
    server_app=app,
    snapshot_dir="./mem-snapshots",
    autostart_tracemalloc=True,   # ← tracemalloc.start() runs immediately
    tracemalloc_frames=30,        # deeper tracebacks for startup analysis
)

_LEAK_BUCKET: list = []


@app.on_event("startup")
async def warmup() -> None:
    """Simulate a startup hook that allocates more memory."""
    _STARTUP_CACHE.append(bytearray(512 * 1024))


@app.get("/alloc/leak")
async def alloc_leak():
    _LEAK_BUCKET.append(["x" * 64 for _ in range(10000)])
    return JSONResponse({
        "retMsg": "leak step recorded",
        "leak_bucket_size": len(_LEAK_BUCKET),
    })


@app.get("/startup/cache")
async def startup_cache():
    return JSONResponse({
        "items": len(_STARTUP_CACHE),
        "approx_bytes": sum(len(b) for b in _STARTUP_CACHE),
    })


# Or you can use the console with command "uvicorn" to run this example.
# Command:
#   uvicorn fastapi_memory_autostart_example:app --host=0.0.0.0 --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
