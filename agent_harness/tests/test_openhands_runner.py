import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agent_harness.openhands_runner import (
    REASONING_REPLAY_MODELS,
    _resolve_llm_auth,
    _resolve_llm_kwargs,
    _responses_llm,
    _share_reasoning_item_across_parallel_actions,
    configure_condenser_transport,
    install_model_capabilities,
    install_responses_replay,
)


def test_gemini_route_uses_the_proxy_key_and_base_url(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "proxy-token")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://proxy.example/gemini")

    assert _resolve_llm_auth("gemini/gemini-3.7-flash") == (
        "gemini/gemini-3.7-flash",
        "proxy-token",
        "https://proxy.example/gemini",
    )


def test_openai_route_does_not_use_the_gemini_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-token")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/openai")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://proxy.example/gemini")

    assert _resolve_llm_auth("openai/gpt-5.6-luna") == (
        "openai/gpt-5.6-luna",
        "openai-token",
        "https://proxy.example/openai",
    )


def test_openrouter_model_uses_openai_compatible_proxy(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "proxy-token")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/openai/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-used")

    assert _resolve_llm_auth("openrouter/meta/muse-glimmer-30b") == (
        "openai/meta/muse-glimmer-30b",
        "proxy-token",
        "https://proxy.example/openai/v1",
    )


def test_model_id_must_include_a_provider():
    with pytest.raises(ValueError, match="provider/model"):
        _resolve_llm_auth("gemini-3.7-flash")


def test_agent_and_condenser_kwargs_include_invoice_line_item(monkeypatch):
    ili = "00000000-0000-4000-8000-000000000003"
    monkeypatch.setenv("SURGE_INVOICE_LINE_ITEM_ID", ili)
    kwargs, _effort, _summary, _responses = _resolve_llm_kwargs({
        "llmKwargs": {"extra_headers": {"X-Existing": "kept"}}
    })
    assert kwargs["extra_headers"] == {
        "X-Existing": "kept",
        "X-Invoice-Line-Item-Id": ili,
    }


# ---------------------------------------------------------------------------
# Turn-ending and Responses behavior. These drive the real runner code
# through the pinned openhands-sdk with scripted transports (no network) and skip
# when the SDK is not installed. Run them with:
#   uv run --with openhands-sdk==1.28.1 --with pytest python -m pytest
# ---------------------------------------------------------------------------


@pytest.fixture
def sdk():
    return pytest.importorskip("openhands.sdk")


def _response(tool_calls=None, content=None):
    """A litellm ModelResponse shaped like a real /chat/completions reply."""
    from litellm.types.utils import ModelResponse

    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for name, args in tool_calls
        ]
    return ModelResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:8]}",
        created=0,
        model="gpt-4o",
        object="chat.completion",
        choices=[{"finish_reason": "tool_calls" if tool_calls else "stop", "index": 0, "message": message}],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


def _run_scripted(tmp_path, monkeypatch, script):
    """Drive the real ``run()`` with a scripted chat transport and no MCP server.

    Asking for more responses than scripted is itself a failure (the loop should
    have stopped). Returns (trajectory, captured request kwargs per LLM call).
    """
    import openhands.sdk
    import openhands.sdk.llm.llm as llm_mod

    from agent_harness import openhands_runner

    responses = list(script)
    captured = []

    def scripted_completion(**kwargs):
        captured.append(kwargs)
        assert responses, "script exhausted: the loop should have stopped"
        return responses.pop(0)

    monkeypatch.setattr(llm_mod, "litellm_completion", scripted_completion)
    # run() points the agent at the in-container MCP proxy; there is none here.
    real_agent = openhands.sdk.Agent
    monkeypatch.setattr(openhands.sdk, "Agent", lambda mcp_config, **kwargs: real_agent(**kwargs))
    monkeypatch.setattr(openhands_runner, "WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(openhands_runner, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    trajectory = openhands_runner.run(
        {
            "model": "openai/gpt-4o",
            "mcpUrl": "http://unused",
            "systemPrompt": "You are a test agent.",
            "instruction": "do the task",
            "maxToolCalls": 10,
        }
    )
    return trajectory, captured


