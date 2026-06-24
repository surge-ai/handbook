"""Tests for service_http module."""

import pytest
from starlette.testclient import TestClient

from mcp_proxy.service_http import ProxyTokenMiddleware, _placeholder_html

# ---------------------------------------------------------------------------
# Placeholder HTML
# ---------------------------------------------------------------------------


class TestPlaceholderHtml:
    def test_contains_service_name(self):
        html = _placeholder_html("My Service")
        assert "My Service" in html

    def test_is_valid_html(self):
        html = _placeholder_html("Test")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html


# ---------------------------------------------------------------------------
# ProxyTokenMiddleware
# ---------------------------------------------------------------------------


class TestProxyTokenMiddleware:
    @pytest.fixture
    def app(self):
        """Create a minimal Starlette app with ProxyTokenMiddleware."""
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.requests import Request
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        async def index(request: Request):
            return PlainTextResponse("ok")

        async def mcp_endpoint(request: Request):
            return PlainTextResponse("mcp ok")

        return Starlette(
            routes=[
                Route("/", index),
                Route("/mcp", mcp_endpoint),
                Route("/mcp/test", mcp_endpoint),
                Route("/api/data", index),
            ],
            middleware=[Middleware(ProxyTokenMiddleware)],
        )

    def test_mcp_path_bypasses_auth(self, app, monkeypatch):
        monkeypatch.setenv("MCP_PROXY_TOKEN", "secret")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp")
        assert resp.status_code == 200

    def test_mcp_subpath_bypasses_auth(self, app, monkeypatch):
        monkeypatch.setenv("MCP_PROXY_TOKEN", "secret")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp/test")
        assert resp.status_code == 200

    def test_non_mcp_blocked_without_token(self, app, monkeypatch):
        monkeypatch.setenv("MCP_PROXY_TOKEN", "secret")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/")
        assert resp.status_code == 403

    def test_non_mcp_allowed_with_correct_token(self, app, monkeypatch):
        monkeypatch.setenv("MCP_PROXY_TOKEN", "secret")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/", headers={"x-proxy-token": "secret"})
        assert resp.status_code == 200

    def test_non_mcp_blocked_with_wrong_token(self, app, monkeypatch):
        monkeypatch.setenv("MCP_PROXY_TOKEN", "secret")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/", headers={"x-proxy-token": "wrong"})
        assert resp.status_code == 403

    def test_no_token_set_allows_all(self, app, monkeypatch):
        monkeypatch.delenv("MCP_PROXY_TOKEN", raising=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/")
        assert resp.status_code == 200
