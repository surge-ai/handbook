"""Start the MCP proxy server.

Discovers MCP servers in a configurable packages directory: any package directory
containing an ``mcp.json`` file is treated as an MCP server.  This is
language/runtime agnostic — Python, Node, or any other executable can be
described by the config.

``mcp.json`` schema
-------------------
{
  "run": {                       // required: how to start the MCP server
    "command": "node",
    "args":    ["dist/index.js"],
    "env":     {}                // optional: extra env vars to inject into the subprocess
  },
  "setup": {                     // optional: run once before the server starts
    "command": "npm",
    "args":    ["run", "build"],
    "env":     {}
  },
  "secrets": ["BRAVE_API_KEY"]   // optional: credential-shaped vars to forward from the proxy
}

``run`` and ``setup`` share the same step format: a single step dict
``{command, args?, env?}`` or a list of such dicts for multi-step hooks
(most useful for ``setup``).

The top-level ``secrets`` is an opt-in for credential-shaped env vars. A credential-shaped
var not in here will not be forwarded to the subprocess.

Each service is started as an HTTP server on a dynamically assigned port.
The proxy communicates with services via StreamableHTTP transport on ``/mcp``
and reverse-proxies their viewer UIs through a tabbed interface on port 8000.

Access to service HTTP servers is restricted via a shared secret token
(``MCP_PROXY_TOKEN``) passed as an environment variable and required as an
``X-Proxy-Token`` header on all non-MCP requests.
"""

import atexit
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import FastMCPProxy, StatefulProxyClient
from fastmcp.server.server import ToolTransform

from mcp_proxy.service import McpConfig, McpService, _bare_toolsets

logger = logging.getLogger("mcp_proxy")

# Sentinel tag used to mark proxy-level tools that must be callable but hidden
# from tools/list — the proxy aggregate export_state / import_state are
# infrastructure for the grading harness, not for agents to discover and use.
HIDDEN_FROM_LISTING_TAG = "mcp_proxy:hidden_from_listing"

# ---------------------------------------------------------------------------
# Fake clock (--current-time)
# ---------------------------------------------------------------------------


def to_faketime_spec(current_time: str) -> str:
    """Convert an RFC3339/ISO-8601 timestamp to the absolute timestamp string
    the ``faketime`` wrapper expects, normalized to UTC.

    A naive timestamp (no tz offset) is assumed to be UTC. Raises ``ValueError``
    on an unparseable string. Combined with ``TZ=UTC`` in the service env, the
    returned ``"%Y-%m-%d %H:%M:%S"`` string anchors the faked clock to the
    intended instant and lets it advance at real wall-clock rate.
    """
    dt = datetime.fromisoformat(current_time)  # `Z` accepted on Python 3.11+
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def resolve_faketime_launch(current_time: str) -> tuple[list[str], dict[str, str]]:
    """Resolve ``--current-time`` into a command prefix and env overrides.

    Returns ``(["faketime", "<spec>"], {"TZ": "UTC"})``. Exits the process
    (``SystemExit``) if the timestamp is unparseable or the ``faketime`` wrapper
    is not installed — a fake clock that silently falls back to real time would
    break determinism, so we fail loudly instead.
    """
    try:
        spec = to_faketime_spec(current_time)
    except ValueError as exc:
        logger.error("Invalid --current-time %r: %s", current_time, exc)
        sys.exit(1)

    if shutil.which("faketime") is None:
        logger.error(
            "--current-time requires the 'faketime' wrapper, which is not on PATH. "
            "Install the faketime package (it ships the libfaketime .so)."
        )
        sys.exit(1)

    return ["faketime", spec], {"TZ": "UTC"}


# ---------------------------------------------------------------------------
# Crash / signal handlers
# ---------------------------------------------------------------------------


_child_processes: list[subprocess.Popen] = []


