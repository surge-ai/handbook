from __future__ import annotations

import pytest
from jira_mock.state import reset_state


@pytest.fixture(autouse=True)
def clean_state() -> None:
    reset_state()
