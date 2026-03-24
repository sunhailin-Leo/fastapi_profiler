"""
This example shows how to use profiler_sample_rate to control
the fraction of requests that get profiled.

Setting sample_rate=0.1 means only ~10% of requests will be profiled,
which reduces overhead in high-traffic production environments.
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
    profiler_sample_rate=0.1,   # Profile only ~10% of requests
    is_print_each_request=True,
)


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


@app.get("/heavy")
async def heavy_request():
    # Simulate some work
    total = sum(i * i for i in range(100_000))
    return JSONResponse({"result": total})


# Or you can use the console with command "uvicorn" to run this example.
# Command: uvicorn fastapi_sampling_rate_example:app --host="0.0.0.0" --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