def install_crash_handlers() -> None:
    """Install handlers that log diagnostic info when the MCP server crashes."""

    def _excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error("Unhandled exception:\n%s", tb)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _exit_handler():
        logger.info("Process exiting (pid=%d)", os.getpid())
        _kill_child_processes()

    def _signal_handler(signum, _frame):
        logger.error("Received signal %s (%d)", signal.Signals(signum).name, signum)
        _kill_child_processes()
        os._exit(128 + signum)

    sys.excepthook = _excepthook
    atexit.register(_exit_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)


def _kill_child_processes() -> None:
    """Terminate all child service processes."""
    for proc in _child_processes:
        with contextlib.suppress(OSError):
            proc.terminate()
    for proc in _child_processes:
        try:
            proc.wait(timeout=5)
        except Exception:
            with contextlib.suppress(OSError):
                proc.kill()


def _validate_tool_sets(requested: list[str], packages_root: Path) -> None:
    """Validate that every requested tool_set is provided by some package's mcp.json.

    The valid universe is ``{<pkg>_<set>}`` over every ``packages/<pkg>/mcp.json``
    ``toolsets`` declaration — the same source the proxy uses to decide what to
    mount. Exits with an error if any requested name is unknown. (Each package's
    ``mcp.json`` is the source of truth; there is no separate ``metadata.jsonc``.)
    """
    valid_sets = {f"{svc.name}_{set_name}" for svc in _iter_packages(packages_root) for set_name in svc.toolsets}

    unknown = set(requested) - valid_sets
    if unknown:
        logger.error(
            "Unknown tool set(s): %s. Valid entries: %s",
            ", ".join(sorted(unknown)),
            ", ".join(sorted(valid_sets)),
        )
        sys.exit(1)


def _iter_packages(packages_root: Path) -> list[McpService]:
    """Return every ``mcp.json``-bearing package under *packages_root*.

    Used by ``gen`` (which needs to introspect every server) and as the
    discovery primitive shared with :func:`discover_mcp_servers`. Sorted by
    directory name for deterministic ordering.
    """
    if not packages_root.exists() or not packages_root.is_dir():
        logger.error("Packages root not found: %s", packages_root)
        sys.exit(1)

    result: list[McpService] = []
    for pkg_dir in sorted(packages_root.iterdir()):
        if not pkg_dir.is_dir():
            continue
        cfg_file = pkg_dir / "mcp.json"
        if not cfg_file.exists():
            continue
        try:
            data = json.loads(cfg_file.read_text())
            cfg = McpConfig.from_mcp_json(pkg_dir.resolve(), data)
        except Exception as e:
            logger.error("Could not parse %s: %s", cfg_file, e)
            sys.exit(1)
        result.append(McpService(cfg, child_processes=_child_processes))
    return result


def discover_mcp_servers(
    packages_root: Path,
    tool_sets: list[str] | None = None,
) -> list[McpService]:
    """Return packages under *packages_root* matching at least one of *tool_sets*.

    *tool_sets* must be a non-empty list of namespaced toolset names
    (e.g. ``"google_mail_read"``, ``"slack_state"``). ``None``, ``[]``, or a
    list containing only empty strings returns no servers — every consumer
    must declare its toolsets explicitly.

    Use :func:`_iter_packages` instead when you need every package
    (``gen`` does this).
    """
    if tool_sets is None:
        return []
    requested = [ts for ts in tool_sets if ts]
    if not requested:
        return []

    _validate_tool_sets(requested, packages_root)

    result: list[McpService] = []
    for svc in _iter_packages(packages_root):
        matched = _bare_toolsets(svc.name, set(svc.toolsets.keys()), requested)
        if matched:
            result.append(svc)

    _warn_core_and_compat_shim(result)
    return result


# ``syntara`` is the temporary backward-compat shim that forwards to ``core``
# (REMOVE after 2026-06-18). It and ``core`` expose the same underlying tools
# under different names; mounting both at once is redundant rather than fatal,
# since syntara is implemented on top of core, so we warn and let both run.
COMPAT_SHIM_NAME = "syntara"
COMPAT_SHIM_TARGET = "core"