def test_the_finish_message_is_the_final_output(sdk, tmp_path, monkeypatch):
    # The finish tool carries its answer on the action, not in an agent MessageEvent, so the
    # released runner reported an empty final_output. The SDK's default tools stay offered.
    trajectory, captured = _run_scripted(
        tmp_path, monkeypatch, [_response(tool_calls=[("finish", {"message": "FINAL ANSWER: 42"})])]
    )
    offered = [t["function"]["name"] for t in (captured[0].get("tools") or [])]
    assert {"finish", "think"} <= set(offered)
    assert trajectory["stopped_reason"] == "end_turn"
    assert trajectory["n_tool_calls"] == 1
    assert trajectory["final_output"] == "FINAL ANSWER: 42"


def test_a_plain_agent_message_ends_the_turn_with_its_text_as_final_output(sdk, tmp_path, monkeypatch):
    trajectory, captured = _run_scripted(tmp_path, monkeypatch, [_response(content="All done, report saved.")])
    assert len(captured) == 1
    assert trajectory["stopped_reason"] == "end_turn"
    assert trajectory["final_output"] == "All done, report saved."
    assert trajectory["n_tool_calls"] == 0


# ---------------------------------------------------------------------------
# Responses-path tests cover history replay and condenser transport against the
# pinned SDK 1.28.1. Conversation-level tests drive the real loop on /responses.
# ---------------------------------------------------------------------------


@pytest.fixture
def responses_sdk(sdk):
    install_responses_replay()
    install_responses_replay()  # idempotent
    _share_reasoning_item_across_parallel_actions()
    return sdk


def _reasoning_item(idx):
    return {
        "type": "reasoning",
        "id": f"rs_{idx}",
        "summary": [{"type": "summary_text", "text": f"summary {idx}"}],
        "encrypted_content": f"ENC-{idx}",
        "status": "completed",
    }


def _function_call_item(name, args, idx):
    return {
        "type": "function_call",
        "id": f"fc_{idx}",
        "call_id": f"call_{idx}",
        "name": name,
        "arguments": json.dumps(args),
        "status": "completed",
    }


