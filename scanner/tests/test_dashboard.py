"""Tests for the Canvas live dashboard (S24)."""

from pathlib import Path

import pytest

DASHBOARD_PATH = Path(__file__).parent.parent / "v6" / "cards" / "dashboard.html"


# ── File-level tests ──────────────────────────────────────────────────────


def test_dashboard_file_exists():
    assert DASHBOARD_PATH.exists(), "dashboard.html must exist"


def test_dashboard_is_valid_html():
    html = DASHBOARD_PATH.read_text()
    assert html.startswith("<!DOCTYPE html>")
    assert "<html>" in html or "<html" in html
    assert "</html>" in html


def test_dashboard_contains_session_panel():
    html = DASHBOARD_PATH.read_text()
    assert "SESSION STATUS" in html
    assert "session-badge" in html
    assert "session-strategy" in html


def test_dashboard_contains_heat_panel():
    html = DASHBOARD_PATH.read_text()
    assert "TOP CONVICTION" in html
    assert "heat-rows" in html


def test_dashboard_contains_gauge_panel():
    html = DASHBOARD_PATH.read_text()
    assert "FEAR" in html and "GREED" in html
    assert "gauge-needle" in html
    assert "gauge-num" in html


def test_dashboard_contains_activity_panel():
    html = DASHBOARD_PATH.read_text()
    assert "RECENT ACTIVITY" in html
    assert "activity-rows" in html


def test_dashboard_fetches_api_endpoints():
    html = DASHBOARD_PATH.read_text()
    assert "/v6/session/status" in html
    assert "/v6/heat" in html
    assert "/v6/brief" in html
    assert "/v6/pulse" in html


def test_dashboard_auto_refreshes():
    html = DASHBOARD_PATH.read_text()
    assert "setInterval" in html
    assert "60000" in html


def test_dashboard_design_tokens():
    html = DASHBOARD_PATH.read_text()
    assert "#c8ff00" in html
    assert "#0a0a0a" in html
    assert "JetBrains Mono" in html


# ── Endpoint test ─────────────────────────────────────────────────────────


def test_dashboard_endpoint_returns_html():
    """Test /v6/dashboard serves the HTML file with correct content-type."""
    from fastapi.testclient import TestClient
    from scanner.api.server import app

    client = TestClient(app)
    resp = client.get("/v6/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "SESSION STATUS" in resp.text
    assert "FEAR" in resp.text
