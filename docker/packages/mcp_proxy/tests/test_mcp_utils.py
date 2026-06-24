"""Tests for utility functions in commands/mcp.py."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from fastmcp import FastMCP

from mcp_proxy.commands import mcp as mcp_command
from mcp_proxy.commands.mcp import (
    HIDDEN_FROM_LISTING_TAG,
    HideTaggedToolsMiddleware,
    _find_free_port,
    _iter_packages,
    _validate_tool_sets,
    _wait_for_port,
    build_proxy_app,
    discover_mcp_servers,
)
from mcp_proxy.service import McpConfig, McpService, resolve_command

# ---------------------------------------------------------------------------
# _find_free_port
# ---------------------------------------------------------------------------


class TestFindFreePort:
    def test_returns_int(self):
        port = _find_free_port()
        assert isinstance(port, int)

    def test_port_is_in_valid_range(self):
        port = _find_free_port()
        assert 1024 <= port <= 65535

    def test_returns_different_ports(self):
        ports = {_find_free_port() for _ in range(5)}
        # At least 2 distinct ports (extremely likely)
        assert len(ports) >= 2


# ---------------------------------------------------------------------------
# _wait_for_port
# ---------------------------------------------------------------------------


class TestWaitForPort:
    def test_returns_true_when_port_is_listening(self):
        # Start a simple server
        server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            assert _wait_for_port(port, timeout=5.0) is True
        finally:
            server.server_close()

    def test_returns_false_when_port_not_listening(self):
        # Find a free port and don't listen on it
        port = _find_free_port()
        assert _wait_for_port(port, timeout=0.5) is False


# ---------------------------------------------------------------------------
# McpConfig & discover_mcp_servers
# ---------------------------------------------------------------------------


class TestMcpConfig:
    def test_basic_config(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/my-service"),
            {"run": {"command": "node", "args": ["index.js"]}},
        )
        assert cfg.name == "my-service"
        assert len(cfg.run) == 1
        assert cfg.run[0].command == "node"
        assert cfg.run[0].args == ["index.js"]
        assert cfg.setup == []

    def test_run_list(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/svc"),
            {"run": [{"command": "build"}, {"command": "node", "args": ["index.js"]}]},
        )
        assert len(cfg.run) == 2
        assert cfg.run[0].command == "build"
        assert cfg.run[1].command == "node"

    def test_missing_run_raises(self):
        with pytest.raises(ValueError, match="'run' is required"):
            McpConfig.from_mcp_json(Path("/tmp/svc"), {})

    def test_custom_name(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/my-service"),
            {"name": "Custom", "run": {"command": "python"}},
        )
        assert cfg.name == "Custom"

    def test_setup_single_dict(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/svc"),
            {
                "run": {"command": "node"},
                "setup": {"command": "npm", "args": ["run", "build"]},
            },
        )
        assert len(cfg.setup) == 1
        assert cfg.setup[0].command == "npm"

    def test_setup_list(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/svc"),
            {
                "run": {"command": "node"},
                "setup": [{"command": "a"}, {"command": "b"}],
            },
        )
        assert len(cfg.setup) == 2

    def test_cwd_override(self, tmp_path):
        cfg = McpConfig.from_mcp_json(
            tmp_path / "svc",
            {"run": {"command": "node"}, "cwd": "subdir"},
        )
        assert cfg.cwd == (tmp_path / "svc" / "subdir").resolve()

    def test_install_single_dict(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/svc"),
            {
                "run": {"command": "node"},
                "install": {"command": "uv", "args": ["sync", "--package", "foo"]},
            },
        )
        assert len(cfg.install) == 1
        assert cfg.install[0].command == "uv"
        assert cfg.install[0].args == ["sync", "--package", "foo"]

    def test_install_defaults_empty(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/svc"),
            {"run": {"command": "node"}},
        )
        assert cfg.install == []

    def test_toolsets_parsed(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/svc"),
            {
                "run": {"command": "node"},
                "toolsets": {"read": ["t1", "t2"], "write": ["t3"]},
            },
        )
        assert cfg.toolsets == {"read": ["t1", "t2"], "write": ["t3"]}

    def test_toolsets_defaults_empty(self):
        cfg = McpConfig.from_mcp_json(
            Path("/tmp/svc"),
            {"run": {"command": "node"}},
        )
        assert cfg.toolsets == {}


class TestIterPackages:
    """Iteration is the discovery primitive — it returns every package
    regardless of any tool_sets filter. ``gen`` uses this directly."""

    def test_iter_packages_returns_each(self, tmp_path):
        pkg = tmp_path / "svc1"
        pkg.mkdir()
        (pkg / "mcp.json").write_text(json.dumps({"run": {"command": "node"}}))

        servers = _iter_packages(tmp_path)
        assert len(servers) == 1
        assert servers[0].name == "svc1"

    def test_exits_on_missing_run_command(self, tmp_path):
        pkg = tmp_path / "bad"
        pkg.mkdir()
        (pkg / "mcp.json").write_text(json.dumps({"run": {}}))

        with pytest.raises(SystemExit):
            _iter_packages(tmp_path)

    def test_exits_on_invalid_json(self, tmp_path):
        pkg = tmp_path / "broken"
        pkg.mkdir()
        (pkg / "mcp.json").write_text("not json")

        with pytest.raises(SystemExit):
            _iter_packages(tmp_path)

    def test_exits_on_nonexistent_root(self, tmp_path):
        with pytest.raises(SystemExit):
            _iter_packages(tmp_path / "nope")

    def test_sorted_by_name(self, tmp_path):
        for name in ("zebra", "alpha", "middle"):
            pkg = tmp_path / name
            pkg.mkdir()
            (pkg / "mcp.json").write_text(json.dumps({"run": {"command": "python"}}))

        servers = _iter_packages(tmp_path)
        names = [s.name for s in servers]
        assert names == ["alpha", "middle", "zebra"]


class TestDiscoverMcpServers:
    def test_filter_by_namespaced_toolset(self, tmp_path):
        """Only servers declaring the requested namespaced toolset are returned."""
        (tmp_path / "alpha").mkdir()
        (tmp_path / "alpha" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"read": ["t1"]}})
        )
        (tmp_path / "beta").mkdir()
        (tmp_path / "beta" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"write": ["t2"]}})
        )
        (tmp_path / "gamma").mkdir()
        (tmp_path / "gamma" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"read": ["t3"]}})
        )

        servers = discover_mcp_servers(tmp_path, tool_sets=["alpha_read", "gamma_read"])
        assert [s.name for s in servers] == ["alpha", "gamma"]

    def test_no_filter_returns_nothing(self, tmp_path):
        """tool_sets=None / [] / [""] all mean 'no servers' — callers must opt in
        explicitly. Use _iter_packages when you need every package."""
        for name in ("alpha", "beta"):
            pkg = tmp_path / name
            pkg.mkdir()
            (pkg / "mcp.json").write_text(json.dumps({"run": {"command": "node"}}))

        assert discover_mcp_servers(tmp_path, tool_sets=None) == []
        assert discover_mcp_servers(tmp_path, tool_sets=[]) == []
        assert discover_mcp_servers(tmp_path, tool_sets=[""]) == []

    def test_filter_multiple_namespaced_toolsets(self, tmp_path):
        """Servers declaring any of the requested namespaced toolsets are included."""
        (tmp_path / "alpha").mkdir()
        (tmp_path / "alpha" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"read": ["t1"]}})
        )
        (tmp_path / "beta").mkdir()
        (tmp_path / "beta" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"write": ["t2"]}})
        )
        (tmp_path / "gamma").mkdir()
        (tmp_path / "gamma" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"debug": ["t3"]}})
        )

        servers = discover_mcp_servers(tmp_path, tool_sets=["alpha_read", "beta_write"])
        assert [s.name for s in servers] == ["alpha", "beta"]

    def test_filter_namespaced_toolset(self, tmp_path):
        """Namespaced form 'pkg_toolset' selects only that package+toolset."""
        (tmp_path / "google_mail").mkdir()
        (tmp_path / "google_mail" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"read": ["t1"], "write": ["t2"]}})
        )
        (tmp_path / "slack").mkdir()
        (tmp_path / "slack" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"read": ["t3"], "write": ["t4"]}})
        )
        (tmp_path / "jira").mkdir()
        (tmp_path / "jira" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}})  # no toolsets
        )

        # Only google_mail should be found; slack has no google_mail_read toolset
        servers = discover_mcp_servers(tmp_path, tool_sets=["google_mail_read"])
        assert [s.name for s in servers] == ["google_mail"]

    def test_returns_mcp_service_objects(self, tmp_path):
        """discover_mcp_servers returns McpService instances wrapping McpConfig."""
        pkg = tmp_path / "svc"
        pkg.mkdir()
        (pkg / "mcp.json").write_text(json.dumps({"run": {"command": "node"}, "toolsets": {"read": ["t"]}}))

        servers = discover_mcp_servers(tmp_path, tool_sets=["svc_read"])
        assert len(servers) == 1
        assert isinstance(servers[0], McpService)
        assert isinstance(servers[0].config, McpConfig)

    def test_bare_package_name_is_rejected(self, tmp_path):
        """Bare package names are not valid tool_sets — they must be namespaced
        toolset names (``google_mail_read``), so validation rejects them."""
        (tmp_path / "google_mail").mkdir()
        (tmp_path / "google_mail" / "mcp.json").write_text(
            json.dumps({"run": {"command": "node"}, "toolsets": {"read": ["t1"]}})
        )
        # "google_mail" is a package name, not a toolset name — unknown tool set.
        with pytest.raises(SystemExit):
            discover_mcp_servers(tmp_path, tool_sets=["google_mail"])

    def _core_and_shim(self, tmp_path):
        """Create fake ``core`` and ``syntara`` (compat shim) packages."""
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "mcp.json").write_text(
            json.dumps({"run": {"command": "python"}, "toolsets": {"read": ["readFile"]}})
        )
        (tmp_path / "syntara").mkdir()
        (tmp_path / "syntara" / "mcp.json").write_text(
            json.dumps({"run": {"command": "python"}, "toolsets": {"read": ["readFile"]}})
        )

    def test_core_and_syntara_shim_together_warns(self, tmp_path, caplog):
        """core + the syntara compat shim are redundant but allowed — selecting both
        mounts both surfaces and warns. REMOVE with the syntara package after 2026-06-18."""
        self._core_and_shim(tmp_path)
        with caplog.at_level(logging.WARNING, logger="mcp_proxy"):
            servers = discover_mcp_servers(tmp_path, tool_sets=["core_read", "syntara_read"])
        assert sorted(s.name for s in servers) == ["core", "syntara"]
        assert any("both 'core' and the legacy 'syntara'" in r.message for r in caplog.records)

    def test_core_alone_is_allowed(self, tmp_path):
        self._core_and_shim(tmp_path)
        assert [s.name for s in discover_mcp_servers(tmp_path, tool_sets=["core_read"])] == ["core"]

    def test_syntara_shim_alone_is_allowed(self, tmp_path):
        self._core_and_shim(tmp_path)
        assert [s.name for s in discover_mcp_servers(tmp_path, tool_sets=["syntara_read"])] == ["syntara"]


class TestValidateToolSets:
    @staticmethod
    def _write_pkg(root, name, toolsets):
        """Materialize a minimal ``packages/<name>/mcp.json`` declaring *toolsets*."""
        pkg = root / name
        pkg.mkdir()
        (pkg / "mcp.json").write_text(json.dumps({"run": {"command": "node"}, "toolsets": toolsets}))

    def test_valid_tool_sets_pass(self, tmp_path):
        """Namespaced tool set names that a package declares do not raise."""
        self._write_pkg(tmp_path, "google_mail", {"read": []})
        self._write_pkg(tmp_path, "slack", {"state": []})
        _validate_tool_sets(["google_mail_read", "slack_state"], tmp_path)

    def test_unknown_tool_set_exits(self, tmp_path):
        """Unknown tool set names cause sys.exit."""
        self._write_pkg(tmp_path, "slack", {"read": []})
        with pytest.raises(SystemExit):
            _validate_tool_sets(["nonexistent"], tmp_path)

    def test_partial_unknown_exits(self, tmp_path):
        """Even one unknown tool set among valid ones causes exit."""
        self._write_pkg(tmp_path, "slack", {"read": []})
        with pytest.raises(SystemExit):
            _validate_tool_sets(["slack_read", "bad_name"], tmp_path)

    def test_empty_request_passes(self, tmp_path):
        """No requested tool sets is vacuously valid (nothing unknown)."""
        self._write_pkg(tmp_path, "slack", {"read": []})
        _validate_tool_sets([], tmp_path)  # should not raise


class TestBuildProxyApp:
    def test_exits_when_selected_service_fails_startup(self, tmp_path, monkeypatch, caplog):
        """Requested services that die during startup abort the whole proxy."""

        class FakeProc:
            returncode = 7

            def poll(self):
                return self.returncode

            def terminate(self):
                pass

        class FakeService:
            name = "jira"
            secrets: ClassVar[list[str]] = []
            config = SimpleNamespace(startup_timeout=0.1)

            def run_install(self, env):
                return True

            def run_setup(self, env):
                return True

            def run_pre_steps(self, env):
                return True

            def start_service(self, env, port, proxy_token, command_prefix=None):
                return FakeProc()

        monkeypatch.setattr(
            "mcp_proxy.commands.mcp.discover_mcp_servers",
            lambda packages_root, tool_sets=None: [FakeService()],
        )
        monkeypatch.setattr(
            "mcp_proxy.commands.mcp._build_subprocess_env",
            lambda base_dir, server_name, declared_secrets=(): {},
        )

        with pytest.raises(SystemExit):
            build_proxy_app(tmp_path, tmp_path, tool_sets=["jira_read"])

        assert "Aborting startup because requested MCP services failed to start" in caplog.text
        assert "jira (exited immediately with code 7)" in caplog.text


class TestRunViewerStartup:
    class FakeApp:
        def __init__(self):
            self.run_calls = []

        def custom_route(self, *_args, **_kwargs):
            def decorator(fn):
                return fn

            return decorator

        def run(self, **kwargs):
            self.run_calls.append(kwargs)

    def _stub_proxy_run(self, monkeypatch, tmp_path):
        app = self.FakeApp()
        monkeypatch.setenv("WORLDBENCH_ROOT", str(tmp_path))
        monkeypatch.setenv("WORLDBENCH_PACKAGES_ROOT", str(tmp_path))
        monkeypatch.delenv("WORLDBENCH_TOOL_SETS", raising=False)
        monkeypatch.delenv("PORT", raising=False)
        monkeypatch.setattr(mcp_command, "install_crash_handlers", lambda: None)
        monkeypatch.setattr(mcp_command, "build_proxy_app", lambda **_kwargs: app)
        monkeypatch.setattr(mcp_command, "_assert_local_tools_async", lambda _app: None)
        monkeypatch.setattr(mcp_command, "_kill_child_processes", lambda: None)
        return app

    def test_does_not_start_viewer_when_viewer_port_is_unset(self, tmp_path, monkeypatch):
        app = self._stub_proxy_run(monkeypatch, tmp_path)
        viewer_calls = []
        monkeypatch.delenv("VIEWER_PORT", raising=False)
        monkeypatch.setattr(mcp_command, "_start_viewer_server", lambda host, port: viewer_calls.append((host, port)))

        mcp_command.run(method="stdio")

        assert viewer_calls == []
        assert app.run_calls == [{"show_banner": False}]

    def test_starts_viewer_when_viewer_port_is_set(self, tmp_path, monkeypatch):
        app = self._stub_proxy_run(monkeypatch, tmp_path)
        viewer_calls = []
        monkeypatch.setenv("VIEWER_PORT", "8765")
        monkeypatch.setattr(mcp_command, "_start_viewer_server", lambda host, port: viewer_calls.append((host, port)))

        mcp_command.run(method="stdio")

        assert viewer_calls == [("0.0.0.0", 8765)]
        assert app.run_calls == [{"show_banner": False}]


class TestHideTaggedToolsMiddleware:
    """The proxy's un-namespaced ``export_state`` / ``import_state`` are
    infrastructure for the grading harness: they must remain callable so
    snapshots round-trip, but they must NOT appear in ``tools/list`` (an
    agent shouldn't even know they exist).
    """

    @pytest.mark.anyio
    async def test_tagged_tools_are_hidden_from_list_but_callable(self):
        app = FastMCP("test")
        app.add_middleware(HideTaggedToolsMiddleware())

        @app.tool(name="visible")
        async def visible() -> dict:
            return {"v": True}

        @app.tool(name="hidden", tags={HIDDEN_FROM_LISTING_TAG})
        async def hidden() -> dict:
            return {"h": True}

        listed = {t.name for t in await app.list_tools()}
        assert listed == {"visible"}, f"hidden tool leaked into list_tools: {listed}"

        # Hidden tool must still be callable — that's the point.
        hidden_result = await app.call_tool("hidden", {})
        assert hidden_result.structured_content == {"h": True}

        visible_result = await app.call_tool("visible", {})
        assert visible_result.structured_content == {"v": True}

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"


class TestMcpServiceLifecycle:
    @pytest.fixture
    def env(self):
        """Return a minimal env dict with PATH so subprocesses can find python."""
        import os

        return dict(os.environ)

    def test_run_install_executes_steps(self, tmp_path, env):
        """Install steps are executed and create side effects."""
        marker = tmp_path / "installed.txt"
        cfg = McpConfig.from_mcp_json(
            tmp_path,
            {
                "run": {"command": "python", "args": ["-c", "pass"]},
                "install": {"command": "python", "args": ["-c", f"open('{marker}', 'w').write('ok')"]},
            },
        )
        svc = McpService(cfg)
        assert svc.run_install(env) is True
        assert marker.read_text() == "ok"

    def test_run_install_returns_true_when_empty(self, tmp_path, env):
        """No install steps means success."""
        cfg = McpConfig.from_mcp_json(tmp_path, {"run": {"command": "python"}})
        svc = McpService(cfg)
        assert svc.run_install(env) is True

    def test_run_install_returns_false_on_failure(self, tmp_path, env):
        """Failed install step returns False."""
        cfg = McpConfig.from_mcp_json(
            tmp_path,
            {
                "run": {"command": "python"},
                "install": {"command": "python", "args": ["-c", "import sys; sys.exit(1)"]},
            },
        )
        svc = McpService(cfg)
        assert svc.run_install(env) is False

    def test_run_setup_executes_steps(self, tmp_path, env):
        """Setup steps are executed and create side effects."""
        marker = tmp_path / "setup.txt"
        cfg = McpConfig.from_mcp_json(
            tmp_path,
            {
                "run": {"command": "python"},
                "setup": {"command": "python", "args": ["-c", f"open('{marker}', 'w').write('done')"]},
            },
        )
        svc = McpService(cfg)
        assert svc.run_setup(env) is True
        assert marker.read_text() == "done"

    def test_run_setup_forwards_env(self, tmp_path, env):
        """Environment variables are forwarded to setup steps."""
        marker = tmp_path / "env.txt"
        env["MY_VAR"] = "hello"
        cfg = McpConfig.from_mcp_json(
            tmp_path,
            {
                "run": {"command": "python"},
                "setup": {
                    "command": "python",
                    "args": ["-c", f"import os; open('{marker}', 'w').write(os.environ.get('MY_VAR', 'MISSING'))"],
                },
            },
        )
        svc = McpService(cfg)
        assert svc.run_setup(env) is True
        assert marker.read_text() == "hello"

    def test_python_steps_use_proxy_interpreter_not_path(self, tmp_path, env):
        # Empty PATH: a bare `python` would be unfindable, so success proves the
        # step ran the absolute sys.executable (the PATH-elimination contract).
        import sys

        marker = tmp_path / "interp.txt"
        env["PATH"] = ""
        cfg = McpConfig.from_mcp_json(
            tmp_path,
            {
                "run": {"command": "python"},
                "setup": {
                    "command": "python",
                    "args": ["-c", f"import sys; open('{marker}', 'w').write(sys.executable)"],
                },
            },
        )
        svc = McpService(cfg)
        assert svc.run_setup(env) is True, "bare `python` step failed — interpreter not resolved to an absolute path"
        assert marker.read_text() == sys.executable


class TestResolveCommand:
    def test_bare_python_resolves_to_sys_executable(self):
        import sys

        assert resolve_command("python") == sys.executable
        assert resolve_command("python3") == sys.executable

    def test_non_python_commands_pass_through(self):
        for command in ("node", "bash", "npm", "uv", "/opt/venv/bin/python", "frappe-bench/env/bin/python"):
            assert resolve_command(command) == command