def _message_item(text):
    return {
        "type": "message",
        "id": f"msg_{uuid.uuid4().hex[:8]}",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _replay_items(output):
    from openhands.sdk.llm import Message
    from openhands.sdk.llm.utils.responses_serialization import (
        _assistant_to_responses_items,
    )

    return _assistant_to_responses_items(Message.from_llm_responses_output(output))


def test_all_reasoning_items_survive_replay(responses_sdk):
    output = [_reasoning_item(1), _reasoning_item(2), _reasoning_item(3), _function_call_item("lookup", {"record": 1}, 1)]
    assert _replay_items(output) == output


def test_reasoning_only_response_preserves_every_item(responses_sdk):
    output = [_reasoning_item(1), _reasoning_item(2)]
    assert _replay_items(output) == output


def test_interleaved_items_keep_original_order_and_identity(responses_sdk):
    output = [
        _reasoning_item(1),
        _message_item("First synthetic message."),
        _function_call_item("lookup", {"z": 2, "a": "line\nvalue"}, 1),
        _reasoning_item(2),
        _message_item("Second synthetic message."),
        _function_call_item("lookup", {"record": 2}, 2),
    ]
    assert _replay_items(output) == output


def test_multi_tool_event_reload_preserves_reasoning_batch(responses_sdk):
    # Persisted events for a parallel batch recombine into one message that still
    # replays the full output. On 1.28.1 this needs the combine shim.
    from openhands.sdk.event import ActionEvent, LLMConvertibleEvent
    from openhands.sdk.llm import Message
    from openhands.sdk.llm.utils.responses_serialization import (
        _assistant_to_responses_items,
    )

    output = [_reasoning_item(1), _function_call_item("lookup", {"record": 1}, 1), _function_call_item("lookup", {"record": 2}, 2)]
    message = Message.from_llm_responses_output(output)
    events = [
        ActionEvent(
            thought=message.content if index == 0 else [],
            reasoning_content=message.reasoning_content if index == 0 else None,
            thinking_blocks=list(message.thinking_blocks) if index == 0 else [],
            responses_reasoning_item=message.responses_reasoning_item if index == 0 else None,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            tool_call=tool_call,
            llm_response_id="resp_fixture_batch",
        )
        for index, tool_call in enumerate(message.tool_calls)
    ]
    reloaded = [ActionEvent.model_validate_json(event.model_dump_json()) for event in events]
    combined = LLMConvertibleEvent.events_to_messages(reloaded)
    assert len(combined) == 1
    assert _assistant_to_responses_items(combined[0]) == output


def test_message_without_capture_falls_back_to_reconstruction(responses_sdk):
    # No reasoning item -> nothing to carry the capture; the SDK's own serialization applies.
    output = [_message_item("Plain answer."), _function_call_item("lookup", {"record": 1}, 1)]
    assert [item["type"] for item in _replay_items(output)] == ["message", "function_call"]


def _responses_response(output_items):
    """A litellm ResponsesAPIResponse shaped like a real /responses reply."""
    from litellm.types.llms.openai import ResponsesAPIResponse

    return ResponsesAPIResponse(
        id=f"resp_{uuid.uuid4().hex[:8]}",
        created_at=0,
        model="gpt-5-alias",
        object="response",
        output=output_items,
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        status="completed",
    )


def _scripted_responses_run(tmp_path, monkeypatch, script, model="openai/gpt-5-alias"):
    """Run a real Conversation on the /responses path with a scripted transport.

    Returns the captured request kwargs, one dict per LLM call.
    """
    import openhands.sdk.llm.llm as llm_mod
    from openhands.sdk import LLM, Agent, Conversation
    from pydantic import SecretStr

    responses = list(script)
    captured = []

    def scripted_responses(**kwargs):
        captured.append(kwargs)
        assert responses, "script exhausted: the loop should have stopped"
        return responses.pop(0)

    monkeypatch.setattr(llm_mod, "litellm_responses", scripted_responses)

    agent = Agent(
        llm=_responses_llm(LLM)(usage_id="agent", model=model, api_key=SecretStr("k")),
        tools=[],
        system_prompt="You are a test agent.",
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "state"),
        max_iteration_per_run=10,
    )
    conversation.send_message("do the task")
    conversation.run()
    return captured


def _reasoning_inputs(request_kwargs):
    return [(i["id"], i.get("encrypted_content")) for i in request_kwargs["input"] if i.get("type") == "reasoning"]


def test_parallel_tool_call_turns_resend_reasoning_items(responses_sdk, tmp_path, monkeypatch):
    captured = _scripted_responses_run(
        tmp_path,
        monkeypatch,
        [
            _responses_response(
                [
                    _reasoning_item(1),
                    _reasoning_item(2),
                    _function_call_item("think", {"thought": "t1"}, 1),
                    _function_call_item("think", {"thought": "t2"}, 2),
                ]
            ),
            _responses_response([_message_item("done")]),
        ],
    )
    assert _reasoning_inputs(captured[1]) == [("rs_1", "ENC-1"), ("rs_2", "ENC-2")]
    # The tool calls and their outputs must survive alongside the reasoning.
    second = captured[1]["input"]
    assert [i["call_id"] for i in second if i.get("type") == "function_call"] == ["call_1", "call_2"]
    assert [i["call_id"] for i in second if i.get("type") == "function_call_output"] == ["call_1", "call_2"]


