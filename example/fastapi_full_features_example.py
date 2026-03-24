"""
This example demonstrates all 1.5.0 features together:

  - profiler_sample_rate    : profile 50% of requests
  - always_profile_errors   : always profile 5xx responses
  - log_format="json"       : structured JSON log lines
  - enable_dashboard        : built-in Web UI at /__profiler__
  - max_profiles_per_route  : keep last 10 profiles per route
  - slow_request_threshold_ms: only emit profile for requests > 100 ms
  - enabled                 : start with profiling on; toggle via /config API
  - filter_paths            : exclude dashboard routes from stats

After starting the server, visit:
  http://localhost:8080/__profiler__  — Web UI Dashboard
"""
import logging
import os
import uvicorn

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_profiler import PyInstrumentProfilerMiddleware

logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI()
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    # Sampling & thresholds
    profiler_sample_rate=0.5,           # Profile 50% of requests
    always_profile_errors=True,         # Always profile 5xx errors
    slow_request_threshold_ms=100,      # Only emit profile when > 100 ms
    # Logging
    is_print_each_request=True,
    log_format="json",                  # Structured JSON log output
    # Dashboard & stats
    enable_dashboard=True,
    dashboard_path="/__profiler__",
    max_profiles_per_route=10,          # Keep last 10 profiles per route
    filter_paths=["/__profiler__"],     # Exclude dashboard from stats
    # Runtime toggle
    enabled=True,
)


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


@app.get("/slow")
async def slow_request():
    # Simulate a slow response that exceeds the 100 ms threshold
    import asyncio
    await asyncio.sleep(0.15)
    return JSONResponse({"retMsg": "Slow response"})


@app.get("/error")
async def error_request():
    return JSONResponse({"error": "Internal Server Error"}, status_code=500)


# Or you can use the console with command "uvicorn" to run this example.
# Command: uvicorn fastapi_full_features_example:app --host="0.0.0.0" --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
