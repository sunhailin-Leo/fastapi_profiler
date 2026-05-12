"""End-to-end driver for the memray HTTP control plane.

Run order::

    # Terminal 1: server
    uv run python example/fastapi_memory_profiler_example.py

    # Terminal 2: this driver
    uv run python example/fastapi_memory_memray_client_example.py

The script walks through:

    GET  /memray/status           ← confirm memray is available
    POST /memray/start            ← begin a tracking session
    GET  /alloc/leak  (×N)        ← generate real allocations
    GET  /memray/status           ← inspect running session
    POST /memray/stop             ← finalise the .bin capture file
    (prints render commands you can copy/paste)

Requires the optional dependency::

    pip install 'fastapi_profiler[memray]'

memray is unavailable on Windows.  In that case the script exits with a
helpful message instead of failing.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("FASTAPI_PROFILER_BASE", "http://127.0.0.1:8080")
DASHBOARD = os.environ.get("FASTAPI_PROFILER_MEM_PATH", "/__memory_profiler__")
MEMRAY_BASE = f"{BASE_URL}{DASHBOARD}/memray"
ALLOC_URL = f"{BASE_URL}/alloc/leak"

NATIVE = os.environ.get("FASTAPI_PROFILER_MEMRAY_NATIVE", "0") == "1"
WORKLOAD = int(os.environ.get("FASTAPI_PROFILER_MEMRAY_WORKLOAD", "30"))


def _request(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        # Surface the JSON error body from the dashboard so users understand
        # *why* memray refused to start (e.g. unavailable, already running).
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"error": str(exc), "status": exc.code}


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    print(f"[client] target base : {BASE_URL}")
    print(f"[client] dashboard   : {DASHBOARD}")
    print(f"[client] native      : {NATIVE}")
    print(f"[client] workload    : {WORKLOAD} requests")

    try:
        status = _request("GET", f"{MEMRAY_BASE}/status")
    except urllib.error.URLError as exc:
        print(f"[client] cannot reach server: {exc}")
        print("[client] start the server first: "
              "`uv run python example/fastapi_memory_profiler_example.py`")
        return 2

    section("1. memray availability check")
    print(json.dumps(status, indent=2))
    if not status.get("available"):
        print()
        print("⚠️  memray is not available on this host. "
              "Install it (Linux/macOS only):")
        print("    pip install 'fastapi_profiler[memray]'")
        return 0

    section("2. start memray session")
    start = _request(
        "POST",
        f"{MEMRAY_BASE}/start",
        {"native": NATIVE, "follow_fork": False},
    )
    print(json.dumps(start, indent=2))
    if not start.get("running"):
        print("⚠️  failed to start memray; aborting.")
        return 1
    output_path = start["output_path"]

    section(f"3. generate workload — {WORKLOAD} /alloc/leak calls")
    for _ in range(WORKLOAD):
        urllib.request.urlopen(ALLOC_URL, timeout=5).read()
    print("done.")

    section("4. inspect running session")
    print(json.dumps(_request("GET", f"{MEMRAY_BASE}/status"), indent=2))

    section("5. stop memray session")
    stop = _request("POST", f"{MEMRAY_BASE}/stop")
    print(json.dumps(stop, indent=2))

    section("6. inspect the captured .bin file")
    if output_path and os.path.isfile(output_path):
        size = os.path.getsize(output_path)
        print(f"  path   : {output_path}")
        print(f"  size   : {size:,} bytes ({size / 1024:.1f} KB)")
    else:
        print(f"  path   : {output_path}  (file missing on this host — "
              "client and server may live on different machines)")

    print()
    print("Render the capture with the memray CLI:")
    print(f"  memray flamegraph {output_path}")
    print(f"  memray tree       {output_path}")
    print(f"  memray stats      {output_path}")
    print()
    print("✅ memray lifecycle finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
