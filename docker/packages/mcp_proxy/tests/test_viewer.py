"""Tests for the viewer reverse-proxy app."""

import httpx
import pytest
from starlette.testclient import TestClient

from mcp_proxy.viewer import _DISPLAY_NAMES, _render_shell, create_viewer_app

# ---------------------------------------------------------------------------
# Shell rendering
# ---------------------------------------------------------------------------


class TestRenderShell:
    def test_empty_registry(self):
        html = _render_shell({})
        assert "No services available" in html

    def test_single_service(self):
        html = _render_shell({"jira": 9000})
        assert "Services" in html
        assert "1 services" in html
        assert 'data-service="jira"' in html
        assert 'src="/__viewer__/set?app=jira"' in html

    def test_multiple_services(self):
        html = _render_shell({"jira": 9000, "slack": 9001, "google_mail": 9002})
        assert "3 services" in html
        for name in ("jira", "slack", "google_mail"):
            assert f'data-service="{name}"' in html

    def test_display_names_used(self):
        html = _render_shell({"jira": 9000})
        assert _DISPLAY_NAMES["jira"] in html  # "Issues"

    def test_unknown_service_gets_title_case(self):
        html = _render_shell({"my_custom_service": 9000})
        assert "My Custom Service" in html

    def test_services_sorted(self):
        html = _render_shell({"slack": 9001, "jira": 9000})
        jira_pos = html.index('data-service="jira"')
        slack_pos = html.index('data-service="slack"')
        assert jira_pos < slack_pos

    def test_first_service_is_default_iframe(self):
        html = _render_shell({"slack": 9001, "jira": 9000})
        # sorted → jira comes first
        assert 'src="/__viewer__/set?app=jira"' in html


# ---------------------------------------------------------------------------
# Viewer app routes
# ---------------------------------------------------------------------------


class TestViewerApp:
    @pytest.fixture
    def app(self):
        return create_viewer_app(
            service_registry={"test_svc": 9999, "other": 8888},
            proxy_token="secret123",
        )

    @pytest.fixture
    def client(self, app):
        return TestClient(app, raise_server_exceptions=False)

    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Services" in resp.text

    def test_reverse_proxy_unknown_service_returns_404(self, client):
        resp = client.get("/__viewer__/set?app=nonexistent")
        assert resp.status_code == 404
        assert "Unknown service" in resp.text

    def test_iframe_fallback_uses_server_state_when_cookie_blocked(self, app, monkeypatch):
        """When embedded in an iframe, browsers may block the cookie.  The
        viewer should fall back to server-side state when any referer is
        present (indicating a redirect, not a direct navigation)."""

        async def mock_request(self, *args, **kwargs):
            return httpx.Response(200, text="service content")

        monkeypatch.setattr(httpx.AsyncClient, "request", mock_request)
        client = TestClient(app, raise_server_exceptions=False)

        # First, select a service via /__viewer__/set to populate server state.
        resp = client.get("/__viewer__/set?app=test_svc", follow_redirects=False)
        assert resp.status_code == 307

        # Now simulate an iframe request with NO cookie but a referer present
        # (browser stripped the path but kept the origin).
        resp = client.get(
            "/",
            headers={"referer": "http://some-outer-host/"},
            cookies={},  # no cookie — blocked by browser
        )
        # Should proxy to the service, not re-render the shell
        assert resp.status_code == 200
        assert "service content" in resp.text
        assert "Services" not in resp.text  # not the shell HTML

    def test_iframe_fallback_not_triggered_without_referer(self, client):
        """Without a referer, the server state fallback should NOT activate —
        direct navigation should always show the shell."""
        resp = client.get("/", cookies={})
        assert resp.status_code == 200
        assert "Services" in resp.text

    def test_reverse_proxy_connection_error_returns_502(self, app, monkeypatch):
        # Mock httpx to simulate a connection error regardless of port state
        async def mock_request(self, *args, **kwargs):
            raise httpx.ConnectError("Connection refused")

        monkeypatch.setattr(httpx.AsyncClient, "request", mock_request)
        client = TestClient(app, raise_server_exceptions=False)
        # Set the service cookie first, then make a proxied request
        client.cookies.set("__viewer_service", "test_svc")
        resp = client.get("/some-page", headers={"referer": "http://testserver/"})
        assert resp.status_code == 502
        assert "Service unavailable" in resp.text
