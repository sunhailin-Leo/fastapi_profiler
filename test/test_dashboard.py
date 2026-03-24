"""Unit tests for fastapi_profiler.dashboard module."""

import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_profiler.stats import StatsCollector
from fastapi_profiler.dashboard import create_dashboard_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_dashboard_app(
    sample_rate: float = 1.0,
    slow_threshold: float = 0.0,
    enabled: bool = True,
) -> tuple:
    """Return (FastAPI app, StatsCollector, middleware_state dict)."""
    collector = StatsCollector()
    state = {
        "enabled": enabled,
        "sample_rate": sample_rate,
        "slow_request_threshold_ms": slow_threshold,
    }

    def get_enabled() -> bool:
        return state["enabled"]

    def set_enabled(value: bool) -> None:
        state["enabled"] = value

    def get_config() -> dict:
        return {
            "sample_rate": state["sample_rate"],
            "slow_request_threshold_ms": state["slow_request_threshold_ms"],
        }

    def set_config(config: dict) -> None:
        state.update(config)

    router = create_dashboard_router(
        stats_collector=collector,
        get_enabled=get_enabled,
        set_enabled=set_enabled,
        get_config=get_config,
        set_config=set_config,
    )

    app = FastAPI()
    app.mount("/__profiler__", app=router)
    return app, collector, state


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

class TestDashboardHTML:
    def test_get_dashboard_returns_html(self):
        app, _, _ = make_dashboard_app()
        client = TestClient(app)
        response = client.get("/__profiler__/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_dashboard_html_contains_title(self):
        app, _, _ = make_dashboard_app()
        client = TestClient(app)
        response = client.get("/__profiler__/")
        assert "FastAPI Profiler" in response.text

    def test_dashboard_html_no_external_urls(self):
        app, _, _ = make_dashboard_app()
        client = TestClient(app)
        response = client.get("/__profiler__/")
        # Should not reference any CDN or external resource
        assert "cdn." not in response.text
        assert "https://" not in response.text


# ---------------------------------------------------------------------------
# /stats endpoint
# ---------------------------------------------------------------------------

class TestStatsEndpoint:
    def test_stats_returns_json(self):
        app, _, _ = make_dashboard_app()
        client = TestClient(app)
        response = client.get("/__profiler__/stats")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "sample_rate" in data
        assert "slow_request_threshold_ms" in data
        assert "routes" in data

    def test_stats_enabled_field(self):
        app, _, _ = make_dashboard_app(enabled=True)
        client = TestClient(app)
        data = client.get("/__profiler__/stats").json()
        assert data["enabled"] is True

    def test_stats_disabled_field(self):
        app, _, _ = make_dashboard_app(enabled=False)
        client = TestClient(app)
        data = client.get("/__profiler__/stats").json()
        assert data["enabled"] is False

    def test_stats_sample_rate(self):
        app, _, _ = make_dashboard_app(sample_rate=0.5)
        client = TestClient(app)
        data = client.get("/__profiler__/stats").json()
        assert data["sample_rate"] == 0.5

    def test_stats_empty_routes(self):
        app, _, _ = make_dashboard_app()
        client = TestClient(app)
        data = client.get("/__profiler__/stats").json()
        assert data["routes"] == []

    def test_stats_with_recorded_data(self):
        import asyncio
        app, collector, _ = make_dashboard_app()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(collector.record("/api/test", "GET", 100.0, 200))
        client = TestClient(app)
        data = client.get("/__profiler__/stats").json()
        assert len(data["routes"]) == 1
        assert data["routes"][0]["path"] == "/api/test"


# ---------------------------------------------------------------------------
# /reset endpoint
# ---------------------------------------------------------------------------

class TestResetEndpoint:
    def test_reset_returns_message(self):
        app, _, _ = make_dashboard_app()
        client = TestClient(app)
        response = client.post("/__profiler__/reset")
        assert response.status_code == 200
        assert response.json()["message"] == "stats reset"

    def test_reset_clears_stats(self):
        import asyncio
        app, collector, _ = make_dashboard_app()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(collector.record("/test", "GET", 100.0, 200))
        client = TestClient(app)
        client.post("/__profiler__/reset")
        data = client.get("/__profiler__/stats").json()
        assert data["routes"] == []


# ---------------------------------------------------------------------------
# /config endpoint
# ---------------------------------------------------------------------------

class TestConfigEndpoint:
    def test_config_update_enabled(self):
        app, _, state = make_dashboard_app(enabled=True)
        client = TestClient(app)
        response = client.post(
            "/__profiler__/config",
            json={"enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        assert state["enabled"] is False

    def test_config_update_sample_rate(self):
        app, _, state = make_dashboard_app(sample_rate=1.0)
        client = TestClient(app)
        response = client.post(
            "/__profiler__/config",
            json={"sample_rate": 0.25},
        )
        assert response.status_code == 200
        assert response.json()["sample_rate"] == 0.25
        assert state["sample_rate"] == 0.25

    def test_config_update_slow_threshold(self):
        app, _, state = make_dashboard_app(slow_threshold=0.0)
        client = TestClient(app)
        response = client.post(
            "/__profiler__/config",
            json={"slow_request_threshold_ms": 500.0},
        )
        assert response.status_code == 200
        assert response.json()["slow_request_threshold_ms"] == 500.0

    def test_config_partial_update(self):
        app, _, state = make_dashboard_app(enabled=True, sample_rate=1.0)
        client = TestClient(app)
        # Only update sample_rate, enabled should remain True
        client.post("/__profiler__/config", json={"sample_rate": 0.5})
        assert state["enabled"] is True
        assert state["sample_rate"] == 0.5

    def test_config_invalid_json_returns_400(self):
        app, _, _ = make_dashboard_app()
        client = TestClient(app)
        response = client.post(
            "/__profiler__/config",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
