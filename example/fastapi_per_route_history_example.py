"""
This example shows how to use max_profiles_per_route to keep a rolling
history of the last N profile records per route in memory.

The per-route history is accessible via the /stats API when the dashboard
is enabled.  Each ProfileRecord contains the route path, HTTP method,
duration in milliseconds, HTTP status code, and an optional profile output
string captured at request time.
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
    enable_dashboard=True,
    dashboard_path="/__profiler__",
    max_profiles_per_route=20,      # Keep the last 20 profiles per route
    filter_paths=["/__profiler__"],
    is_print_each_request=True,
)


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


@app.get("/compute")
async def compute_request():
    result = sum(i ** 2 for i in range(50_000))
    return JSONResponse({"result": result})


# Or you can use the console with command "uvicorn" to run this example.
# Command: uvicorn fastapi_per_route_history_example:app --host="0.0.0.0" --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
