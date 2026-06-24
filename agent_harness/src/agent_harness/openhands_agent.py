"""Harbor agent that runs the OpenHands SDK agent loop *inside* the task
container.

It execs the vendored Python OpenHands runner (``/app/openhands-runner``)
in-container, driving the agent loop through the ``BaseEnvironment``
exec/upload/download contract. That makes it work on any harbor environment —
including Modal sandboxes, where the host can't reach the in-sandbox MCP port.

This agent targets the **single-container** task shape only (as staged for
Modal, and its local equivalent). It
launches the MCP proxy in-container itself; it does not handle the compose
host-side path.

Container prerequisites (baked into the ``syntara`` base image at build time):
- the runner at ``/app/openhands-runner/openhands_runner.py``
- an isolated venv with ``openhands-sdk`` at ``/app/openhands-runner/.venv``
- the syntara MCP proxy launcher at ``/app/scripts/start.sh``

Usage:
    harbor run -p .modal_tasks/some_task \
        --agent-import-path agent_harness.openhands_agent:OpenHandsAgent \
        -m anthropic/claude-opus-4-8 -e modal --ek registry_secret=aws-ecr-secret
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

MAX_TOOL_CALLS = 200

# In-container locations.
RUNNER_DIR = "/app/openhands-runner"
RUNNER_PATH = f"{RUNNER_DIR}/openhands_runner.py"
RUNNER_PYTHON = f"{RUNNER_DIR}/.venv/bin/python"
PROXY_START_CMD = "/app/scripts/start.sh --method http --port 8000"
MCP_URL = "http://localhost:8000/mcp"
HEALTH_URL = "http://localhost:8000/health"
REMOTE_LOG_DIR = "/logs/agent"
REMOTE_CONFIG_PATH = "/tmp/openhands_run_config.json"
REMOTE_TRAJECTORY_PATH = f"{REMOTE_LOG_DIR}/trajectory.json"
REMOTE_RUNNER_LOG_PATH = f"{REMOTE_LOG_DIR}/run-openhands.log"
REMOTE_PROXY_LOG_PATH = f"{REMOTE_LOG_DIR}/mcp-proxy.log"

# Timeouts (seconds).
PROBE_TIMEOUT = 15
HEALTH_TIMEOUT = 180
# Generous ceiling for the runner exec; the effective limit is harbor's [agent]
# timeout_sec (× --agent-timeout-multiplier).
AGENT_EXEC_TIMEOUT = 7200

# Host env vars forwarded into the container for the runner. The runner resolves
# LLM credentials from these per provider (see openhands_runner._resolve_llm_auth).
FORWARDED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
)


def _forwarded_env() -> dict[str, str]:
    return {k: v for k in FORWARDED_ENV_VARS if (v := os.environ.get(k))}


FORWARDED_LLM_KWARGS = (
    "reasoning_effort",
    "reasoning_summary",
    # Opt-in: --ak log_completions=true makes the runner dump each LLM call's
    # request payload (including the kwargs actually sent, e.g. reasoning_effort)
    # into the trial's agent/ log dir for verification.
    "log_completions",
)


class OpenHandsAgent(BaseAgent):
    """Runs a single OpenHands SDK agent loop inside the task container."""

    def __init__(self, *args, **kwargs) -> None:
        # Pull the allow-listed LLM kwargs out of harbor's ``--ak`` kwargs;
        # BaseAgent would otherwise drop them into **kwargs and discard them.
        # Forwarded to the runner via the run config (see run()).
        self.llm_kwargs = {
            k: kwargs.pop(k) for k in FORWARDED_LLM_KWARGS if k in kwargs
        }
        super().__init__(*args, **kwargs)

    @staticmethod
    def name() -> str:
        return "openhands-agent"

    def version(self) -> str | None:
        return "0.2.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        probe = await environment.exec(
            f"test -f {shlex.quote(RUNNER_PATH)} && test -x {shlex.quote(RUNNER_PYTHON)}",
            timeout_sec=PROBE_TIMEOUT,
        )
        if probe.return_code != 0:
            raise RuntimeError(
                f"container is missing the OpenHands runner at {RUNNER_DIR}. "
                "Rebuild the base image so the runner is staged into it."
            )

        if not await self._proxy_healthy(environment):
            self.logger.info("Launching MCP proxy in-container")
            launch = (
                f"mkdir -p {REMOTE_LOG_DIR} && "
                f"nohup {PROXY_START_CMD} </dev/null "
                f">>{REMOTE_PROXY_LOG_PATH} 2>&1 &"
            )
            result = await environment.exec(launch, timeout_sec=PROBE_TIMEOUT)
            if result.return_code != 0:
                raise RuntimeError(
                    f"failed to launch MCP proxy (rc={result.return_code}): "
                    f"{(result.stderr or '').strip()}"
                )
        await self._wait_for_proxy(environment)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        task_dir = Path(environment.environment_dir).parent
        sp_path = task_dir / "system_prompt.md"
        system_prompt = sp_path.read_text()

        config = {
            "instruction": instruction,
            "systemPrompt": system_prompt,
            "model": self.model_name,
            "mcpUrl": MCP_URL,
            "maxToolCalls": MAX_TOOL_CALLS,
            "llmKwargs": self.llm_kwargs,
            # Host-visible log dir, so opt-in completion logs land where the
            # trial syncs them back (see openhands_runner log_completions).
            "logDir": REMOTE_LOG_DIR,
        }
        config_path = (self.logs_dir / "openhands_run_config.json").resolve()
        config_path.write_text(json.dumps(config, indent=2))
        await environment.upload_file(str(config_path), REMOTE_CONFIG_PATH)

        self.logger.info("Running OpenHands runner in-container (model=%s)", self.model_name)

        # The runner writes the trajectory JSON to a dedicated file (argv[2]);
        # the OpenHands SDK's human-readable transcript + our logs go to stdout/
        # stderr, which we capture together in the runner log. Keeping the two
        # streams separate is essential — the SDK prints to stdout, so a stdout
        # redirect would corrupt the machine-readable trajectory.
        command = (
            f"mkdir -p {REMOTE_LOG_DIR} && "
            f"{shlex.quote(RUNNER_PYTHON)} {shlex.quote(RUNNER_PATH)} "
            f"{shlex.quote(REMOTE_CONFIG_PATH)} {shlex.quote(REMOTE_TRAJECTORY_PATH)} "
            f"> {REMOTE_RUNNER_LOG_PATH} 2>&1"
        )
        result = await environment.exec(
            command=command,
            env=_forwarded_env(),
            timeout_sec=AGENT_EXEC_TIMEOUT,
        )

        runner_log = await self._download_quiet(
            environment, REMOTE_RUNNER_LOG_PATH, "run-openhands.log"
        )
        trajectory = await self._download_quiet(
            environment, REMOTE_TRAJECTORY_PATH, "trajectory.json"
        )

        if result.return_code != 0:
            log_tail = "<runner log unavailable>"
            if runner_log is not None:
                log_tail = runner_log.read_text(errors="replace")[-2000:]
            raise RuntimeError(
                f"OpenHands runner exited {result.return_code}\n"
                f"--- exec stderr (last 2000) ---\n{(result.stderr or '')[-2000:]}\n"
                f"--- runner log tail ---\n{log_tail}"
            )

        if trajectory is None or trajectory.stat().st_size == 0:
            raise RuntimeError(
                "OpenHands runner produced no trajectory output; "
                f"see {self.logs_dir} for the runner log"
            )

        traj = json.loads(trajectory.read_text())
        context.n_input_tokens = traj.get("input_tokens")
        context.n_output_tokens = traj.get("output_tokens")
        context.n_cache_tokens = traj.get("cache_tokens")
        context.cost_usd = traj.get("cost_usd")
        context.metadata = {
            "agent_id": traj.get("agent_id"),
            "model": traj.get("model"),
            "n_tool_calls": traj.get("n_tool_calls"),
            "stopped_reason": traj.get("stopped_reason"),
            "final_output_chars": len(traj.get("final_output") or ""),
            "error_message": traj.get("error_message"),
        }

        # Surface genuine agent-loop errors as a trial exception so broken runs
        # aren't averaged into results.
        if traj.get("stopped_reason") == "error":
            raise RuntimeError(
                "OpenHands agent loop ended with an error after "
                f"{traj.get('n_tool_calls') or 0} tool call(s): "
                f"{traj.get('error_message') or '<no error message>'}"
            )

    async def _proxy_healthy(self, environment: BaseEnvironment) -> bool:
        result = await environment.exec(
            f"curl -fsS -o /dev/null --max-time 5 {shlex.quote(HEALTH_URL)}",
            timeout_sec=PROBE_TIMEOUT,
        )
        return result.return_code == 0

    async def _wait_for_proxy(self, environment: BaseEnvironment) -> None:
        deadline = time.monotonic() + HEALTH_TIMEOUT
        while time.monotonic() < deadline:
            if await self._proxy_healthy(environment):
                self.logger.info("MCP proxy healthy")
                return
            await asyncio.sleep(2)
        tail = await environment.exec(
            f"tail -c 2000 {REMOTE_PROXY_LOG_PATH} 2>/dev/null || true",
            timeout_sec=PROBE_TIMEOUT,
        )
        raise RuntimeError(
            f"MCP proxy at {HEALTH_URL} never became healthy within {HEALTH_TIMEOUT}s"
            f"\n--- proxy log tail ---\n{(tail.stdout or '').strip() or '<no log>'}"
        )

    async def _download_quiet(
        self, environment: BaseEnvironment, remote_path: str, filename: str
    ) -> Path | None:
        """Download a container file into logs_dir; None if unavailable."""
        target = self.logs_dir / filename
        try:
            await environment.download_file(remote_path, target)
        except Exception as e:
            self.logger.warning("could not download %s: %s", remote_path, e)
            return None
        return target if target.exists() else None
