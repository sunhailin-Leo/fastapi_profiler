"""
This example shows how to enable structured JSON logging for request
profiling output.

With log_format="json", each request log line is emitted as a JSON
object, making it easy to ingest into log aggregation platforms such
as ELK Stack, Datadog, or Splunk.

Example log output:
  {"logger": "fastapi_profiler", "method": "GET", "path": "/test",
   "duration_ms": 1.234, "status_code": 200}
"""
import logging
import os
import uvicorn

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_profiler import PyInstrumentProfilerMiddleware

# Configure the root logger so the JSON lines are visible in the console.
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI()
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    log_format="json",              # Emit structured JSON log lines
    is_print_each_request=True,
)


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


# Or you can use the console with command "uvicorn" to run this example.
# Command: uvicorn fastapi_json_logging_example:app --host="0.0.0.0" --port=8080
if __name__ == "__main__":
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
