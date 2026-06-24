"""Pin `sandbox._agent_env`: the single chokepoint that hides /opt/venv from
agent-spawned subprocesses (bash)."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from core.tools import sandbox


def _polluted_environ() -> dict[str, str]:
    return {
        "HOME": "/home/model",
        "USER": "model",
        "LANG": "C.UTF-8",
        "PATH": "/opt/venv/bin:/app/scripts:/usr/local/bin:/usr/bin:/bin",
        "VIRTUAL_ENV": "/opt/venv",
        "UV_PROJECT_ENVIRONMENT": "/opt/venv",
        "PYTHONPATH": "/app:/opt/venv/lib/python3.13/site-packages",
        "PYTHONHOME": "/opt/venv",
        "SETUID": "1000",
        "SETGID": "1000",
    }


def test_strips_opt_venv_from_path():
    with mock.patch.dict(os.environ, _polluted_environ(), clear=True):
        env = sandbox._agent_env()
    assert "/opt/venv" not in env["PATH"], env["PATH"]


def test_strips_app_from_path():
    with mock.patch.dict(os.environ, _polluted_environ(), clear=True):
        env = sandbox._agent_env()
    assert "/app" not in env["PATH"], env["PATH"]


def test_drops_venv_pointers():
    with mock.patch.dict(os.environ, _polluted_environ(), clear=True):
        env = sandbox._agent_env()
    for var in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME"):
        assert var not in env, f"{var!r} leaked: {env.get(var)!r}"


def test_drops_privilege_control_vars():
    with mock.patch.dict(os.environ, _polluted_environ(), clear=True):
        env = sandbox._agent_env()
    assert "SETUID" not in env
    assert "SETGID" not in env


def test_preserves_user_facing_vars():
    with mock.patch.dict(os.environ, _polluted_environ(), clear=True):
        env = sandbox._agent_env()
    assert env["HOME"] == "/home/model"
    assert env["USER"] == "model"
    assert env["LANG"] == "C.UTF-8"


def test_path_is_a_concrete_default_not_absent():
    with mock.patch.dict(os.environ, _polluted_environ(), clear=True):
        env = sandbox._agent_env()
    assert env.get("PATH"), "agent PATH unexpectedly empty"
    assert "/usr/bin" in env["PATH"]
    assert "/usr/local/bin" in env["PATH"]


# Regression guard for the P1: derived images (docker/stem-*) append dirs like
# /opt/conda/bin; sanitising must strip only /opt/venv and /app, never the rest.


def _derived_image_environ() -> dict[str, str]:
    env = _polluted_environ()
    env["PATH"] = "/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/conda/bin"
    return env


def test_preserves_derived_image_path_additions():
    with mock.patch.dict(os.environ, _derived_image_environ(), clear=True):
        env = sandbox._agent_env()
    entries = env["PATH"].split(os.pathsep)
    assert "/opt/conda/bin" in entries, env["PATH"]
    assert "/opt/venv/bin" not in entries, env["PATH"]


def test_preserves_path_entry_order():
    with mock.patch.dict(os.environ, _derived_image_environ(), clear=True):
        env = sandbox._agent_env()
    entries = env["PATH"].split(os.pathsep)
    assert entries == ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin", "/opt/conda/bin"]


def test_strips_only_venv_and_app_prefixes_not_lookalikes():
    polluted = _polluted_environ()
    polluted["PATH"] = "/opt/venv/bin:/opt/venvtools/bin:/application/bin:/app/scripts:/usr/bin"
    with mock.patch.dict(os.environ, polluted, clear=True):
        env = sandbox._agent_env()
    entries = env["PATH"].split(os.pathsep)
    assert entries == ["/opt/venvtools/bin", "/application/bin", "/usr/bin"], env["PATH"]


def test_falls_back_to_default_when_only_server_entries():
    polluted = _polluted_environ()
    polluted["PATH"] = "/opt/venv/bin:/app/scripts"
    with mock.patch.dict(os.environ, polluted, clear=True):
        env = sandbox._agent_env()
    assert env["PATH"] == sandbox._AGENT_DEFAULT_PATH


def test_falls_back_to_default_when_path_unset():
    polluted = _polluted_environ()
    del polluted["PATH"]
    with mock.patch.dict(os.environ, polluted, clear=True):
        env = sandbox._agent_env()
    assert env["PATH"] == sandbox._AGENT_DEFAULT_PATH


def test_parent_env_unchanged():
    # Sanitiser must not mutate os.environ — core itself relies on those
    # vars for its own subsequent imports.
    polluted = _polluted_environ()
    with mock.patch.dict(os.environ, polluted, clear=True):
        sandbox._agent_env()
        assert os.environ["PATH"] == polluted["PATH"]
        assert os.environ["VIRTUAL_ENV"] == polluted["VIRTUAL_ENV"]
        assert os.environ["UV_PROJECT_ENVIRONMENT"] == polluted["UV_PROJECT_ENVIRONMENT"]
        assert os.environ["SETUID"] == polluted["SETUID"]


def test_drops_harness_internal_vars():
    polluted = _polluted_environ()
    polluted.update(
        {
            "WORLDBENCH_ROOT": "/app",
            "WORLDBENCH_SEED": "deadbeef",
            "WORLDBENCH_TOOL_SETS": "core_debug",
            # A harness var nobody listed explicitly — the prefix sweep must catch it.
            "WORLDBENCH_SOME_FUTURE_VAR": "/app/whatever",
            "INPUTDIR": "/app/setup_data/entities/core",
            "OUTPUTDIR": "/app/output_data/core",
            "BUNDLE_OUTPUT_DIR": "/app/output_data/services",
            "BUNDLE_INPUT_DIR": "/app/bundle/services/core",
            "BUNDLEDIR": "/app/bundle",
            "PORT": "41984",
            "VIEWER_PORT": "8123",
        }
    )
    with mock.patch.dict(os.environ, polluted, clear=True):
        env = sandbox._agent_env()
    for var in (
        "WORLDBENCH_ROOT",
        "WORLDBENCH_SEED",
        "WORLDBENCH_TOOL_SETS",
        "WORLDBENCH_SOME_FUTURE_VAR",
        "INPUTDIR",
        "OUTPUTDIR",
        "BUNDLE_OUTPUT_DIR",
        "BUNDLE_INPUT_DIR",
        "BUNDLEDIR",
        "PORT",
        "VIEWER_PORT",
    ):
        assert var not in env, f"{var!r} leaked to the agent: {env.get(var)!r}"
    # /app must not survive anywhere in the agent env (paths or otherwise).
    assert not any("/app" in v for v in env.values()), env


def test_preserves_clock_var():
    # The fake clock must reach the agent's shell; it is NOT a harness leak.
    polluted = _polluted_environ()
    polluted["WORLDBENCH_CURRENT_TIME"] = "2026-01-02T03:04:05Z"
    with mock.patch.dict(os.environ, polluted, clear=True):
        env = sandbox._agent_env()
    assert env["WORLDBENCH_CURRENT_TIME"] == "2026-01-02T03:04:05Z"


def test_privilege_drop_kwargs_noop_when_not_root():
    # Tests/CI run unprivileged: nothing to drop to, so subprocess gets no
    # user/group kwargs and runs as the invoking user.
    with mock.patch("core.tools.sandbox.os.geteuid", return_value=1000):
        assert sandbox._privilege_drop_kwargs() == {}


def test_privilege_drop_kwargs_drops_when_root():
    polluted = _polluted_environ()  # has SETUID/SETGID = 1000
    with (
        mock.patch("core.tools.sandbox.os.geteuid", return_value=0),
        mock.patch.dict(os.environ, polluted, clear=True),
    ):
        kwargs = sandbox._privilege_drop_kwargs()
    assert kwargs == {"user": 1000, "group": 1000, "extra_groups": []}


def test_privilege_drop_kwargs_fails_closed_when_root_without_ids():
    # Root but no SETUID/SETGID: refuse rather than run the agent's command as
    # root (a sandbox escape).
    env = {k: v for k, v in _polluted_environ().items() if k not in ("SETUID", "SETGID")}
    with (
        mock.patch("core.tools.sandbox.os.geteuid", return_value=0),
        mock.patch.dict(os.environ, env, clear=True),
        pytest.raises(RuntimeError, match="SETUID/SETGID are not both set"),
    ):
        sandbox._privilege_drop_kwargs()


# ---------------------------------------------------------------------------
# agent_stream_file — the unprivileged whole-file reader the viewer streams from
# ---------------------------------------------------------------------------


def test_agent_stream_file_streams_whole_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "WORKDIR", str(tmp_path))
    target = tmp_path / "data.bin"
    payload = bytes(range(256)) * 1000  # 256 KB, spans many read() calls
    target.write_bytes(payload)
    chunks = list(sandbox.agent_stream_file(str(target), chunk_size=4096))
    assert b"".join(chunks) == payload
    # Actually streamed in pieces rather than buffered as one blob.
    assert len(chunks) > 1


def test_agent_stream_file_empty_file_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "WORKDIR", str(tmp_path))
    target = tmp_path / "empty.bin"
    target.write_bytes(b"")
    assert list(sandbox.agent_stream_file(str(target))) == []


def test_agent_stream_file_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "WORKDIR", str(tmp_path))
    with pytest.raises(sandbox.AgentReadError):
        list(sandbox.agent_stream_file(str(tmp_path / "nope.bin")))


def test_agent_stream_file_rejects_fifo(tmp_path, monkeypatch):
    # A named pipe with no writer would block a reader forever; the helper must
    # refuse non-regular files instead of hanging. (No writer is opened here, so
    # this test would deadlock if the guard regressed.)
    monkeypatch.setattr(sandbox, "WORKDIR", str(tmp_path))
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(sandbox.AgentReadError):
        list(sandbox.agent_stream_file(str(fifo)))


def test_agent_read_window_rejects_fifo(tmp_path, monkeypatch):
    # Same guard on the windowed reader (backs readFile / viewer / grading).
    monkeypatch.setattr(sandbox, "WORKDIR", str(tmp_path))
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(sandbox.AgentReadError):
        sandbox.agent_read_window(str(fifo))


def test_agent_read_window_roundtrips(tmp_path):
    sandbox.WORKDIR = str(tmp_path)
    p = tmp_path / "data.bin"
    p.write_bytes(b"0123456789abcdef")
    size, header, raw, start = sandbox.agent_read_window(str(p), offset=4, limit=5, sniff=3)
    assert size == 16
    assert header == b"012"
    assert raw == b"45678"
    assert start == 4


def test_agent_read_window_raises_on_missing(tmp_path):
    sandbox.WORKDIR = str(tmp_path)
    with pytest.raises(sandbox.AgentReadError):
        sandbox.agent_read_window(str(tmp_path / "nope.bin"))


def test_agent_list_dir(tmp_path):
    sandbox.WORKDIR = str(tmp_path)
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    entries = {e["name"]: e for e in sandbox.agent_list_dir(str(tmp_path))}
    assert entries["a.txt"]["is_dir"] is False
    assert entries["a.txt"]["size"] == 2
    assert entries["sub"]["is_dir"] is True


def test_run_in_sandbox_passes_sanitised_env(tmp_path):
    # End-to-end: catches the failure mode where _agent_env exists but
    # run_in_sandbox forgot to call it.
    sandbox.WORKDIR = str(tmp_path)
    polluted = _polluted_environ()
    with mock.patch.dict(os.environ, polluted, clear=True):
        result = sandbox.run_in_sandbox(
            [
                "sh",
                "-c",
                "echo PATH=$PATH; echo VENV=${VIRTUAL_ENV:-}; echo UV=${UV_PROJECT_ENVIRONMENT:-}; echo SU=${SETUID:-}",
            ],
            timeout=10,
        )
    assert result["returncode"] == 0, result
    out = result["stdout"]
    assert "/opt/venv" not in out, out
    assert "/app" not in out, out
    assert "VENV=\n" in out or out.rstrip().endswith("VENV="), out
    assert "UV=\n" in out or ("UV=" in out and "/opt/venv" not in out), out
    assert "SU=\n" in out or out.rstrip().endswith("SU="), out
