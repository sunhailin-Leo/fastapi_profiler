"""
This example shows how to use the runtime enable/disable toggle.

You can start the server with profiling disabled and enable it on demand
via the dashboard /config API — no restart required.

Try the following after starting the server:

  # Check current config
  curl http://localhost:8080/__profiler__/stats

  # Disable profiling at runtime
  curl -X POST http://localhost:8080/__profiler__/config \
       -H "Content-Type: application/json" \
       -d '{"enabled": false}'

  # Re-enable profiling and lower the sample rate
  curl -X POST http://localhost:8080/__profiler__/config \
       -H "Content-Type: application/json" \
       -d '{"enabled": true, "sample_rate": 0.5}'

  # Raise the slow-request threshold to 200 ms
  curl -X POST http://localhost:8080/__profiler__/config \
       -H "Content-Type: application/json" \
       -d '{"slow_request_threshold_ms": 200}'
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
    enabled=True,                   # Start with profiling enabled (default)
    enable_dashboard=True,          # Expose /config API via the dashboard
    dashboard_path="/__profiler__",
    profiler_sample_rate=1.0,
    slow_request_threshold_ms=0,    # Profile every request regardless of duration
    filter_paths=["/__profiler__"],
    is_print_each_request=True,
)


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


# Or you can use the console with command "uvicorn" to run this example.
# Command: uvicorn fastapi_runtime_toggle_example:app --host="0.0.0.0" --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
