"""Tests for the setup pass the proxy runs during ``mcp`` startup.

There is no standalone setup command: ``build_proxy_app`` discovers MCP
servers and runs each one's ``install`` then ``setup`` hook from
``mcp.json`` (forwarding WORLDBENCH_* env vars) before starting the
server. Servers are selected by WORLDBENCH_TOOL_SETS: only servers whose
mcp.json declares at least one of the requested namespaced toolsets are
included. An empty / unset value runs no setup hooks.

These tests exercise that same discover -> install -> setup pass in
process, using the exact primitives ``build_proxy_app`` calls.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp_proxy.commands.mcp import _build_subprocess_env, discover_mcp_servers


class SetupCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.packages_root = Path(self.temp_dir) / "packages"
        self.packages_root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_server(self, name, setup_hook=None, toolsets=None):
        """Create a fake MCP server package with an optional setup hook.

        Always declares a ``"default"`` toolset (so :meth:`_run_setup` can
        opt every package in by default). *toolsets* is merged on top, in
        case a test wants to declare additional toolsets to filter against.
        """
        pkg_dir = self.packages_root / name
        pkg_dir.mkdir(parents=True)

        config = {"run": {"command": "python", "args": ["-c", "pass"]}}
        if setup_hook is not None:
            config["setup"] = setup_hook
        config["toolsets"] = {"default": [], **(toolsets or {})}

        (pkg_dir / "mcp.json").write_text(json.dumps(config))
        return pkg_dir

    def _run_setup(self, env_overrides=None):
        """Run the startup setup pass in process and return the failed servers.

        Mirrors the discover -> install -> setup loop ``build_proxy_app``
        runs before booting (the only place setup hooks fire now). Returns
        the list of server names whose install or setup hook failed — empty
        on success.

        Defaults WORLDBENCH_TOOL_SETS to every fake package's ``_default``
        toolset so each test gets full coverage without spelling them out.
        Override via *env_overrides* when a test wants narrower filtering.
        """
        default_sets = " ".join(f"{p.name}_default" for p in sorted(self.packages_root.iterdir()) if p.is_dir())
        env = {
            "WORLDBENCH_ROOT": self.temp_dir,
            "WORLDBENCH_PACKAGES_ROOT": str(self.packages_root),
            "WORLDBENCH_TOOL_SETS": default_sets,
        }
        if env_overrides:
            env.update(env_overrides)

        failed: list[str] = []
        with mock.patch.dict(os.environ, env):
            base_dir = Path(os.environ["WORLDBENCH_ROOT"]).resolve()
            packages_root = Path(os.environ["WORLDBENCH_PACKAGES_ROOT"])
            tool_sets = os.environ["WORLDBENCH_TOOL_SETS"].split()
            for cfg in discover_mcp_servers(packages_root, tool_sets=tool_sets):
                hook_env = _build_subprocess_env(base_dir, cfg.name, declared_secrets=cfg.secrets)
                if not cfg.run_install(hook_env) or not cfg.run_setup(hook_env):
                    failed.append(cfg.name)
        return failed

    def test_runs_setup_hook(self):
        """Setup hook from mcp.json is executed."""
        marker = Path(self.temp_dir) / "setup_ran.txt"
        self._create_server(
            "svc",
            setup_hook={
                "command": "python",
                "args": ["-c", f"open('{marker}', 'w').write('ok')"],
            },
        )

        failed = self._run_setup()
        assert failed == [], failed
        assert marker.exists()
        assert marker.read_text() == "ok"

    def test_skips_server_without_setup_hook(self):
        """Servers without a setup hook are skipped gracefully."""
        self._create_server("no-setup")

        failed = self._run_setup()
        assert failed == [], failed

    def test_forwards_env_vars(self):
        """WORLDBENCH_* env vars are forwarded to the setup hook."""
        marker = Path(self.temp_dir) / "env_check.txt"
        self._create_server(
            "svc",
            setup_hook={
                "command": "python",
                "args": [
                    "-c",
                    f"import os; open('{marker}', 'w').write(os.environ.get('WORLDBENCH_TASK_ID', 'MISSING'))",
                ],
            },
        )

        failed = self._run_setup({"WORLDBENCH_TASK_ID": "test-123"})
        assert failed == [], failed
        assert marker.read_text() == "test-123"

    def test_respects_server_filter(self):
        """Only servers declaring the requested toolset are set up."""
        marker_a = Path(self.temp_dir) / "a_ran.txt"
        marker_b = Path(self.temp_dir) / "b_ran.txt"
        self._create_server(
            "alpha",
            setup_hook={
                "command": "python",
                "args": ["-c", f"open('{marker_a}', 'w').write('ok')"],
            },
            toolsets={"read": ["tool_a"]},
        )
        self._create_server(
            "beta",
            setup_hook={
                "command": "python",
                "args": ["-c", f"open('{marker_b}', 'w').write('ok')"],
            },
            toolsets={"write": ["tool_b"]},
        )

        failed = self._run_setup({"WORLDBENCH_TOOL_SETS": "alpha_read"})
        assert failed == [], failed
        assert marker_a.exists()
        assert not marker_b.exists()

    def test_fails_if_setup_hook_fails(self):
        """A non-zero exit from a setup hook is reported as a failed server."""
        self._create_server(
            "bad",
            setup_hook={
                "command": "python",
                "args": ["-c", "import sys; sys.exit(1)"],
            },
        )

        failed = self._run_setup()
        assert failed == ["bad"], failed

    def test_multi_step_setup(self):
        """Setup hooks can be a list of steps."""
        marker1 = Path(self.temp_dir) / "step1.txt"
        marker2 = Path(self.temp_dir) / "step2.txt"
        self._create_server(
            "svc",
            setup_hook=[
                {"command": "python", "args": ["-c", f"open('{marker1}', 'w').write('1')"]},
                {"command": "python", "args": ["-c", f"open('{marker2}', 'w').write('2')"]},
            ],
        )

        failed = self._run_setup()
        assert failed == [], failed
        assert marker1.read_text() == "1"
        assert marker2.read_text() == "2"


if __name__ == "__main__":
    unittest.main()
