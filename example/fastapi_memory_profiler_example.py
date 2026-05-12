"""Example: drive tracemalloc and (optional) memray via HTTP control plane.

This example provides several allocation scenarios to make profiler output
meaningful:

- ``GET /alloc/small``   — small per-request allocation (~64 KB)
- ``GET /alloc/big``     — single large allocation (~10 MB)
- ``GET /alloc/leak``    — accumulating allocations (simulated leak)
- ``POST /alloc/clear``  — release everything to validate ``compare`` deltas
- ``GET /alloc/stats``   — quick view of current bucket state

Run the server::

    uv run python example/fastapi_memory_profiler_example.py

Then exercise the control plane::

    # ── tracemalloc ────────────────────────────────────────────────
    curl -XPOST localhost:8080/__memory_profiler__/tracemalloc/start
    curl -XGET  localhost:8080/alloc/leak                # generate growth
    curl -XGET  localhost:8080/alloc/leak
    curl -XPOST localhost:8080/__memory_profiler__/tracemalloc/snapshot
    curl -XGET  localhost:8080/alloc/leak
    curl -XPOST localhost:8080/__memory_profiler__/tracemalloc/snapshot
    curl -XPOST localhost:8080/__memory_profiler__/tracemalloc/compare
    curl -XPOST localhost:8080/__memory_profiler__/tracemalloc/stop

    # ── memray (requires `pip install fastapi_profiler[memray]`,
    #            Linux/macOS only) ─────────────────────────────────
    curl -XPOST localhost:8080/__memory_profiler__/memray/start \\
         -H 'Content-Type: application/json' \\
         -d '{"native": false}'
    curl -XGET  localhost:8080/alloc/leak
    curl -XPOST localhost:8080/__memory_profiler__/memray/stop
    # → render the resulting .bin file with `memray flamegraph <path>`
"""

import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_profiler import MemoryProfilerMiddleware

app = FastAPI()
app.add_middleware(
    MemoryProfilerMiddleware,
    server_app=app,
    snapshot_dir="./mem-snapshots",
    autostart_tracemalloc=False,
)

# In-memory buckets used to drive allocation patterns.
_LEAK_BUCKET: list = []
_BIG_BUCKET: list = []


@app.get("/alloc/small")
async def alloc_small():
    """Allocate ~64 KB per call (1000 strings of 64 bytes)."""
    payload = ["x" * 64 for _ in range(1000)]
    return JSONResponse({
        "retMsg": "small allocation done",
        "approx_bytes": 64 * 1000,
        "items": len(payload),
    })


@app.get("/alloc/big")
async def alloc_big():
    """Allocate ~10 MB in a single call and keep it referenced."""
    chunk = bytearray(10 * 1024 * 1024)
    _BIG_BUCKET.append(chunk)
    return JSONResponse({
        "retMsg": "big allocation done",
        "approx_bytes": len(chunk),
        "big_bucket_size": len(_BIG_BUCKET),
    })


@app.get("/alloc/leak")
async def alloc_leak():
    """Accumulate ~640 KB per call to simulate a slow leak."""
    _LEAK_BUCKET.append(["x" * 64 for _ in range(10000)])
    return JSONResponse({
        "retMsg": "leak step recorded",
        "leak_bucket_size": len(_LEAK_BUCKET),
    })


@app.post("/alloc/clear")
async def alloc_clear():
    """Drop both buckets so a follow-up ``compare`` shows the delta."""
    leak_dropped = len(_LEAK_BUCKET)
    big_dropped = len(_BIG_BUCKET)
    _LEAK_BUCKET.clear()
    _BIG_BUCKET.clear()
    return JSONResponse({
        "retMsg": "cleared",
        "leak_dropped": leak_dropped,
        "big_dropped": big_dropped,
    })


@app.get("/alloc/stats")
async def alloc_stats():
    return JSONResponse({
        "leak_bucket_size": len(_LEAK_BUCKET),
        "big_bucket_size": len(_BIG_BUCKET),
    })


# Or you can use the console with command "uvicorn" to run this example.
# Command:
#   uvicorn fastapi_memory_profiler_example:app --host=0.0.0.0 --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
