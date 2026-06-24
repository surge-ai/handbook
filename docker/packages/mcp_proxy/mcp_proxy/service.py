"""MCP service configuration and lifecycle management.

Provides :class:`McpConfig` (a Pydantic model representing ``mcp.json``) and
:class:`McpService` (a wrapper that adds install/setup/run lifecycle methods).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger("mcp_proxy")


def resolve_command(command: str) -> str:
    """Resolve a bare ``python``/``python3`` to the proxy's own interpreter so
    services don't depend on /opt/venv being on PATH (it isn't, by design)."""
    if command in ("python", "python3"):
        return sys.executable
    return command


# ---------------------------------------------------------------------------
# mcp.json data model
# ---------------------------------------------------------------------------


class HookStep(BaseModel):
    """A single setup/run step: ``{command, args?, env?}``."""

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


def _parse_steps(raw: dict | list | None) -> list[HookStep]:
    """Normalise a hook value (run or setup) to a list of HookStep."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [HookStep(**raw)]
    return [HookStep(**s) for s in raw]


class McpConfig(BaseModel):
    """Parsed ``mcp.json`` configuration for one MCP server package."""

    model_config = {"arbitrary_types_allowed": True}

    package_dir: Path
    name: str
    run: list[HookStep]
    install: list[HookStep] = Field(default_factory=list)
    setup: list[HookStep] = Field(default_factory=list)
    # Optional lightweight entrypoint used by `mcp-proxy gen` to list tools
    # without spinning up the full service. Falls back to ``run`` when absent.
    gen: list[HookStep] = Field(default_factory=list)
    toolsets: dict[str, list[str]] = Field(default_factory=dict)
    # Names of secrets to forward from the proxy's env to the
    # subprocess. The proxy filter drops credential-shaped names by default.
    secrets: list[str] = Field(default_factory=list)
    startup_timeout: float = 30.0
    # When False, tools from this package are exposed without the ``{pkg}__``
    # prefix. Defaults to True for backwards compatibility.
    namespaced: bool = True
    cwd: Path

    @classmethod
    def from_mcp_json(cls, package_dir: Path, data: dict) -> McpConfig:
        name = data.get("name") or package_dir.name
        run = _parse_steps(data.get("run"))
        if not run:
            raise ValueError("'run' is required and must define at least one step")
        install = _parse_steps(data.get("install"))
        setup = _parse_steps(data.get("setup"))
        gen = _parse_steps(data.get("gen"))
        toolsets = data.get("toolsets", {})
        secrets = list(data.get("secrets", []))
        startup_timeout = float(data.get("startup_timeout", 30.0))
        namespaced = bool(data.get("namespaced", True))

        raw_cwd = data.get("cwd")
        cwd = (package_dir / raw_cwd).resolve() if raw_cwd else package_dir

        return cls(
            package_dir=package_dir,
            name=name,
            run=run,
            install=install,
            setup=setup,
            gen=gen,
            toolsets=toolsets,
            secrets=secrets,
            startup_timeout=startup_timeout,
            namespaced=namespaced,
            cwd=cwd,
        )


# ---------------------------------------------------------------------------
# Toolset helpers
# ---------------------------------------------------------------------------


def _service_argv(step: HookStep, command_prefix: list[str] | None) -> list[str]:
    """Assemble the argv for a run step, optionally prefixed.

    *command_prefix* (e.g. ``["faketime", "2025-01-15 09:00:00"]``) is prepended
    to the step's command so the service runs under a wrapper. ``None`` or an
    empty list returns the bare argv. Factored out so the prefixing is unit
    testable without launching a process.
    """
    argv = [resolve_command(step.command), *step.args]
    if command_prefix:
        return [*command_prefix, *argv]
    return argv


def _bare_toolsets(pkg_name: str, pkg_toolset_keys: set[str], requested: list[str]) -> set[str]:
    """Resolve *requested* toolset names to bare names for *pkg_name*.

    Accepts package-namespaced names (``"google_mail_read"``) and returns
    the set of matching bare toolset names that exist in *pkg_toolset_keys*.

    Examples::

        _bare_toolsets("google_mail", {"read", "write"}, ["google_mail_read", "slack_write"])
        # → {"read"}   ("slack_write" has no google_mail prefix)

        _bare_toolsets("core", {"ds_all", "read"}, ["core_ds_all", "core_read"])
        # → {"ds_all", "read"}
    """
    result: set[str] = set()
    for ts in requested:
        prefix = f"{pkg_name}_"
        if ts.startswith(prefix):
            bare = ts[len(prefix) :]
            if bare in pkg_toolset_keys:
                result.add(bare)
    return result


# ---------------------------------------------------------------------------
# McpService — lifecycle wrapper around McpConfig
# ---------------------------------------------------------------------------


