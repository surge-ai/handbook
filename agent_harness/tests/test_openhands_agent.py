"""The host side of the agent: which --ak kwargs survive the trip to the container.

A key missing from FORWARDED_LLM_KWARGS is not an error. BaseAgent takes **kwargs and ignores
what it does not recognize, so the value disappears between registry.json and the LLM with
nothing logged, and the run still looks valid.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agent_harness.openhands_agent import (
    FORWARDED_LLM_KWARGS,
    OpenHandsAgent,
    _forwarded_env,
)


def _agent(tmp_path, **kwargs):
    return OpenHandsAgent(logs_dir=tmp_path, model_name="openai/thinkingmachines/inkling-small",
                          **kwargs)


def test_max_tokens_reaches_the_llm_kwargs(tmp_path):
    # Without this the endpoint default of 4096 applies and every long turn is truncated.
    assert "max_tokens" in FORWARDED_LLM_KWARGS
    assert _agent(tmp_path, max_tokens=250000).llm_kwargs == {"max_tokens": 250000}


def test_a_harbor_string_becomes_an_int(tmp_path):
    # harbor renders --ak values as strings, so the registry's 250000 arrives as "250000".
    # A string cap is not a cap the LLM constructor can use.
    kwargs = _agent(tmp_path, max_tokens="250000").llm_kwargs
    assert kwargs == {"max_tokens": 250000} and isinstance(kwargs["max_tokens"], int)


def test_an_unforwarded_kwarg_is_still_dropped(tmp_path):
    # The allowlist is the contract; this test fails loudly if a key is added without a reason.
    agent = _agent(tmp_path, temperature=0.7)
    assert "temperature" not in agent.llm_kwargs


def test_invoice_line_item_reaches_the_task_container(monkeypatch):
    ili = "00000000-0000-4000-8000-000000000003"
    monkeypatch.setenv("SURGE_INVOICE_LINE_ITEM_ID", ili)
    assert _forwarded_env()["SURGE_INVOICE_LINE_ITEM_ID"] == ili


def test_a_malformed_cap_fails_before_the_run(tmp_path):
    # Loud beats silent: a bad registry value stops trial setup instead of quietly reverting
    # to the endpoint default, which is the failure this allowlist entry exists to prevent.
    with pytest.raises(ValueError):
        _agent(tmp_path, max_tokens="lots")