def test_single_tool_call_turns_resend_reasoning(responses_sdk, tmp_path, monkeypatch):
    captured = _scripted_responses_run(
        tmp_path,
        monkeypatch,
        [
            _responses_response([_reasoning_item(1), _function_call_item("think", {"thought": "t1"}, 1)]),
            _responses_response([_message_item("done")]),
        ],
    )
    assert _reasoning_inputs(captured[1]) == [("rs_1", "ENC-1")]


def _instrumented_condenser_llm(llm):
    """Stub all four transports on an LLM, then configure it. Returns (llm, calls)."""
    from types import SimpleNamespace

    from openhands.sdk.llm import Message, TextContent

    calls = []

    def _response():
        return SimpleNamespace(
            id="resp_condenser_fixture",
            message=Message(role="assistant", content=[TextContent(text="Synthetic condensation summary.")]),
        )

    def _sync_stub(name):
        def stub(*args, **kwargs):
            calls.append(name)
            return _response()

        return stub

    def _async_stub(name):
        async def stub(*args, **kwargs):
            calls.append(name)
            return _response()

        return stub

    object.__setattr__(llm, "completion", _sync_stub("completion"))
    object.__setattr__(llm, "responses", _sync_stub("responses"))
    object.__setattr__(llm, "acompletion", _async_stub("acompletion"))
    object.__setattr__(llm, "aresponses", _async_stub("aresponses"))
    configure_condenser_transport(llm)
    return llm, calls


def _event_view():
    from openhands.sdk.context.view import View
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message, TextContent

    # One past the condenser's default max_size (240) forces condensation.
    events = [
        MessageEvent(
            id=f"event-fixture-{index}",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text=f"Synthetic event {index}")]),
        )
        for index in range(241)
    ]
    return View.from_events(events)


def test_chat_condenser_stays_on_chat_completions(responses_sdk):
    from openhands.sdk import LLM, LLMSummarizingCondenser
    from pydantic import SecretStr

    llm, calls = _instrumented_condenser_llm(LLM(model="openai/gpt-4.1", api_key=SecretStr("k"), num_retries=0))
    assert not llm.uses_responses_api()
    LLMSummarizingCondenser(llm=llm).condense(_event_view())
    assert calls == ["completion"]


def test_detected_responses_condenser_uses_responses(responses_sdk):
    # gpt-5* models are auto-detected as Responses-API models.
    from openhands.sdk import LLM, LLMSummarizingCondenser
    from pydantic import SecretStr

    llm, calls = _instrumented_condenser_llm(LLM(model="openai/gpt-5.6-sol", api_key=SecretStr("k"), num_retries=0))
    assert llm.uses_responses_api()
    LLMSummarizingCondenser(llm=llm).condense(_event_view())
    assert calls == ["responses"]


def test_forced_responses_condenser_uses_responses(responses_sdk):
    # --ak responses_api=true pins the LLM class to Responses for proxy aliases.
    from openhands.sdk import LLM, LLMSummarizingCondenser
    from pydantic import SecretStr

    llm, calls = _instrumented_condenser_llm(
        _responses_llm(LLM)(model="openai/muse-spark-1.2", api_key=SecretStr("k"), num_retries=0)
    )
    LLMSummarizingCondenser(llm=llm).condense(_event_view())
    assert calls == ["responses"]


def test_async_responses_condenser_uses_async_responses(responses_sdk):
    from openhands.sdk import LLM, LLMSummarizingCondenser
    from pydantic import SecretStr

    llm, calls = _instrumented_condenser_llm(
        _responses_llm(LLM)(model="openai/grok-4.6", api_key=SecretStr("k"), num_retries=0)
    )
    asyncio.run(LLMSummarizingCondenser(llm=llm).acondense(_event_view()))
    assert calls == ["aresponses"]


