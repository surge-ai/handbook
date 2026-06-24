"""Tests for core's sandbox viewer app — focused on the download path, which
streams file bytes from a uid-1000 reader instead of buffering them whole."""

import os

import pytest
from starlette.testclient import TestClient

from core import viewer
from core.tools import sandbox


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point WORKDIR at a temp dir so reads stay confined to it, and run the
    # viewer with no proxy token so the middleware lets requests through.
    monkeypatch.setattr(sandbox, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(viewer, "get_proxy_token", lambda: None)
    return TestClient(viewer.create_core_viewer_app())


def test_download_streams_file_contents(client, tmp_path):
    payload = bytes(range(256)) * 1000  # 256 KB — larger than one read() chunk
    (tmp_path / "data.bin").write_bytes(payload)
    resp = client.get("/api/download", params={"path": "data.bin"})
    assert resp.status_code == 200
    assert resp.content == payload
    assert resp.headers["content-type"] == "application/octet-stream"
    assert 'filename="data.bin"' in resp.headers["content-disposition"]


def test_download_empty_file(client, tmp_path):
    (tmp_path / "empty.bin").write_bytes(b"")
    resp = client.get("/api/download", params={"path": "empty.bin"})
    assert resp.status_code == 200
    assert resp.content == b""


def test_download_missing_file_returns_404(client):
    resp = client.get("/api/download", params={"path": "nope.bin"})
    assert resp.status_code == 404


def test_download_path_escape_rejected(client):
    resp = client.get("/api/download", params={"path": "../../etc/passwd"})
    assert resp.status_code == 400


def test_download_fifo_returns_404_not_hang(client, tmp_path):
    # An agent-created named pipe must not block a request worker waiting for a
    # writer — it's rejected as a non-regular file. (No writer is opened, so a
    # regression here would hang the test.)
    os.mkfifo(tmp_path / "pipe")
    resp = client.get("/api/download", params={"path": "pipe"})
    assert resp.status_code == 404
