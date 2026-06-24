"""Generate per-package mcp-tools.generated.json files.

Discovers MCP servers the same way the ``mcp`` and ``setup`` commands do
(WORLDBENCH_ROOT, WORLDBENCH_PACKAGES_ROOT), spawns each server in HTTP mode to
introspect its tools, and writes one file per package:

  - packages/<name>/mcp-tools.generated.json   (tools + toolset membership)

This per-package file is the single committed source of truth: the runtime
proxy reads it to mount tools and validate requested tool sets, and
``scripts/collect_docker_images.py`` reads it to build the per-image toolset
artifacts. There is no root aggregate / metadata.jsonc / toolsets/ tree — they
were redundant re-groupings of this same data.

Usage:
    mcp-proxy gen
"""

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from mcp_proxy.commands.mcp import (
    _find_free_port,
    _iter_packages,
    _wait_for_port,
)
from mcp_proxy.service import McpService, resolve_command

# ---------------------------------------------------------------------------
# Per-package tool introspection
# ---------------------------------------------------------------------------


async def _list_tools_http(cfg: McpService, env: dict[str, str]) -> list[dict]:
    """Spawn the server in HTTP mode and return its tool list.

    Prefers the dedicated ``gen`` entrypoint (a lightweight tool-listing
    process that doesn't require the package's full install) and falls back
    to the last ``run`` step when no ``gen`` is declared.
    """
    port = _find_free_port()
    proxy_token = secrets.token_hex(16)

    server_step = cfg.config.gen[-1] if cfg.config.gen else cfg.config.run[-1]
    run_args = [resolve_command(server_step.command), *server_step.args]
    proc_env = {
        **env,
        **server_step.env,
        "PORT": str(port),
        "MCP_PROXY_TOKEN": proxy_token,
    }

    proc = await asyncio.create_subprocess_exec(
        *run_args,
        cwd=str(cfg.cwd),
        env=proc_env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        if proc.returncode is not None:
            raise RuntimeError(f"Server exited immediately (code {proc.returncode})")
        if not _wait_for_port(port):
            raise RuntimeError(f"Server did not start listening on port {port} within 30 s")

        transport = StreamableHttpTransport(
            f"http://127.0.0.1:{port}/mcp",
            headers={"X-Proxy-Token": proxy_token},
        )
        async with Client(transport) as client:
            tools = await client.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema,
                    **({"outputSchema": t.outputSchema} if t.outputSchema else {}),
                }
                for t in tools
            ]
    finally:
        proc.terminate()
        await proc.wait()


REQUIRED_STATE_TOOLS = ("export_state", "import_state")


def _gen_pkg(cfg: McpService, env: dict[str, str]) -> tuple[list[dict], dict[str, list[str]]]:
    """Introspect one package and write its mcp-tools.generated.json.

    Returns ``(tools, toolsets)`` with unnamespaced tool names.
    """
    print(f"[GEN] Spawning {cfg.name} (HTTP mode) to list tools…", file=sys.stderr)
    tools = asyncio.run(_list_tools_http(cfg, env))
    tools.sort(key=lambda t: t["name"])

    tool_names = {t["name"] for t in tools}
    missing = [t for t in REQUIRED_STATE_TOOLS if t not in tool_names]
    if missing:
        print(
            f"[GEN] ERROR: {cfg.name} does not expose required state tool(s): {', '.join(missing)}. "
            "Every MCP server must implement export_state and import_state.",
            file=sys.stderr,
        )
        sys.exit(1)

    toolsets = cfg.toolsets

    output_path = cfg.package_dir / "mcp-tools.generated.json"
    output_path.write_text(json.dumps({"schema_version": "v1", "tools": tools, "toolsets": toolsets}, indent=2) + "\n")
    print(f"[GEN] Generated {output_path} ({len(tools)} tools)", file=sys.stderr)

    return tools, toolsets


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run() -> None:
    base_dir = Path(os.environ.get("WORLDBENCH_ROOT", os.getcwd())).resolve()
    packages_root = Path(os.environ.get("WORLDBENCH_PACKAGES_ROOT") or (base_dir / "packages"))

    # gen always introspects every package, regardless of WORLDBENCH_TOOL_SETS.
    configs = _iter_packages(packages_root)
    if not configs:
        print(f"[GEN] No MCP servers found under {packages_root}", file=sys.stderr)
        sys.exit(1)

    env = {
        **os.environ,
        "WORLDBENCH_ROOT": str(base_dir),
    }

    for cfg in configs:
        _gen_pkg(cfg, env)

    print(f"[GEN] Generated per-package mcp-tools.generated.json for {len(configs)} servers", file=sys.stderr)