def test_responses_condenser_omits_tool_choice(responses_sdk):
    from openhands.sdk import LLM
    from openhands.sdk.llm import Message, TextContent
    from pydantic import SecretStr

    llm, _calls = _instrumented_condenser_llm(
        _responses_llm(LLM)(model="openai/grok-4.6", api_key=SecretStr("k"), num_retries=0)
    )
    messages = [Message(role="user", content=[TextContent(text="Summarize.")])]
    prepared = [
        llm._prepare_responses_params(messages, None, None, None, False, {}),
        asyncio.run(llm._aprepare_responses_params(messages, None, None, None, False, {})),
    ]
    for _instructions, _input, tools, call_kwargs, _telemetry in prepared:
        assert tools is None
        assert "tool_choice" not in call_kwargs


def test_condenser_request_keeps_prompt_limits_and_reasoning_body(responses_sdk, monkeypatch):
    # End to end through the real responses() path: the summarization prompt, the
    # model's limits and the Responses-style reasoning body all reach the transport.
    import openhands.sdk.llm.llm as llm_mod
    from openhands.sdk import LLM, LLMSummarizingCondenser
    from openhands.sdk.event.llm_convertible.message import MessageEvent
    from openhands.sdk.llm import Message, TextContent
    from pydantic import SecretStr

    routes = []

    def chat_transport(**kwargs):
        raise AssertionError("condenser hit /chat/completions")

    def responses_transport(**kwargs):
        routes.append(kwargs)
        return _responses_response([_message_item("a fine summary")])

    monkeypatch.setattr(llm_mod, "litellm_completion", chat_transport)
    monkeypatch.setattr(llm_mod, "litellm_responses", responses_transport)

    condenser_llm = _responses_llm(LLM)(
        usage_id="condenser", model="openai/gpt-5-alias", api_key=SecretStr("k"), num_retries=1, max_output_tokens=1234
    )
    condenser_llm.litellm_extra_body = {**condenser_llm.litellm_extra_body, "reasoning": {"effort": "max", "summary": "auto"}}
    configure_condenser_transport(condenser_llm)
    condenser = LLMSummarizingCondenser(llm=condenser_llm)
    events = [MessageEvent(source="user", llm_message=Message(role="user", content=[TextContent(text="hello world event")]))]
    condensation = condenser._generate_condensation(forgotten_events=events, summary_offset=0)

    assert len(routes) == 1
    kwargs = routes[0]
    assert any(
        "hello world event" in part.get("text", "")
        for item in kwargs["input"]
        if isinstance(item, dict)
        for part in (item.get("content") or [])
        if isinstance(part, dict)
    )
    assert kwargs["max_output_tokens"] == 1234
    assert kwargs["extra_body"] == {"reasoning": {"effort": "max", "summary": "auto"}}
    assert "tool_choice" not in kwargs
    assert condensation.summary == "a fine summary"