def _warn_core_and_compat_shim(servers: list[McpService]) -> None:
    names = {svc.name for svc in servers}
    if COMPAT_SHIM_NAME in names and COMPAT_SHIM_TARGET in names:
        logger.warning(
            "requested tool sets include both '%s' and the legacy '%s' compatibility shim, "
            "which forwards to '%s'. Both will be mounted, exposing the same underlying tools "
            "under different names. Prefer a single surface: drop the legacy '%s_*' tool sets "
            "(preferred) or the '%s_*' ones in WORLDBENCH_TOOL_SETS.",
            COMPAT_SHIM_TARGET,
            COMPAT_SHIM_NAME,
            COMPAT_SHIM_TARGET,
            COMPAT_SHIM_NAME,
            COMPAT_SHIM_TARGET,
        )


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Find a free TCP port on localhost using OS assignment."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    """Wait until a service is listening on the given port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Subprocess environment
# ---------------------------------------------------------------------------


# Every env var from the proxy is forwarded *unless* its name contains a
# credential keyword.
_CREDENTIAL_KEYWORDS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "PASSPHRASE",
    "CREDENTIAL",
    "AUTH",
    "BEARER",
    "JWT",
    "COOKIE",
    "OTP",
)


def _looks_like_credential(name: str) -> bool:
    """True if the env var's name contains any credential keyword (case-insensitive)."""
    upper = name.upper()
    return any(kw in upper for kw in _CREDENTIAL_KEYWORDS)


_CRED_URL_RE = re.compile(r"://[^/\s@]+@")


def _value_has_url_userinfo(value: str) -> bool:
    """True if *value* contains a URL-like substring with userinfo (``user@`` or ``user:pass@``).

    Catches credentials embedded in values whose names don't trip
    :func:`_looks_like_credential` — e.g. ``DATABASE_URL=postgres://u:p@host/db``,
    ``PIP_INDEX_URL=https://user:token@pypi.example.com/simple``, or
    ``GIT_REMOTE_URL=https://ghp_xxx@github.com/org/repo`` (token-as-username,
    no password).

    Uses a regex (not :func:`urllib.parse.urlsplit`) so nested URI schemes
    are also caught — ``://X@`` is the universal "URL userinfo" shape regardless
    of outer scheme.
    """
    return bool(_CRED_URL_RE.search(value))


