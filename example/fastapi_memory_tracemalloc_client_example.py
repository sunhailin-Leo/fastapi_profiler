"""End-to-end driver for the tracemalloc HTTP control plane.

This is a *client* script: it expects a running server that mounts
:class:`fastapi_profiler.MemoryProfilerMiddleware` (e.g. start
``example/fastapi_memory_profiler_example.py`` first), then walks through
the full lifecycle:

    /tracemalloc/start
        ↓
    a few /alloc/leak calls            ← grow the heap a bit
        ↓
    /tracemalloc/snapshot              ← snapshot A
        ↓
    a few more /alloc/leak calls       ← grow some more
        ↓
    /tracemalloc/snapshot              ← snapshot B
        ↓
    /tracemalloc/compare               ← print top growers
        ↓
    /tracemalloc/snapshots             ← list all .snap files on disk
        ↓
    /tracemalloc/stop

It uses only the standard library (urllib + json) so it runs anywhere
without extra dependencies.

Usage::

    # Terminal 1: start the server
    uv run python example/fastapi_memory_profiler_example.py

    # Terminal 2: drive it
    uv run python example/fastapi_memory_tracemalloc_client_example.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("FASTAPI_PROFILER_BASE", "http://127.0.0.1:8080")
DASHBOARD = os.environ.get("FASTAPI_PROFILER_MEM_PATH", "/__memory_profiler__")
TRACEMALLOC_BASE = f"{BASE_URL}{DASHBOARD}/tracemalloc"
ALLOC_URL = f"{BASE_URL}/alloc/leak"


def _request(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
        if not body:
            return {}
        return json.loads(body)


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def drive_workload(times: int) -> None:
    for _ in range(times):
        _request("GET", ALLOC_URL)


def main() -> int:
    print(f"[client] target base : {BASE_URL}")
    print(f"[client] dashboard   : {DASHBOARD}")

    try:
        _request("GET", f"{TRACEMALLOC_BASE}/status")
    except urllib.error.URLError as exc:
        print(f"[client] cannot reach server: {exc}")
        print("[client] start the server first: "
              "`uv run python example/fastapi_memory_profiler_example.py`")
        return 2

    section("1. start tracemalloc (frames=25)")
    print(json.dumps(
        _request("POST", f"{TRACEMALLOC_BASE}/start", {"frames": 25}),
        indent=2,
    ))

    section("2. workload round 1 — 5 leak calls")
    drive_workload(5)
    print("done.")

    section("3. snapshot A")
    snap_a = _request("POST", f"{TRACEMALLOC_BASE}/snapshot", {"top": 10})
    print(json.dumps(snap_a, indent=2))

    section("4. workload round 2 — 10 leak calls")
    drive_workload(10)
    print("done.")
    # Give tracemalloc a tick to register the latest allocations.
    time.sleep(0.05)

    section("5. snapshot B")
    snap_b = _request("POST", f"{TRACEMALLOC_BASE}/snapshot", {"top": 10})
    print(json.dumps(snap_b, indent=2))

    section("6. compare A → B (top growers)")
    diff = _request("POST", f"{TRACEMALLOC_BASE}/compare", {"top": 10})
    print(json.dumps(diff, indent=2))

    section("7. list all snapshots on disk")
    listing = _request("GET", f"{TRACEMALLOC_BASE}/snapshots")
    print(json.dumps(listing, indent=2))
    for item in listing.get("snapshots", []):
        path = item.get("file_path")
        if path and os.path.isfile(path):
            size = os.path.getsize(path)
            print(f"  - {path}  ({size:,} bytes)")

    section("8. stop tracemalloc")
    print(json.dumps(_request("POST", f"{TRACEMALLOC_BASE}/stop"), indent=2))

    print()
    print("✅ tracemalloc lifecycle finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
