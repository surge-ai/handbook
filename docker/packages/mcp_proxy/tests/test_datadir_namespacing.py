from pathlib import Path

from mcp_proxy.commands.mcp import _build_subprocess_env


def test_inputdir_namespaced_per_server(tmp_path, monkeypatch):
    """When INPUTDIR is set in env, _build_subprocess_env appends server_name."""
    monkeypatch.setenv("INPUTDIR", str(tmp_path))
    env = _build_subprocess_env(tmp_path, server_name="google_mail")
    assert env["INPUTDIR"] == str(tmp_path / "google_mail")
    assert Path(env["INPUTDIR"]).is_dir()


def test_inputdir_defaults_to_base_dir(tmp_path, monkeypatch):
    """When INPUTDIR is not in env, it defaults to base_dir/server_name."""
    monkeypatch.delenv("INPUTDIR", raising=False)
    env = _build_subprocess_env(tmp_path, server_name="google_mail")
    assert env["INPUTDIR"] == str(tmp_path / "google_mail")
    assert Path(env["INPUTDIR"]).is_dir()


def test_outputdir_from_env(tmp_path, monkeypatch):
    """When OUTPUTDIR is set in env, it is namespaced per server."""
    monkeypatch.setenv("OUTPUTDIR", str(tmp_path))
    env = _build_subprocess_env(tmp_path, server_name="slack")
    assert env["OUTPUTDIR"] == str(tmp_path / "slack")
    assert Path(env["OUTPUTDIR"]).is_dir()


def test_outputdir_defaults_to_base_dir(tmp_path, monkeypatch):
    """When OUTPUTDIR is not in env, it defaults to base_dir/server_name."""
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    env = _build_subprocess_env(tmp_path, server_name="google_mail")
    assert env["OUTPUTDIR"] == str(tmp_path / "google_mail")
    assert Path(env["OUTPUTDIR"]).is_dir()


def test_all_three_dirs_always_set(tmp_path, monkeypatch):
    """INPUTDIR, OUTPUTDIR, and BUNDLE_OUTPUT_DIR are always present in the returned env."""
    monkeypatch.delenv("INPUTDIR", raising=False)
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    env = _build_subprocess_env(tmp_path, server_name="jira")
    assert "INPUTDIR" in env
    assert "OUTPUTDIR" in env
    assert "BUNDLE_OUTPUT_DIR" in env


def test_bundle_output_dir_namespaced_per_service(tmp_path, monkeypatch):
    """BUNDLE_OUTPUT_DIR is per-service so each service writes its own
    services/<name>/state.json, matching the nested input bundle layout."""
    monkeypatch.setenv("OUTPUTDIR", str(tmp_path))

    env_a = _build_subprocess_env(tmp_path, server_name="google_mail")
    env_b = _build_subprocess_env(tmp_path, server_name="shopify")

    assert env_a["BUNDLE_OUTPUT_DIR"] == str(tmp_path / "services" / "google_mail")
    assert env_b["BUNDLE_OUTPUT_DIR"] == str(tmp_path / "services" / "shopify")


def test_bundle_output_dir_defaults_to_base_dir(tmp_path, monkeypatch):
    """Without OUTPUTDIR in env, BUNDLE_OUTPUT_DIR is rooted at
    base_dir/services/<name>."""
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    env = _build_subprocess_env(tmp_path, server_name="emails_mock")
    assert env["BUNDLE_OUTPUT_DIR"] == str(tmp_path / "services" / "emails_mock")
    assert Path(env["BUNDLE_OUTPUT_DIR"]).is_dir()