def test_responses_behavior_is_inert_on_the_chat_path(responses_sdk, tmp_path, monkeypatch):
    # Chat requests keep their usual shape with no Responses-only state.
    import openhands.sdk.llm.llm as llm_mod
    from openhands.sdk import LLM, Agent, Conversation
    from pydantic import SecretStr

    script = [_response(tool_calls=[("think", {"thought": "t1"}), ("think", {"thought": "t2"})]), _response(content="All done.")]
    captured = []

    def scripted_completion(**kwargs):
        captured.append(kwargs)
        return script.pop(0)

    monkeypatch.setattr(llm_mod, "litellm_completion", scripted_completion)

    agent = Agent(
        llm=LLM(usage_id="agent", model="anthropic/claude-sonnet-4-5", api_key=SecretStr("k")),
        tools=[],
        system_prompt="You are a test agent.",
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "state"),
        max_iteration_per_run=10,
    )
    conversation.send_message("do the task")
    conversation.run()

    assert len(captured) == 2
    payload = json.dumps(captured[1]["messages"])
    assert "responses_output_items" not in payload
    assert "responses_reasoning_item" not in payload
    assert '"type": "reasoning"' not in payload
    # The parallel-call turn is one assistant message with both tool calls.
    assistant = [m for m in captured[1]["messages"] if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert [tc["function"]["name"] for tc in assistant[0]["tool_calls"]] == ["think", "think"]


# ---------------------------------------------------------------------------
# Model capabilities (chat-path reasoning replay + litellm registry gaps).
# ---------------------------------------------------------------------------

# The SDK matches replay patterns as case-insensitive substrings of the full model
# id, so any pattern that is itself a fragment of a provider or proxy name would
# enable replay for that provider's (or every proxied) model.
_PROVIDER_NAMES = ("anthropic", "azure", "bedrock", "claude", "gemini", "gpt", "litellm_proxy", "openai", "openrouter", "vertex_ai")


@pytest.mark.parametrize("pattern", REASONING_REPLAY_MODELS)
def test_no_dangerously_broad_replay_patterns(pattern):
    normalized = pattern.casefold().rstrip("/-_.: ")
    assert normalized, f"empty replay pattern {pattern!r}"
    assert not any(normalized in name for name in _PROVIDER_NAMES), (
        f"replay pattern {pattern!r} is a fragment of a provider name and would enable replay for that whole provider"
    )


def test_install_model_capabilities_is_idempotent(sdk, monkeypatch):
    from openhands.sdk.llm.utils import model_features as _model_features

    allowlist = ["existing/model"]
    monkeypatch.setattr(_model_features, "SEND_REASONING_CONTENT_MODELS", allowlist)

    install_model_capabilities()
    install_model_capabilities()

    assert allowlist == ["existing/model", *REASONING_REPLAY_MODELS]


@pytest.mark.parametrize(
    "model",
    [
        "openrouter/qwen/qwen3.8-max",
        "litellm_proxy/tencent/hy3-thinking",
        "z-ai/glm-5",
        "meta/muse-glimmer-30b-v2",
        "thinkingmachines/inkling-preview",
        "openrouter/meta/muse-spark-1.2",
        "openai/meta/muse-spark-1.3",
        "openrouter/nvidia/nemotron-3-ultra-550b-a55b",
        "litellm_proxy/tencent/hy4-preview",
        "openai/kimi-k2.7",
        "openai/kimi-k3",
    ],
)
def test_configured_models_send_reasoning_content(sdk, model):
    from openhands.sdk.llm.utils import model_features as _model_features

    install_model_capabilities()

    assert _model_features.get_features(model).send_reasoning_content


@pytest.mark.parametrize(
    "model",
    ["openai/gpt-5", "openrouter/anthropic/claude-sonnet-4", "qwen/qwen3.8-coder", "tencent/hy4", "meta/muse-glimmer-29b"],
)
def test_unconfigured_models_do_not_send_reasoning_content(sdk, model):
    from openhands.sdk.llm.utils import model_features as _model_features

    install_model_capabilities()

    assert not _model_features.get_features(model).send_reasoning_content


@pytest.mark.parametrize("model", ["gemini/gemini-3.1-pro-preview", "gemini/gemini-3.7-flash", "gemini-3.7-flash"])
def test_gemini_models_keep_native_reasoning_effort(sdk, model):
    """Registering the id with litellm makes the SDK detect supports_reasoning_effort, so the param reaches the request."""
    # 1.28.1 has no LLM._model_features(); features resolve through get_features() and
    # the chat options select the effort from there.
    from openhands.sdk import LLM
    from openhands.sdk.llm.options.chat_options import select_chat_options
    from openhands.sdk.llm.utils.model_features import get_features
    from pydantic import SecretStr

    install_model_capabilities()

    llm = LLM(model=model, api_key=SecretStr("k"), reasoning_effort="high")
    assert get_features(llm._model_name_for_capabilities()).supports_reasoning_effort
    assert select_chat_options(llm, {}, has_tools=False)["reasoning_effort"] == "high"
