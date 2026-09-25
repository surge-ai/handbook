"""Run OpenHands inside the task container.

The agent uses the in-container MCP proxy. The image stores this file at
``/app/openhands-runner/openhands_runner.py`` with an isolated ``openhands-sdk`` venv.

Contract:
- ``argv[1]`` is the config JSON path.
- ``argv[2]`` is the trajectory JSON path. Stdout holds the readable transcript.
- Agent errors are stored in the trajectory. The process still exits 0 so the host decides
  whether the trial failed.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any

MCP_TOOL_TIMEOUT = 300
WORKSPACE_DIR = "/tmp/openhands_workspace"
STATE_DIR = "/tmp/openhands_state"

def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _as_bool(value: Any) -> bool | None:
    """Harbor passes ``--ak`` values as strings, so accept the usual spellings."""
    if value is None or isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _responses_llm(base_cls: type) -> type:
    """Pin an LLM subclass to the Responses API.

    The SDK routes unknown proxy models to /chat/completions. Set ``responses_api=true`` when a
    route serves only /responses. Build the subclass here because ``run`` imports the SDK.
    """
    return type("ResponsesLLM", (base_cls,), {"uses_responses_api": lambda self: True})


def _resolve_llm_kwargs(
    config: dict[str, Any],
) -> tuple[dict[str, Any], str | None, str | None, bool | None]:
    """Split constructor args from effort, summary, and route controls.

    The SDK rejects ``max`` in the constructor, so effort is applied later. Summary goes in the
    request body. ``responses_api`` selects the LLM class.
    """
    llm_kwargs = dict(config.get("llmKwargs") or {})

    effort = llm_kwargs.pop("reasoning_effort", None)
    if effort is not None:
        effort = str(effort).strip().lower() or None

    if invoice_line_item_id := os.environ.get("SURGE_INVOICE_LINE_ITEM_ID"):
        llm_kwargs["extra_headers"] = {
            **dict(llm_kwargs.get("extra_headers") or {}),
            "X-Invoice-Line-Item-Id": invoice_line_item_id,
        }
    summary = llm_kwargs.pop("reasoning_summary", None)
    if summary is not None:
        summary = str(summary).strip().lower() or None

    forced = _as_bool(llm_kwargs.pop("responses_api", None))

    return llm_kwargs, effort, summary, forced


def _resolve_llm_auth(model_name: str) -> tuple[str, str | None, str | None]:
    """Map a Harbor model ID to LiteLLM credentials.

    OpenRouter Anthropic IDs keep the Anthropic route. Other OpenRouter IDs use the
    OpenAI-compatible proxy.
    """
    if "/" not in model_name:
        raise ValueError(
            f"model must be in 'provider/model' form; got {model_name!r}"
        )
    provider, model = model_name.split("/", 1)

    if provider == "openrouter" and model.startswith("anthropic/"):
        provider = "anthropic"
        model = model.split("/", 1)[1].replace(".", "-")
    elif provider == "openrouter":
        provider = "openai"

    if provider == "anthropic":
        litellm_model = f"anthropic/{model}"
        return (
            litellm_model,
            os.environ.get("ANTHROPIC_API_KEY"),
            os.environ.get("ANTHROPIC_BASE_URL"),
        )

    _provider_key_env = {
        "openai": "OPENAI_API_KEY",
        "google": "GEMINI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    api_key = os.environ.get(_provider_key_env.get(provider, ""))
    if provider == "openai":
        base_url = os.environ.get("OPENAI_BASE_URL")
    elif provider in ("google", "gemini"):
        base_url = os.environ.get("GEMINI_BASE_URL")
    else:
        base_url = None
    return f"{provider}/{model}", api_key, base_url


def _usage(*llms: Any) -> dict[str, int | float | None]:
    """Pull token/cost totals from one or more LLMs' metrics, tolerant of API drift.

    Sums across every LLM passed in (e.g. the agent loop plus the condenser) so
    the reported cost and cache usage reflect *all* model traffic for the trial,
    not just the main agent call. Each field stays ``None`` if no LLM exposed it,
    so a metrics-shape change degrades to null rather than crashing the trial.
    """
    input_tokens = output_tokens = cache_tokens = None
    cost: float | None = None

    def _accumulate(current: int | float | None, value: Any) -> int | float | None:
        if not isinstance(value, (int, float)):
            return current
        return value if current is None else current + value

    for llm in llms:
        metrics = getattr(llm, "metrics", None)
        if metrics is None:
            continue
        cost = _accumulate(cost, getattr(metrics, "accumulated_cost", None))
        token_usage = getattr(metrics, "accumulated_token_usage", None)
        if token_usage is None:
            continue
        input_tokens = _accumulate(input_tokens, getattr(token_usage, "prompt_tokens", None))
        output_tokens = _accumulate(output_tokens, getattr(token_usage, "completion_tokens", None))
        cache_tokens = _accumulate(cache_tokens, getattr(token_usage, "cache_read_tokens", None))

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_tokens": cache_tokens,
        # accumulated_cost is 0.0 when litellm has no pricing for the model
        # (e.g. an aliased model behind a proxy); treat that as "unknown" so
        # downstream cost reporting can fall back to provider dashboards.
        "cost_usd": cost if cost else None,
    }


# The SDK truncates tool results at 50K characters. That can cut off large PDFs and sheets.
# A 1,000,000-character cap preserves realistic reads while protecting the context window
# from unusually large output.
TOOL_OBSERVATION_CHAR_LIMIT = 1_000_000


def _raise_tool_observation_limit() -> None:
    """Lift the SDK's hardcoded 50K tool-observation truncation cap.

    The limit is a module-level constant (the per-message ``enable_truncation``
    field is deprecated), and ``message.py`` binds it at import time, so both the
    source module and that imported reference must be patched.
    """
    import openhands.sdk.llm.message as _message
    import openhands.sdk.utils.truncate as _truncate

    _truncate.DEFAULT_TEXT_CONTENT_LIMIT = TOOL_OBSERVATION_CHAR_LIMIT
    _message.DEFAULT_TEXT_CONTENT_LIMIT = TOOL_OBSERVATION_CHAR_LIMIT


def _merge_tool_outputs() -> None:
    """Send one ``function_call_output`` per tool call on the /responses path.

    The SDK emits one output per content block. MCP observations often have two blocks, but the
    proxy requires one output per call. Merge the blocks without dropping content.
    """
    from openhands.sdk.llm.utils import responses_serialization as _rs

    if getattr(_rs, "_surge_merges_tool_outputs", False):
        return
    original = _rs._tool_to_responses_items

    def merged(message, *, vision_enabled: bool) -> list[dict[str, Any]]:
        items = original(message, vision_enabled=vision_enabled)
        if len(items) < 2:
            return items
        # Preserve block order, including the order of images and captions.
        parts: list[dict[str, Any]] = []
        for item in items:
            out = item["output"]
            if isinstance(out, str):
                parts.append({"type": "input_text", "text": out})
            else:
                parts.extend(out)
        # Keep text-only output as a string.
        output: Any = ("\n".join(p["text"] for p in parts)
                       if all(p.get("type") == "input_text" for p in parts) else parts)
        return [{"type": "function_call_output", "call_id": items[0]["call_id"],
                 "output": output}]

    _rs._tool_to_responses_items = merged
    _rs._surge_merges_tool_outputs = True


# Compatibility patches for the SDK pinned in docker/Dockerfile. SDK/LiteLLM imports
# are deferred so the host environment can import this module without installing the SDK.
_responses_replay_installed = False

# Reasoning models that openhands.sdk does not include in SEND_REASONING_CONTENT_MODELS
# Note: these are substrings not full model IDs
# TODO: Remove entries as the pinned SDK gains native support.
REASONING_REPLAY_MODELS = (
    "qwen/qwen3.8-max",
    "tencent/hy3",
    "z-ai/glm",
    "meta/muse-glimmer-30b",
    "thinkingmachines/inkling",
    "meta/muse-spark-1.2",
    "meta/muse-spark-1.3",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "tencent/hy4-preview",
    "kimi-k2.7",
    # OpenHands SDK 1.43.1 includes this model natively; the pinned 1.28.1 does not.
    "kimi-k3",
)

# Model IDs missing from pinned litellm registry.
# TODO: Remove entries as the pinned LiteLLM registry gains native support.
_GEMINI_37_FLASH_INFO = {
    "litellm_provider": "gemini",
    "mode": "chat",
    "supports_reasoning": True,
    "supports_function_calling": True,
    "supports_vision": True,
    "supports_prompt_caching": True,
}
MISSING_LITELLM_MODELS = {
    "gemini/gemini-3.7-flash": _GEMINI_37_FLASH_INFO,
    "gemini-3.7-flash": _GEMINI_37_FLASH_INFO,
}


def install_model_capabilities() -> None:
    """Fix model-capability gaps in the pinned SDK/litellm. Idempotent."""
    import litellm
    from openhands.sdk.llm.utils import model_features as _model_features

    allowlist = _model_features.SEND_REASONING_CONTENT_MODELS
    for pattern in REASONING_REPLAY_MODELS:
        if pattern not in allowlist:
            allowlist.append(pattern)
    litellm.register_model(MISSING_LITELLM_MODELS)


def install_responses_replay() -> None:
    """Patch the SDK so Responses API history survives round-trips.

    When openhands converts Responses API <-> Message, it strips all but the
    last reasoning item. Idempotent; call before the loop. Strategy:

    1. Add a field to the ReasoningItemModel to capture the original output
       items.
    2. Capture the full ordered output on conversion.
    3. Replay the original items verbatim on serialization.
    """
    import openhands.sdk.llm.utils.responses_serialization as ser_mod
    from openhands.sdk.event.base import Event
    from openhands.sdk.llm.message import Message, ReasoningItemModel
    from pydantic.fields import FieldInfo

    global _responses_replay_installed
    if _responses_replay_installed:
        return

    # -- 1. add the persistence field to the real class -------------------
    if "responses_output_items" not in ReasoningItemModel.__pydantic_fields__:
        ReasoningItemModel.__pydantic_fields__["responses_output_items"] = FieldInfo(
            annotation=list[dict[str, Any]] | None, default=None, repr=False
        )
        ReasoningItemModel.model_rebuild(force=True)

        # Nested schemas were compiled against the old shape; recompile every
        # model that can carry a ReasoningItemModel through persistence.
        Message.model_rebuild(force=True)
        _rebuild_subclass_tree(Event)

    # -- 2. capture the full ordered output on conversion -----------------
    original_from_output = Message.from_llm_responses_output.__func__

    def from_output_with_capture(cls: type, output: Any) -> Any:
        items = list(output or [])
        message = original_from_output(cls, items)
        carrier = message.responses_reasoning_item
        if carrier is not None:
            carrier.responses_output_items = [
                copy.deepcopy(item)
                if isinstance(item, dict)
                else item.model_dump(mode="json", exclude_none=True)
                for item in items
            ]
        return message

    Message.from_llm_responses_output = classmethod(from_output_with_capture)

    # -- 3. replay the original items verbatim on serialization -----------
    original_assistant = ser_mod._assistant_to_responses_items

    def assistant_with_replay(message: Any) -> list[dict[str, Any]]:
        """Replay the original items verbatim on serialization."""
        carrier = getattr(message, "responses_reasoning_item", None)
        items = getattr(carrier, "responses_output_items", None)
        if items is not None:
            return copy.deepcopy(items)
        return original_assistant(message)

    ser_mod._assistant_to_responses_items = assistant_with_replay

    _responses_replay_installed = True


def _rebuild_subclass_tree(cls: type) -> None:
    """Rebuild the Pydantic model tree for all subclasses of the given class."""
    cls.model_rebuild(force=True)  # type: ignore[attr-defined]
    for subclass in cls.__subclasses__():
        _rebuild_subclass_tree(subclass)


def _share_reasoning_item_across_parallel_actions() -> None:
    """Keep the Responses reasoning carrier on a parallel tool-call turn.

    openhands-sdk 1.28.1's ``_combine_action_events`` rebuilds the assistant message of a multi-action
    batch without ``responses_reasoning_item``; 1.43.1 copies it from the first event (event/base.py).
    Apply that one-field fix here so the replay above also covers parallel tool calls.
    """
    import openhands.sdk.event.base as _event_base

    if getattr(_event_base, "_surge_shares_reasoning_item", False):
        return
    original_combine = _event_base._combine_action_events

    def combine(events: list[Any]) -> Any:
        message = original_combine(events)
        if message.responses_reasoning_item is None:
            message.responses_reasoning_item = events[0].responses_reasoning_item
        return message

    _event_base._combine_action_events = combine
    _event_base._surge_shares_reasoning_item = True


def configure_condenser_transport(llm: Any) -> None:
    """Route condenser calls through the Responses API when the model uses it.

    The SDK's ``LLMSummarizingCondenser`` hard-codes the Chat Completions
    transport (``completion``/``acompletion``) even when its model is a
    Responses-API model, so a trial that triggers compaction sends
    Responses-specific request configuration to the wrong endpoint and
    fails. Rebind the condenser LLM's transports to
    ``responses``/``aresponses``; condenser calls are tool-free, so drop
    ``tool_choice`` from the prepared request.
    """
    if not llm.uses_responses_api():
        return

    finalize = llm._finalize_responses_params

    def finalize_condenser_params(*args: Any, **kwargs: Any) -> Any:
        # (instructions, input_items, resp_tools, call_kwargs, telemetry_ctx)
        prepared = finalize(*args, **kwargs)
        prepared[3].pop("tool_choice", None)
        telemetry_kwargs = prepared[4].get("kwargs")
        if isinstance(telemetry_kwargs, dict):
            telemetry_kwargs.pop("tool_choice", None)
        return prepared

    # LLM is a frozen pydantic model; bypass its setattr guard.
    object.__setattr__(llm, "_finalize_responses_params", finalize_condenser_params)
    object.__setattr__(llm, "completion", llm.responses)
    object.__setattr__(llm, "acompletion", llm.aresponses)


def run(config: dict[str, Any]) -> dict[str, Any]:
    from openhands.sdk import (
        LLM,
        Agent,
        Conversation,
        ConversationExecutionStatus,
        Event,
        LLMSummarizingCondenser,
        MessageEvent,
    )
    from openhands.sdk.event import ActionEvent, AgentErrorEvent
    from openhands.sdk.llm.message import content_to_str
    from pydantic import SecretStr

    _raise_tool_observation_limit()
    _merge_tool_outputs()
    install_responses_replay()
    _share_reasoning_item_across_parallel_actions()
    install_model_capabilities()

    model_name = config["model"]
    litellm_model, api_key, base_url = _resolve_llm_auth(model_name)
    llm_kwargs, reasoning_effort, reasoning_summary, responses_api = _resolve_llm_kwargs(config)

    # Opt-in per-call request logging (--ak log_completions=true). Point the
    # folder at the trajectory's log dir so it's downloaded with the trial. Each
    # logged payload records the kwargs actually sent to the model (e.g.
    # reasoning_effort), giving a verifiable record of what was requested.
    if llm_kwargs.pop("log_completions", False):
        log_root = config.get("logDir") or WORKSPACE_DIR
        llm_kwargs["log_completions"] = True
        llm_kwargs["log_completions_folder"] = os.path.join(log_root, "completions")
        _log(f"completion logging enabled -> {llm_kwargs['log_completions_folder']}")

    _log(
        f"Running OpenHands loop (model={litellm_model}, "
        f"base_url={base_url or 'default'})"
    )

    # Allow 10 minutes per model call. OpenHands defaults to 300 seconds, and LiteLLM does
    # not retry its timeout, so slow proxy calls otherwise drop valid trials.
    llm_cls = _responses_llm(LLM) if responses_api else LLM
    llm = llm_cls(
        usage_id="agent",
        model=litellm_model,
        api_key=SecretStr(api_key) if api_key else None,
        base_url=base_url,
        timeout=600,
        **llm_kwargs,
    )

    # The SDK summarizes old turns after a context overflow and retries. A separate
    # LLM instance keeps condenser metrics distinct.
    condenser_llm = llm_cls(
        usage_id="condenser",
        model=litellm_model,
        api_key=SecretStr(api_key) if api_key else None,
        base_url=base_url,
        timeout=600,
        **llm_kwargs,
    )

    anthropic = litellm_model.startswith("anthropic/")
    reasoning: dict[str, str] = {}
    if reasoning_effort is not None:
        if anthropic:
            llm.reasoning_effort = reasoning_effort
            condenser_llm.reasoning_effort = reasoning_effort
        else:
            reasoning["effort"] = reasoning_effort
    if reasoning_summary is not None and not anthropic:
        reasoning["summary"] = reasoning_summary
    if reasoning:
        rb = {"reasoning": reasoning}
        llm.litellm_extra_body = {**llm.litellm_extra_body, **rb}
        condenser_llm.litellm_extra_body = {**condenser_llm.litellm_extra_body, **rb}

    configure_condenser_transport(condenser_llm)

    # Use the task's system prompt in place of the default coding prompt.
    agent = Agent(
        llm=llm,
        tools=[],
        mcp_config={
            "mcpServers": {
                "handbook": {
                    "url": config["mcpUrl"],
                    "timeout": MCP_TOOL_TIMEOUT,
                }
            }
        },
        system_prompt=config["systemPrompt"],
        condenser=LLMSummarizingCondenser(llm=condenser_llm),
    )

    n_tool_calls = 0
    n_agent_errors = 0
    final_output = ""
    error_message: str | None = None

    def callback(event: Event) -> None:
        nonlocal n_tool_calls, n_agent_errors, final_output, error_message
        if isinstance(event, ActionEvent):
            n_tool_calls += 1
            # The finish tool ends the run without an agent MessageEvent; its message is the final answer.
            if event.tool_name == "finish" and getattr(event.action, "message", ""):
                final_output = event.action.message
        elif isinstance(event, AgentErrorEvent):
            n_agent_errors += 1
            error_message = getattr(event, "error", None) or str(event)
        elif isinstance(event, MessageEvent) and event.source == "agent":
            text = "".join(content_to_str(event.to_llm_message().content))
            if text:
                final_output = text

    conversation = Conversation(
        agent=agent,
        callbacks=[callback],
        workspace=WORKSPACE_DIR,
        persistence_dir=STATE_DIR,
        max_iteration_per_run=int(config["maxToolCalls"]),
    )

    saw_error = False
    try:
        conversation.send_message(config["instruction"])
        conversation.run()
    except Exception as e:  # provider/transport/loop failure
        saw_error = True
        error_message = error_message or str(e)
        _log(f"OpenHands conversation.run() raised: {e}")

    status = getattr(conversation.state, "execution_status", None)
    status_val = getattr(status, "value", status)
    infra_failure = saw_error or status_val == ConversationExecutionStatus.ERROR.value
    did_work = n_tool_calls > 0 or bool(final_output)
    if saw_error:
        # conversation.run() raised (e.g. litellm.Timeout / provider error): the
        # loop was cut short and never completed, so this is a transport/infra
        # failure even if the agent had already made tool calls.
        stopped_reason = "error"
    elif infra_failure and not did_work:
        # ERROR with no output is a genuine failure.
        stopped_reason = "error"
    elif status_val == ConversationExecutionStatus.STUCK.value:
        stopped_reason = "stuck"
    elif status_val == ConversationExecutionStatus.FINISHED.value:
        stopped_reason = "end_turn"
    else:
        stopped_reason = "max_tool_calls"

    usage = _usage(llm, condenser_llm)

    return {
        "agent_id": "openhands_sdk",
        "model": litellm_model,
        "final_output": final_output,
        "n_tool_calls": n_tool_calls,
        "n_agent_errors": n_agent_errors,
        "stopped_reason": stopped_reason,
        "error_message": error_message,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cache_tokens": usage["cache_tokens"],
        "cost_usd": usage["cost_usd"],
    }


def main() -> None:
    if len(sys.argv) != 3:
        _log("usage: openhands_runner.py <config.json> <output.json>")
        sys.exit(2)

    config_path, output_path = sys.argv[1], sys.argv[2]
    config = json.loads(open(config_path).read())
    try:
        result = run(config)
    except Exception as e:
        # Preserve pre-loop failures in a trajectory for the host.
        result = {
            "agent_id": "openhands_sdk",
            "model": config.get("model"),
            "final_output": "",
            "n_tool_calls": 0,
            "stopped_reason": "error",
            "error_message": str(e),
            "input_tokens": None,
            "output_tokens": None,
            "cache_tokens": None,
            "cost_usd": None,
        }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    _log(
        f"wrote trajectory to {output_path} "
        f"(stopped_reason={result.get('stopped_reason')}, "
        f"n_tool_calls={result.get('n_tool_calls')})"
    )


if __name__ == "__main__":
    main()
