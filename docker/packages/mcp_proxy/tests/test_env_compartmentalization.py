"""Regression tests for proxy-level env compartmentalization.

This is the structural defense against agent-side secret exfiltration via
mcp tools, like core's bash.
"""

from mcp_proxy.commands.mcp import _build_subprocess_env


def test_declared_credential_is_forwarded(tmp_path, monkeypatch):
    """A service that declares a credential by exact name in its mcp.json
    ``env`` list does receive it — opt-in bypasses the denylist."""
    monkeypatch.setenv("BRAVE_API_KEY", "secret-for-web")
    env = _build_subprocess_env(tmp_path, server_name="web", declared_secrets=["BRAVE_API_KEY"])
    assert env["BRAVE_API_KEY"] == "secret-for-web"


def test_secret_isolation_between_services(tmp_path, monkeypatch):
    """Same proxy env, two services with different declarations — only the
    declaring service sees the secret."""
    monkeypatch.setenv("BRAVE_API_KEY", "secret-do-not-leak")

    web_env = _build_subprocess_env(tmp_path, server_name="web", declared_secrets=["BRAVE_API_KEY"])
    core_env = _build_subprocess_env(tmp_path, server_name="core", declared_secrets=())

    assert "BRAVE_API_KEY" in web_env
    assert "BRAVE_API_KEY" not in core_env


def test_credential_shaped_vars_are_dropped(tmp_path, monkeypatch):
    pollute = {
        # KEY
        "BRAVE_API_KEY": "x",
        "MY_PRIVATE_KEY_PATH": "/etc/key",
        "WORLDBENCH_FOO_API_KEY": "x",
        "GITHUB_TOKEN": "ghp_x",
        "AWS_SECRET_ACCESS_KEY": "aws-x",
        "stripe_secret": "x",  # case-insensitive
        "DB_PASSWORD": "hunter2",
        "MYSQL_PASSWD": "old-school-x",
        "SSH_PASSPHRASE": "x",
        "GPG_PASSPHRASE": "x",
        "GOOGLE_CREDENTIALS": "json-x",
        "OAUTH_CLIENT_ID": "x",
        "BEARER_TOKEN": "x",
        "JWT_SECRET": "x",
        "JWT_SIGNING_KEY": "x",
        "SESSION_COOKIE": "x",
        "AUTH_COOKIE": "x",
        "TOTP_SECRET": "x",
        "HOTP_KEY": "x",
        "DATABASE_URL": "postgres://app_user:hunter2@db.internal:5432/app",
        "REDIS_URL": "redis://:hunter2@cache.internal:6379/0",
        "PIP_INDEX_URL": "https://user:token@pypi.example.com/simple",
        "UV_INDEX_URL": "https://user:token@uv.example.com/simple",
        "JDBC_DATABASE_URL": "jdbc:postgresql://app:hunter2@db.internal/app",
        "JDBC_MYSQL_URL": "jdbc:mysql://u:p@db:3306/x",
        "GIT_REMOTE_URL": "https://ghp_xxxxxxxxxxxx@github.com/org/repo",
        "REGISTRY_URL": "https://pypi-token-here@pypi.example.com/simple",
    }
    for k, v in pollute.items():
        monkeypatch.setenv(k, v)

    env = _build_subprocess_env(tmp_path, server_name="core", declared_secrets=())

    for k in pollute:
        assert k not in env, f"{k} leaked despite credential-shaped name"


def test_innocuous_vars_pass_through(tmp_path, monkeypatch):
    """Default-allow: anything without a credential keyword is forwarded.
    This is what makes the simplified model work — we don't have to enumerate
    every system var a subprocess might legitimately need."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/model")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("WORLDBENCH_TASK_ID", "task-123")
    monkeypatch.setenv("WORLDBENCH_TOOL_SETS", "core_read")
    monkeypatch.setenv("RANDOM_CUSTOM_VAR", "value")
    monkeypatch.setenv("BRAVE_SEARCH_URL", "https://api.search.brave.com/res/v1/web/search")
    monkeypatch.setenv("PUBLIC_API_BASE", "https://api.example.com/v1")
    monkeypatch.setenv("DATABASE_URL", "postgres://localhost/dev")  # no userinfo

    env = _build_subprocess_env(tmp_path, server_name="core", declared_secrets=())

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/model"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["WORLDBENCH_TASK_ID"] == "task-123"
    assert env["WORLDBENCH_TOOL_SETS"] == "core_read"
    assert env["RANDOM_CUSTOM_VAR"] == "value"
    assert env["BRAVE_SEARCH_URL"] == "https://api.search.brave.com/res/v1/web/search"
    assert env["PUBLIC_API_BASE"] == "https://api.example.com/v1"
    assert env["DATABASE_URL"] == "postgres://localhost/dev"


def test_url_with_userinfo_can_be_explicitly_declared(tmp_path, monkeypatch):
    """A service that legitimately needs a credentialed URL can opt in by
    declaring the var name in mcp.json's ``secrets`` — declared names bypass
    both the name-shape and value-shape checks."""
    monkeypatch.setenv("DATABASE_URL", "postgres://app_user:hunter2@db.internal:5432/app")

    db_user_env = _build_subprocess_env(tmp_path, server_name="my-db-service", declared_secrets=["DATABASE_URL"])
    core_env = _build_subprocess_env(tmp_path, server_name="core", declared_secrets=())

    assert "DATABASE_URL" in db_user_env
    assert "DATABASE_URL" not in core_env
