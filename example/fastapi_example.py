import os
import uvicorn

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fastapi_profiler.profiler_middleware import PyInstrumentProfilerMiddleware


app = FastAPI()
app.add_middleware(PyInstrumentProfilerMiddleware)


@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})


# Or you can use the console with command "uvicorn" to run this example.
# Command: uvicorn fastapi_example:app --host="0.0.0.0" --port=8080
if __name__ == '__main__':
    app_name = os.path.basename(__file__).replace(".py", "")
    uvicorn.run(app=f"{app_name}:app", host="0.0.0.0", port=8080, workers=1)