class McpService:
    """Wraps an :class:`McpConfig` with lifecycle methods (install, setup, run)."""

    def __init__(self, config: McpConfig, child_processes: list[subprocess.Popen] | None = None) -> None:
        self.config = config
        self._child_processes = child_processes if child_processes is not None else []
        self._tools_cache: list[dict] | None = None

    # -- Convenience accessors ------------------------------------------------

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def package_dir(self) -> Path:
        return self.config.package_dir

    @property
    def cwd(self) -> Path:
        return self.config.cwd

    @property
    def toolsets(self) -> dict[str, list[str]]:
        return self.config.toolsets

    @property
    def secrets(self) -> list[str]:
        """Credential-shaped env var names this service declared in mcp.json to
        receive from the proxy's env. Used by ``_build_subprocess_env`` to
        scope which subprocesses see each secret."""
        return self.config.secrets

    @property
    def tools(self) -> list[dict]:
        """Tools introspected at gen time, lazily loaded from mcp-tools.generated.json.

        Raises FileNotFoundError if the file is absent — run ``just gen`` to produce it.
        """
        if self._tools_cache is None:
            tools_file = self.package_dir / "mcp-tools.generated.json"
            if not tools_file.exists():
                raise FileNotFoundError(
                    f"mcp-tools.generated.json not found for {self.name} at {tools_file}. "
                    "Run 'just gen' to generate it."
                )
            self._tools_cache = json.loads(tools_file.read_text()).get("tools", [])
        return self._tools_cache

    # -- Hook execution -------------------------------------------------------

    def _run_hook_step(self, step: HookStep, cwd: Path, env: dict[str, str], label: str) -> bool:
        """Run a single hook step synchronously.  Returns True on success."""
        step_args = [resolve_command(step.command), *step.args]
        merged_env = {**env, **step.env}

        logger.info("%s: %s", label, " ".join(step_args))
        try:
            result = subprocess.run(
                step_args, cwd=str(cwd), env=merged_env, check=False, stdin=subprocess.DEVNULL, stdout=sys.stderr
            )
            if result.returncode != 0:
                logger.error("%s exited with code %d", label, result.returncode)
                return False
            return True
        except FileNotFoundError:
            logger.error("%s: command not found: %s", label, step.command)
            return False

    def _run_steps(self, steps: list[HookStep], phase: str, cwd: Path, env: dict[str, str]) -> bool:
        """Run a list of hook steps in order.  Returns False if any step fails."""
        for i, step in enumerate(steps):
            label = f"{phase}({self.name})[{i}]" if len(steps) > 1 else f"{phase}({self.name})"
            if not self._run_hook_step(step, cwd, env, label):
                return False
        return True

    def run_install(self, env: dict[str, str]) -> bool:
        """Run the package's install steps.  No-op (returns True) if none are defined."""
        if not self.config.install:
            logger.info("install(%s): no install hook defined, skipping", self.name)
            return True
        return self._run_steps(self.config.install, "install", self.package_dir, env)

    def run_setup(self, env: dict[str, str]) -> bool:
        """Run the package's setup steps.  No-op (returns True) if none are defined."""
        if not self.config.setup:
            logger.info("setup(%s): no setup hook defined, skipping", self.name)
            return True
        return self._run_steps(self.config.setup, "setup", self.package_dir, env)

    def run_pre_steps(self, env: dict[str, str]) -> bool:
        """Run all run steps except the last (pre-run steps).  No-op if only one run step."""
        pre = self.config.run[:-1]
        if not pre:
            return True
        return self._run_steps(pre, "run", self.cwd, env)

    def start_service(
        self,
        env: dict[str, str],
        port: int,
        proxy_token: str,
        command_prefix: list[str] | None = None,
    ) -> subprocess.Popen | None:
        """Start the service as an HTTP subprocess on the given port.

        Uses the last ``run`` step as the server command. *command_prefix*, when
        given, wraps that command (e.g. ``["faketime", "<spec>"]``) so the
        service — and everything it spawns — runs under the wrapper.
        Returns the Popen handle, or None if the service failed to start.
        """
        step = self.config.run[-1]
        run_args = _service_argv(step, command_prefix)
        merged_env = {
            **env,
            **step.env,
            "PORT": str(port),
            "MCP_PROXY_TOKEN": proxy_token,
        }

        logger.info("Starting %s on port %d (command: %s)", self.name, port, " ".join(run_args))

        try:
            proc = subprocess.Popen(
                run_args,
                cwd=str(self.cwd),
                env=merged_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=sys.stderr,
            )
            self._child_processes.append(proc)
            return proc
        except FileNotFoundError:
            logger.error("Failed to start %s: command not found: %s", self.name, step.command)
            return None

    def resolve_tool_transforms(self, tool_sets: list[str]) -> dict:
        """Return a ``{original_name: ToolTransformConfig}`` mapping.

        Allowed tools are renamed to ``{pkg}__{name}``.  Tools not in the
        requested *tool_sets* are disabled (hidden from clients).

        An empty *tool_sets* (or a package that declares no toolsets in its
        ``mcp.json``) exposes every introspected tool. Otherwise only tools
        listed in the named, namespaced toolsets (e.g. ``"slack_read"``) are
        exposed.

        Raises ``FileNotFoundError`` if ``mcp-tools.generated.json`` is absent.
        Run ``just gen`` to generate it.
        """
        from fastmcp.server.server import ToolTransformConfig

        all_tool_names = [t["name"] for t in self.tools]

        # Determine the allowed set of tool names. tool_sets entries are
        # always namespaced ("{pkg}_{toolset}") — bare names are no longer
        # accepted. An empty list (or no toolsets defined) means "expose
        # everything", which is how the proxy behaves with no filter.
        pkg_toolsets = self.toolsets
        if not tool_sets or not pkg_toolsets:
            allowed: set[str] | None = None  # expose everything
        else:
            bare = _bare_toolsets(self.name, set(pkg_toolsets.keys()), tool_sets)
            if not bare:
                allowed = set()  # no matching toolsets → expose nothing
            else:
                allowed = set()
                for ts in bare:
                    allowed.update(pkg_toolsets.get(ts, []))

        transforms: dict[str, ToolTransformConfig] = {}
        enabled_count = 0
        for name in all_tool_names:
            if allowed is None or name in allowed:
                exposed = name if not self.config.namespaced else f"{self.name}__{name}"
                transforms[name] = ToolTransformConfig(name=exposed)
                enabled_count += 1
            else:
                transforms[name] = ToolTransformConfig(enabled=False)

        logger.info(
            "Namespacing %d/%d tools for %s with prefix '%s__' (tool_sets=%s)",
            enabled_count,
            len(all_tool_names),
            self.name,
            self.name,
            tool_sets,
        )
        return transforms
