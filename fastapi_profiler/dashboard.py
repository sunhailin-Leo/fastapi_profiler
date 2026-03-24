"""Built-in Web UI Dashboard and JSON API for fastapi_profiler.

Provides a lightweight Starlette Router that can be mounted on the host
application to expose profiling statistics and runtime configuration.
"""

from typing import Callable

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, Router

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FastAPI Profiler Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0f2f5;color:#333;padding:24px}
.container{max-width:1200px;margin:0 auto}
h1{color:#1a1a2e;margin-bottom:20px;font-size:24px}
.status-bar{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap}
.card{background:#fff;padding:14px 18px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.08);min-width:140px}
.card-label{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.card-value{font-size:20px;font-weight:700}
.enabled{color:#27ae60}.disabled{color:#e74c3c}
table{width:100%;background:#fff;border-collapse:collapse;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}
thead{background:#2c3e50;color:#fff}
th,td{padding:11px 14px;text-align:left;font-size:13px}
th{font-weight:600;text-transform:uppercase;letter-spacing:.4px}
tbody tr:hover{background:#f7f9fc}
.num{text-align:right;font-family:monospace}
.actions{margin-top:18px;display:flex;gap:10px}
.btn{padding:9px 18px;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;transition:opacity .15s}
.btn-danger{background:#e74c3c;color:#fff}.btn-danger:hover{opacity:.85}
.btn-primary{background:#3498db;color:#fff}.btn-primary:hover{opacity:.85}
.empty{text-align:center;padding:32px;color:#aaa}
</style>
</head>
<body>
<div class="container">
  <h1>⚡ FastAPI Profiler Dashboard</h1>
  <div class="status-bar">
    <div class="card"><div class="card-label">Status</div><div class="card-value" id="status">…</div></div>
    <div class="card"><div class="card-label">Sample Rate</div><div class="card-value" id="sample-rate">…</div></div>
    <div class="card"><div class="card-label">Slow Threshold</div><div class="card-value" id="threshold">…</div></div>
    <div class="card"><div class="card-label">Total Routes</div><div class="card-value" id="route-count">…</div></div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Path</th><th>Method</th>
        <th class="num">Requests</th><th class="num">Errors</th>
        <th class="num">Avg (ms)</th><th class="num">P95 (ms)</th>
        <th class="num">P99 (ms)</th><th class="num">Max (ms)</th>
      </tr>
    </thead>
    <tbody id="stats-body"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody>
  </table>
  <div class="actions">
    <button class="btn btn-danger" onclick="resetStats()">Reset Stats</button>
    <button class="btn btn-primary" onclick="loadStats()">Refresh</button>
  </div>
</div>
<script>
const base = window.location.pathname.replace(/\\/$/, '');
async function loadStats() {
  try {
    const r = await fetch(base + '/stats');
    const d = await r.json();
    const statusEl = document.getElementById('status');
    statusEl.textContent = d.enabled ? 'Enabled' : 'Disabled';
    statusEl.className = 'card-value ' + (d.enabled ? 'enabled' : 'disabled');
    document.getElementById('sample-rate').textContent = (d.sample_rate * 100).toFixed(0) + '%';
    document.getElementById('threshold').textContent = d.slow_request_threshold_ms + ' ms';
    document.getElementById('route-count').textContent = d.routes.length;
    const tbody = document.getElementById('stats-body');
    if (!d.routes.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">No data yet</td></tr>';
      return;
    }
    tbody.innerHTML = d.routes.map(r => `<tr>
      <td>${esc(r.path)}</td><td>${esc(r.method)}</td>
      <td class="num">${r.count}</td><td class="num">${r.error_count}</td>
      <td class="num">${r.avg_duration_ms.toFixed(2)}</td>
      <td class="num">${r.p95_duration_ms.toFixed(2)}</td>
      <td class="num">${r.p99_duration_ms.toFixed(2)}</td>
      <td class="num">${r.max_duration_ms.toFixed(2)}</td>
    </tr>`).join('');
  } catch(e) {
    document.getElementById('stats-body').innerHTML = '<tr><td colspan="8" class="empty">Failed to load stats</td></tr>';
  }
}
function esc(s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}
async function resetStats() {
  if (!confirm('Reset all profiler stats?')) return;
  await fetch(base + '/reset', {method:'POST'});
  loadStats();
}
loadStats();
</script>
</body>
</html>"""


def create_dashboard_router(
    stats_collector,
    get_enabled: Callable[[], bool],
    set_enabled: Callable[[bool], None],
    get_config: Callable[[], dict],
    set_config: Callable[[dict], None],
) -> Router:
    """Create and return a Starlette Router with dashboard routes.

    Routes
    ------
    GET  /        HTML Dashboard page
    GET  /stats   JSON statistics API
    POST /reset   Reset all collected stats
    POST /config  Update runtime configuration
    """

    async def get_dashboard(request: Request) -> HTMLResponse:
        return HTMLResponse(content=_DASHBOARD_HTML)

    async def get_stats(request: Request) -> JSONResponse:
        routes = await stats_collector.get_all_stats()
        config = get_config()
        return JSONResponse({
            "enabled": get_enabled(),
            "sample_rate": config.get("sample_rate", 1.0),
            "slow_request_threshold_ms": config.get("slow_request_threshold_ms", 0),
            "routes": routes,
        })

    async def reset_stats(request: Request) -> JSONResponse:
        await stats_collector.reset()
        return JSONResponse({"message": "stats reset"})

    async def update_config(request: Request) -> JSONResponse:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        if "enabled" in data:
            enabled_value = data["enabled"]
            if not isinstance(enabled_value, bool):
                return JSONResponse({"error": "enabled must be a boolean"}, status_code=400)
            set_enabled(enabled_value)

        config_patch: dict = {}
        if "sample_rate" in data:
            try:
                sample_rate = float(data["sample_rate"])
            except (TypeError, ValueError):
                return JSONResponse(
                    {"error": "sample_rate must be a number between 0.0 and 1.0"},
                    status_code=400,
                )
            if not 0.0 <= sample_rate <= 1.0:
                return JSONResponse(
                    {"error": "sample_rate must be a number between 0.0 and 1.0"},
                    status_code=400,
                )
            config_patch["sample_rate"] = sample_rate
        if "slow_request_threshold_ms" in data:
            try:
                slow_threshold = float(data["slow_request_threshold_ms"])
            except (TypeError, ValueError):
                return JSONResponse(
                    {"error": "slow_request_threshold_ms must be a non-negative number"},
                    status_code=400,
                )
            if slow_threshold < 0.0:
                return JSONResponse(
                    {"error": "slow_request_threshold_ms must be a non-negative number"},
                    status_code=400,
                )
            config_patch["slow_request_threshold_ms"] = slow_threshold
        if config_patch:
            set_config(config_patch)

        updated = get_config()
        return JSONResponse({
            "enabled": get_enabled(),
            "sample_rate": updated.get("sample_rate", 1.0),
            "slow_request_threshold_ms": updated.get("slow_request_threshold_ms", 0),
        })

    return Router(routes=[
        Route("/", endpoint=get_dashboard, methods=["GET"]),
        Route("/stats", endpoint=get_stats, methods=["GET"]),
        Route("/reset", endpoint=reset_stats, methods=["POST"]),
        Route("/config", endpoint=update_config, methods=["POST"]),
    ])
