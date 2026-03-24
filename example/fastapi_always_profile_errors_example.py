"""
This example shows how to use always_profile_errors to automatically
capture profiling data for every 5xx error response, regardless of
the sampling rate or slow-request threshold.

This is useful for diagnosing performance regressions that only appear
under error conditions.
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
    profiler_sample_rate=0.0,       # Normally profile nothing...
    always_profile_errors=True,     # ...but always profile 5xx errors
    is_print_each_request=True,
)


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


@app.get("/error")
async def error_request():
    # This 500 response will always be profiled even with sample_rate=0.0
    return JSONResponse({"error": "Something went wrong"}, status_code=500)


# Or you can use the console with command "uvicorn" to run this example.
# Command: uvicorn fastapi_always_profile_errors_example:app --host="0.0.0.0" --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
