"""
This example shows how to enable the built-in Web UI Dashboard and
the stats aggregation API.

After starting the server, visit:
  http://localhost:8080/__profiler__          — HTML dashboard
  http://localhost:8080/__profiler__/stats    — JSON stats API
  POST http://localhost:8080/__profiler__/reset  — Reset all stats
  POST http://localhost:8080/__profiler__/config — Update runtime config

The dashboard shows per-route statistics including request count,
error count, average / p95 / p99 / max duration in milliseconds.
"""
import os
import uvicorn

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_profiler import PyInstrumentProfilerMiddleware

app = FastAPI()
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    enable_dashboard=True,          # Mount the built-in Web UI Dashboard
    dashboard_path="/__profiler__", # URL prefix for the dashboard (default)
    is_print_each_request=True,
    filter_paths=["/__profiler__"], # Exclude dashboard requests from stats
)


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


@app.get("/heavy")
async def heavy_request():
    total = sum(i * i for i in range(100_000))
    return JSONResponse({"result": total})


@app.get("/error")
async def error_request():
    return JSONResponse({"error": "Something went wrong"}, status_code=500)


# Or you can use the console with command "uvicorn" to run this example.
# Command: uvicorn fastapi_stats_dashboard_example:app --host="0.0.0.0" --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
