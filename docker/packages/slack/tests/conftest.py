from __future__ import annotations

import sys
from pathlib import Path

import pytest

from slack_mock.state import set_snapshot_paths

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("INPUTDIR", raising=False)
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLEDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("MCP_PROXY_TOKEN", raising=False)
    set_snapshot_paths(final_path=None, bundle_state_path=None)
    yield
    set_snapshot_paths(final_path=None, bundle_state_path=None)