def _build_subprocess_env(
    base_dir: Path,
    server_name: str,
    declared_secrets: Iterable[str] = (),
) -> dict[str, str]:
    """Return an environment dict to forward to one subprocess.

    Default-allow with a credential-name denylist: every var from the proxy's
    env is forwarded unless its name looks credential-shaped. Services that need
    a real credential must declare it by exact name in their mcp.json ``secrets`` field.

    WORLDBENCH_ROOT is set to ``base_dir`` for every subprocess.

    INPUTDIR keeps today's legacy contract: namespaced to
    ``<INPUTDIR>/<server_name>`` when set, else ``<base_dir>/<server_name>``.

    OUTPUTDIR is namespaced per server (``<OUTPUTDIR or base_dir>/<server_name>``)
    so legacy ``initial.json`` / ``final.json`` snapshots stay isolated.

    BUNDLEDIR is the unified-bundle root (set by the parent process —
    ``scripts/start.sh`` for local dev, the production harness in production). Each
    service owns ``<BUNDLEDIR>/services/<own_name>/`` for input; the
    conventional single-file name is ``state.json`` but services with
    multi-file seeds glob additional JSON next to it. When unset,
    services fall back to the legacy INPUTDIR path. mcp_proxy doesn't
    namespace this env var — services append their own name themselves.

    BUNDLE_OUTPUT_DIR is namespaced per server
    (``<OUTPUTDIR or base_dir>/services/<server_name>``). Services write
    ``state.json`` directly here, so the on-disk layout becomes
    ``services/<name>/state.json`` in the output bundle. Input and output
    share the same per-service-subdir layout, so an output bundle
    round-trips cleanly as the next run's input bundle.

    BUNDLE_INPUT_DIR is the per-service bundle path
    (``<BUNDLEDIR>/services/<server_name>``, without trailing separator)
    — only set when ``BUNDLEDIR`` is present in the parent env. It resolves
    both bundle layouts transparently to the consuming service: in the new
    folder layout it is a real directory containing files like
    ``mysql.tar.zst``; in the legacy flat layout it is a path *prefix*
    whose siblings are named ``<server_name>.mysql.tar.zst``. A service
    can probe both ``"${BUNDLE_INPUT_DIR}/<name>"`` and
    ``"${BUNDLE_INPUT_DIR}.<name>"`` to find a bundle file without
    having to know its own server name.
    """
    declared = set(declared_secrets)
    env = {
        k: v
        for k, v in os.environ.items()
        if k in declared or (not _looks_like_credential(k) and not _value_has_url_userinfo(v))
    }

    output_root = Path(env.get("OUTPUTDIR", base_dir))
    input_dir = Path(env.get("INPUTDIR", base_dir)) / server_name
    output_dir = output_root / server_name
    bundle_output_dir = output_root / "services" / server_name
    bundle_root = env.get("BUNDLEDIR")
    bundle_input_dir = Path(bundle_root) / "services" / server_name if bundle_root else None
    # INPUTDIR may live on a read-only mount or not have a seed dir for every
    # server (e.g. core has no seed state). Servers should handle a missing
    # INPUTDIR themselves; don't fail the proxy on mkdir.
    try:
        input_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("[%s] INPUTDIR mkdir skipped (%s): %s", server_name, exc, input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_output_dir.mkdir(parents=True, exist_ok=True)

    env["WORLDBENCH_ROOT"] = str(base_dir)
    env["INPUTDIR"] = str(input_dir)
    env["OUTPUTDIR"] = str(output_dir)
    env["BUNDLE_OUTPUT_DIR"] = str(bundle_output_dir)
    if bundle_input_dir is not None:
        env["BUNDLE_INPUT_DIR"] = str(bundle_input_dir)

    logger.info(
        "[%s] INPUTDIR=%s OUTPUTDIR=%s BUNDLE_OUTPUT_DIR=%s BUNDLE_INPUT_DIR=%s",
        server_name,
        input_dir,
        output_dir,
        bundle_output_dir,
        bundle_input_dir if bundle_input_dir is not None else "<unset>",
    )

    return env


# ---------------------------------------------------------------------------
# Proxy setup
# ---------------------------------------------------------------------------


# Registry of running services: name → port
service_registry: dict[str, int] = {}
proxy_token: str = ""


class HideTaggedToolsMiddleware(Middleware):
    """Strip tools tagged with ``HIDDEN_FROM_LISTING_TAG`` from ``tools/list``.

    Hidden tools remain fully callable via ``tools/call`` — the grading harness
    relies on the un-namespaced ``export_state`` / ``import_state`` for snapshots —
    but they are removed from the listing the model sees, so an agent has no
    way to discover or invoke them on its own.
    """

    async def on_list_tools(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ):
        tools = await call_next(context)
        return [t for t in tools if HIDDEN_FROM_LISTING_TAG not in (t.tags or set())]


def _assert_local_tools_async(app: FastMCP) -> None:
    """Run the async-tool guard against only the proxy's *locally* registered tools.

    The aggregate proxy mounts child-server tools via ``add_provider`` — those
    arrive as ``ProxyTool`` (no ``.fn``) or ``ToolTransform``-wrapped
    ``TransformedTool`` instances, neither of which is a locally-defined
    ``FunctionTool``. Feeding them to ``find_sync_tools`` would either raise
    (ProxyTool has no ``fn`` field) or false-positive (a transform wrapper's
    ``fn`` is not the remote tool's coroutine). The pydantic-core concurrency
    panic the guard defends against only affects tools whose argument validation
    runs locally in the proxy — i.e. its own ``FunctionTool`` definitions
    (``export_state``, ``import_state``).
    So we scope the guard to ``FunctionTool`` instances and skip remote/proxied
    tools, while still failing for any local sync ``def`` tool.
    """
    from fastmcp.tools.function_tool import FunctionTool

    from mcp_proxy.async_tool_guard import assert_tools_async

    async def _local_tools() -> list:
        local = []
        for descriptor in await app.list_tools():
            tool = await app.get_tool(descriptor.name)
            if isinstance(tool, FunctionTool):
                local.append(tool)
        return local

    import asyncio

    tools = asyncio.run(_local_tools())

    class _LocalView:
        async def list_tools(self):
            return tools

        async def get_tool(self, name: str):
            return next(t for t in tools if t.name == name)

    assert_tools_async(_LocalView())


def build_proxy_app(
    packages_root: Path,
    base_dir: Path,
    run_setup_hooks: bool = False,
    tool_sets: list[str] | None = None,
    command_prefix: list[str] | None = None,
    service_env_overrides: dict[str, str] | None = None,
) -> FastMCP:
    """Discover sub-servers, start them as HTTP processes, and return
    an aggregating FastMCP proxy app.

    Also populates the global ``service_registry`` with name → port mappings
    and sets ``proxy_token`` for viewer authentication.

    *command_prefix* and *service_env_overrides*, when set, wrap and augment
    each service's launch — used by ``--current-time`` to run services under
    the ``faketime`` wrapper with ``TZ=UTC``.
    """
    global proxy_token

    proxy_token = secrets.token_hex(32)

    configs = discover_mcp_servers(packages_root, tool_sets=tool_sets)
    if not configs:
        logger.error("No MCP servers found under %s", packages_root)
        sys.exit(1)

    app = FastMCP("mcp-proxy")
    app.add_middleware(HideTaggedToolsMiddleware())
    pending: list[tuple[McpService, int, subprocess.Popen]] = []
    startup_failures: list[str] = []

    for cfg in configs:
        env = _build_subprocess_env(base_dir, server_name=cfg.name, declared_secrets=cfg.secrets)

        if not cfg.run_install(env):
            logger.error("Install failed for %s, aborting", cfg.name)
            sys.exit(1)

        if run_setup_hooks and not cfg.run_setup(env):
            logger.error("Setup failed for %s, aborting", cfg.name)
            sys.exit(1)

        if not cfg.run_pre_steps(env):
            logger.error("Pre-run step failed for %s, aborting", cfg.name)
            sys.exit(1)

        # Apply the fake clock only to the long-running service (and its
        # children, e.g. the syntara bash/python sandboxes) — not to the
        # install/setup/pre-run hooks above.
        service_env = {**env, **service_env_overrides} if service_env_overrides else env

        port = _find_free_port()
        proc = cfg.start_service(service_env, port, proxy_token, command_prefix=command_prefix)
        if proc is None:
            startup_failures.append(f"{cfg.name} (failed to launch process)")
            continue

        pending.append((cfg, port, proc))

    # Wait for all services to become ready, then mount them
    for cfg, port, proc in pending:
        if proc.poll() is not None:
            logger.error("%s exited immediately with code %d", cfg.name, proc.returncode)
            startup_failures.append(f"{cfg.name} (exited immediately with code {proc.returncode})")
            continue

        if not _wait_for_port(port, timeout=cfg.config.startup_timeout):
            logger.error(
                "%s did not start listening on port %d within %.0fs", cfg.name, port, cfg.config.startup_timeout
            )
            proc.terminate()
            startup_failures.append(
                f"{cfg.name} (did not start listening on port {port} within {cfg.config.startup_timeout:.0f}s)"
            )
            continue

        logger.info("Mounting %s (HTTP on port %d)", cfg.name, port)

        transport = StreamableHttpTransport(
            f"http://127.0.0.1:{port}/mcp",
            headers={"X-Proxy-Token": proxy_token},
        )
        client = StatefulProxyClient(transport)
        proxy_server = FastMCPProxy(
            client_factory=client.new_stateful,
            name=cfg.name,
        )

        transforms = cfg.resolve_tool_transforms(tool_sets or [])

        from fastmcp.server.providers.fastmcp_provider import FastMCPProvider

        provider = FastMCPProvider(proxy_server)
        provider = provider.wrap_transform(ToolTransform(transforms))
        app.add_provider(provider, namespace="")

        service_registry[cfg.name] = port

    if startup_failures:
        logger.error(
            "Aborting startup because requested MCP services failed to start: %s",
            ", ".join(startup_failures),
        )
        sys.exit(1)

    _register_aggregate_state_tools(app, configs)

    return app


def _aggregate_state_schema(configs: list[McpService]) -> dict:
    """Assemble the proxy-level import_state schema from each mounted server.

    Pulls the ``state`` property schema out of each server's ``import_state``
    tool so the aggregate schema reflects only the currently-mounted services
    (not every package on disk). Produces an object schema keyed by server
    name, giving clients a precise view of the aggregate shape rather than a
    generic ``{}``.
    """
    properties: dict[str, dict] = {}
    for cfg in configs:
        for tool in cfg.tools:
            if tool.get("name") != "import_state":
                continue
            state_schema = (tool.get("inputSchema") or {}).get("properties", {}).get("state")
            if state_schema is not None:
                properties[cfg.name] = state_schema
            break
    return {
        "type": "object",
        "description": "Aggregate state keyed by server name.",
        "properties": properties,
    }


def _register_aggregate_state_tools(app: FastMCP, configs: list[McpService]) -> None:
    """Register the proxy's own ``export_state`` / ``import_state`` tools.

    These aggregate every mounted sub-server's state into a single JSON object
    keyed by server name, enabling round-trip snapshots across the full proxy.
    """
    from fastmcp.client import Client
    from mcp.types import ToolAnnotations

    aggregate_schema = _aggregate_state_schema(configs)

    async def _call_downstream(name: str, tool: str, args: dict) -> dict | None:
        port = service_registry.get(name)
        if port is None:
            raise ValueError(f"service '{name}' is not mounted on this proxy")
        transport = StreamableHttpTransport(
            f"http://127.0.0.1:{port}/mcp",
            headers={"X-Proxy-Token": proxy_token},
        )
        async with Client(transport) as client:
            result = await client.call_tool(tool, args)
        if not result.content:
            return None
        text = getattr(result.content[0], "text", None)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}

    @app.tool(
        name="export_state",
        description=(
            "Export the aggregate state of every mounted MCP server as one JSON "
            "object keyed by server name. Round-trips with import_state."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
        tags={HIDDEN_FROM_LISTING_TAG},
    )
    async def export_state() -> dict:
        result: dict = {}
        for name in sorted(service_registry):
            result[name] = await _call_downstream(name, "export_state", {})
        return result

    @app.tool(
        name="import_state",
        description=(
            "Replace the state of every mounted MCP server from one JSON object "
            "keyed by server name. Servers omitted from the input are left "
            "untouched. Round-trips with export_state."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
        tags={HIDDEN_FROM_LISTING_TAG},
    )
    async def import_state(state: dict) -> dict:
        unknown = [n for n in state if n not in service_registry]
        if unknown:
            raise ValueError(f"unknown server(s): {', '.join(sorted(unknown))}")
        for name, sub_state in state.items():
            await _call_downstream(name, "import_state", {"state": sub_state})
        return {"ok": True}

    # Attach the assembled schema so clients see the per-server state shape.
    # FastMCP doesn't expose a public API for overriding an already-registered
    # tool's parameters, so we reach into the tool manager carefully. If the
    # internal layout changes, we silently skip and keep the generic schema.
    try:
        tool_manager = getattr(app, "_tool_manager", None)
        registered = getattr(tool_manager, "_tools", {}) if tool_manager else {}
        import_tool = registered.get("import_state")
        if import_tool is not None:
            import_tool.parameters = {
                "type": "object",
                "properties": {"state": aggregate_schema},
                "required": ["state"],
            }
    except Exception:
        logger.debug("Could not override proxy import_state schema", exc_info=True)


# ---------------------------------------------------------------------------
# Viewer HTTP server
# ---------------------------------------------------------------------------


def _start_viewer_server(host: str, port: int) -> None:
    """Start the viewer reverse-proxy HTTP server in a background thread."""
    import uvicorn

    from mcp_proxy.viewer import create_viewer_app

    viewer_app = create_viewer_app(service_registry, proxy_token)

    def _run():
        uvicorn.run(
            viewer_app,
            host=host,
            port=port,
            log_level="warning",
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    print(
        f"[MCP_PROXY] Viewer server started on http://{host}:{port}/",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _read_build_sha() -> str:
    """Return the build SHA stamped into the image, or 'dev' for local runs.

    The Dockerfile writes ``/app/.git-sha`` from the ``GIT_SHA`` build arg.
    Logged at startup and surfaced via ``/health`` so an out-of-date image
    can be identified without inspecting the host.
    """
    for candidate in (Path("/app/.git-sha"), Path(".git-sha")):
        try:
            sha = candidate.read_text().strip()
        except OSError:
            continue
        if sha:
            return sha
    return "dev"


def run(method: str | None = None, port: int | None = None, current_time: str | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(message)s",
        stream=sys.stderr,
    )

    install_crash_handlers()

    build_sha = _read_build_sha()
    logger.info("build %s", build_sha)

    base_dir = Path(os.environ.get("WORLDBENCH_ROOT", os.getcwd())).resolve()
    packages_root = Path(os.environ.get("WORLDBENCH_PACKAGES_ROOT") or (base_dir / "packages"))
    tool_sets = os.environ.get("WORLDBENCH_TOOL_SETS", "").split()
    method = method or os.environ.get("WORLDBENCH_METHOD", "stdio")
    viewer_port = os.environ.get("VIEWER_PORT")
    mcp_port = port or int(os.environ.get("PORT", "8000"))

    command_prefix: list[str] | None = None
    service_env_overrides: dict[str, str] | None = None
    # `is not None` (not truthiness): an explicitly-passed empty --current-time
    # is a bad value that must fail loudly, not silently fall back to real time.
    if current_time is not None:
        command_prefix, service_env_overrides = resolve_faketime_launch(current_time)
        logger.info("Faking clock for all services: faketime %s (TZ=UTC)", command_prefix[1])

    app = build_proxy_app(
        packages_root=packages_root,
        base_dir=base_dir,
        run_setup_hooks=True,
        tool_sets=tool_sets,
        command_prefix=command_prefix,
        service_env_overrides=service_env_overrides,
    )

    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @app.custom_route("/health", methods=["GET"])
    async def _health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "build": build_sha,
                "services": sorted(service_registry.keys()),
            }
        )

    _assert_local_tools_async(app)

    if viewer_port:
        _start_viewer_server("0.0.0.0", int(viewer_port))

    try:
        if method in ("http", "sse"):
            transport = "sse" if method == "sse" else "streamable-http"
            app.run(
                transport=transport,
                port=mcp_port,
                host="0.0.0.0",
                path="/mcp",
                show_banner=False,
            )
        else:
            app.run(show_banner=False)
    except Exception:
        logger.exception("app.run() raised an unhandled exception")
        raise

    _kill_child_processes()
