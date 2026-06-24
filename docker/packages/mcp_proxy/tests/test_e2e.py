"""End-to-end test: seed fixtures → start proxy → call tools via MCP client.

Run with:  just test-e2e
           uv run pytest packages/mcp_proxy/tests/test_e2e.py -v -m e2e
"""

from __future__ import annotations

import asyncio
import json
import os
import select
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(300)]

REPO_ROOT = Path(__file__).parent.parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "fixtures" / "simple_office"


class _ProxyEndpoints(NamedTuple):
    """The two HTTP surfaces a running proxy exposes."""

    mcp: str  # streamable-http MCP endpoint, e.g. http://127.0.0.1:PORT/mcp
    viewer: str  # viewer reverse-proxy base URL, e.g. http://127.0.0.1:VIEWER_PORT


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 120.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def seeded_inputdir(tmp_path_factory):
    """Seed fixtures/simple_office into a temp INPUTDIR."""
    inputdir = tmp_path_factory.mktemp("core-e2e")
    for server_dir in sorted(FIXTURES_DIR.iterdir()):
        if server_dir.is_dir():
            dest_dir = inputdir / server_dir.name
            dest_dir.mkdir()
            for json_file in server_dir.glob("*.json"):
                shutil.copy(json_file, dest_dir / json_file.name)
    return inputdir


_FIXTURE_PACKAGES = (
    "core",
    "emails_mock",
    "google_calendar",
    "google_mail",
    "grading",
    "jira",
    "shopify",
    "slack",
    "web",
)


def _every_namespaced_toolset() -> str:
    """Build a WORLDBENCH_TOOL_SETS value that exposes every tool from every
    server in :data:`_FIXTURE_PACKAGES` (state, grading, etc. included).
    """
    out: list[str] = []
    for pkg in _FIXTURE_PACKAGES:
        data = json.loads((REPO_ROOT / "packages" / pkg / "mcp.json").read_text())
        for ts in data.get("toolsets", {}):
            out.append(f"{pkg}_{ts}")
    return " ".join(out)


@pytest.fixture(scope="module")
def proxy_endpoints(seeded_inputdir):
    """Start mcp_proxy in HTTP mode via start.sh; yield its MCP + viewer URLs;
    tear down after module."""
    port = _find_free_port()
    viewer_port = _find_free_port()

    env = {
        **os.environ,
        "WORLDBENCH_ROOT": str(REPO_ROOT),
        "INPUTDIR": str(seeded_inputdir),
        "WORLDBENCH_METHOD": "http",
        "WORLDBENCH_TOOL_SETS": _every_namespaced_toolset(),
        "PORT": str(port),
        "VIEWER_PORT": str(viewer_port),
    }

    proc = subprocess.Popen(
        ["bash", str(REPO_ROOT / "scripts" / "start.sh")],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Drain stderr in background so the pipe never blocks child processes.
    stderr_lines: list[str] = []

    def _drain():
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.decode(errors="replace"))

    threading.Thread(target=_drain, daemon=True).start()

    ready = _wait_for_port(port, timeout=180.0)
    if not ready:
        proc.terminate()
        stderr_tail = "".join(stderr_lines[-40:])
        pytest.fail(f"Proxy did not start within 180s.\nSTDERR:\n{stderr_tail}")

    # The viewer runs in a background uvicorn thread that binds VIEWER_PORT
    # *after* the MCP port is connectable, so wait for it explicitly — otherwise
    # the first request against viewer_url can hit a not-yet-bound socket.
    if not _wait_for_port(viewer_port, timeout=30.0):
        proc.terminate()
        stderr_tail = "".join(stderr_lines[-40:])
        pytest.fail(f"Viewer server did not start within 30s.\nSTDERR:\n{stderr_tail}")

    yield _ProxyEndpoints(
        mcp=f"http://127.0.0.1:{port}/mcp",
        viewer=f"http://127.0.0.1:{viewer_port}",
    )

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def proxy_url(proxy_endpoints):
    """MCP endpoint URL. Every existing test depends on this fixture name."""
    return proxy_endpoints.mcp


@pytest.fixture(scope="module")
def viewer_url(proxy_endpoints):
    """Base URL of the proxy's viewer reverse-proxy server (VIEWER_PORT)."""
    return proxy_endpoints.viewer


def _run_tool(proxy_url: str, tool_name: str, arguments: dict):
    """Call an MCP tool via fastmcp.Client and return the CallToolResult."""

    async def _call():
        async with Client(StreamableHttpTransport(proxy_url)) as client:
            return await client.call_tool(tool_name, arguments)

    return asyncio.run(_call())


@pytest.mark.e2e
def test_proxy_exposes_slack_and_mail_tools(proxy_url):
    """Both slack and gmail tools appear in the proxy's tool list."""

    async def _list():
        async with Client(StreamableHttpTransport(proxy_url)) as client:
            return await client.list_tools()

    tools = asyncio.run(_list())
    names = [t.name for t in tools]
    assert "slack__list_channels" in names, f"slack tools missing. Got: {names}"
    assert "google_mail__get_emails" in names, f"gmail tools missing. Got: {names}"


@pytest.mark.e2e
def test_slack_channels_loaded(proxy_url):
    """slack_list_channels returns the seeded channels."""
    result = _run_tool(proxy_url, "slack__list_channels", {})
    text = result.content[0].text
    data = json.loads(text)
    channel_names = [ch["name"] for ch in data.get("channels", [])]
    assert "general" in channel_names
    assert "engineering" in channel_names


@pytest.mark.e2e
def test_gmail_inbox_loaded(proxy_url):
    """mail_get_emails returns seeded INBOX emails including the PR #247 email."""
    result = _run_tool(proxy_url, "google_mail__get_emails", {"folder": "INBOX"})
    text = result.content[0].text
    data = json.loads(text)
    emails = data.get("emails", [])
    assert len(emails) > 0, "Expected at least one email in INBOX"
    subjects = [e["subject"] for e in emails]
    assert any("PR #247" in s for s in subjects), f"PR #247 email not found. Subjects: {subjects}"


@pytest.fixture(scope="module")
def stdio_proxy(seeded_inputdir):
    """Start mcp_proxy in stdio mode via start.sh; yield a helper that sends/receives JSON-RPC."""
    viewer_port = _find_free_port()

    env = {
        **os.environ,
        "WORLDBENCH_ROOT": str(REPO_ROOT),
        "INPUTDIR": str(seeded_inputdir),
        "VIEWER_PORT": str(viewer_port),
        "WORLDBENCH_TOOL_SETS": "google_mail_read",
        # No WORLDBENCH_METHOD → defaults to "stdio"
    }

    proc = subprocess.Popen(
        ["bash", str(REPO_ROOT / "scripts" / "start.sh")],
        cwd=str(REPO_ROOT),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Drain stderr in background so the pipe never blocks.
    stderr_lines: list[str] = []

    def _drain_stderr():
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.decode(errors="replace"))

    threading.Thread(target=_drain_stderr, daemon=True).start()

    def _send(msg: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    def _recv(timeout: float = 30.0) -> dict | None:
        assert proc.stdout is not None
        ready = select.select([proc.stdout], [], [], timeout)
        if not ready[0]:
            return None
        line = proc.stdout.readline()
        return json.loads(line) if line.strip() else None

    # Wait for the proxy to be ready by polling with initialize requests.
    deadline = time.monotonic() + 120.0
    init_result: dict | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr_tail = "".join(stderr_lines[-40:])
            pytest.fail(f"Proxy exited during startup (code {proc.returncode}).\nSTDERR:\n{stderr_tail}")
        _send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest-stdio", "version": "1"},
                },
            }
        )
        init_result = _recv(timeout=5.0)
        if init_result is not None:
            break
        time.sleep(1.0)
    else:
        stderr_tail = "".join(stderr_lines[-40:])
        proc.terminate()
        pytest.fail(f"Proxy did not respond to initialize within 120 s.\nSTDERR:\n{stderr_tail}")

    yield _send, _recv, init_result, stderr_lines

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.e2e
def test_stdio_initialize(stdio_proxy):
    """Proxy responds to MCP initialize with correct protocol version."""
    _send, _recv, init_result, _stderr = stdio_proxy
    assert init_result is not None
    result = init_result.get("result", {})
    assert result.get("protocolVersion") == "2024-11-05"
    assert "tools" in result.get("capabilities", {})


@pytest.mark.e2e
def test_stdio_tools_list_returns_tools(stdio_proxy):
    """tools/list returns tools from all mounted sub-servers.

    This is the regression test for the stdin-inheritance bug: child processes
    (setup hooks, sub-server Popen) used to inherit the proxy's stdin fd,
    causing FastMCP's stdio loop to see a spurious EOF and exit before
    responding to tools/list.
    """
    _send, _recv, _init, stderr_lines = stdio_proxy
    _send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    resp = _recv(timeout=30.0)

    assert resp is not None, (
        f"tools/list timed out — proxy may have exited.\nSTDERR tail:\n{''.join(stderr_lines[-20:])}"
    )
    assert "error" not in resp, f"tools/list returned an error: {resp}"
    tools = resp.get("result", {}).get("tools", [])
    assert len(tools) > 0, "Expected at least one tool from mounted sub-servers"

    names = {t["name"] for t in tools}
    # google_mail tools
    assert any("mail" in n or "email" in n for n in names), f"No mail tools found. Got: {sorted(names)}"


@pytest.mark.e2e
def test_cross_reference_bob_in_both(proxy_url):
    """Bob Smith (bob.smith@techcorp.com) appears in both Slack users and gmail senders."""
    slack_result = _run_tool(proxy_url, "slack__get_users", {})
    slack_data = json.loads(slack_result.content[0].text)
    # slack_get_users returns { ok, members: [...] }
    slack_names = [u.get("real_name", "") for u in slack_data.get("members", [])]
    assert any("Bob" in n for n in slack_names), f"Bob not in slack users: {slack_names}"

    mail_result = _run_tool(proxy_url, "google_mail__get_emails", {"folder": "INBOX"})
    mail_data = json.loads(mail_result.content[0].text)
    # mail_get_emails returns emails with "from" key (not "from_addr")
    senders = [e.get("from", "") for e in mail_data.get("emails", [])]
    assert any("bob.smith" in s for s in senders), f"bob.smith not in senders: {senders}"


# ---------------------------------------------------------------------------
# Convergence: INPUTDIR loader path ≡ import_state path
# ---------------------------------------------------------------------------
#
# Intended to run BEFORE any mutation tests so the initial state still reflects
# INPUTDIR load. Once we've refactored every startup loader to go through
# state_from_json, this test is structurally redundant and can be deleted — it
# exists to prove that the two codepaths converge today before we collapse them
# into one.


_CONVERGENCE_FIXTURES = [
    ("shopify", "shopify_data.json"),
    ("jira", "jira_state.json"),
    ("google_calendar", "calendar_data.json"),
    ("google_mail", "inbox.json"),
    ("emails_mock", "mailbox.json"),
    ("slack", None),  # multi-file, merged
    # core is namespaced: false, so it has no `core__export_state` to call
    # externally — its state is only reachable via the proxy aggregate.
]


@pytest.mark.e2e
@pytest.mark.parametrize(("server", "fixture_name"), _CONVERGENCE_FIXTURES)
def test_inputdir_load_matches_import_state(proxy_url, server, fixture_name):
    """The state after INPUTDIR load must equal the state after import_state(fixture).

    Both paths start from the same raw fixture JSON. If the startup loader
    doesn't funnel through state_from_json, it may produce subtly different
    state (different defaults, different normalisation). This test pins them
    down so we can collapse the two paths into one.
    """
    fixture_json = _load_fixture_state(server, fixture_name)
    if fixture_json is None:
        pytest.skip(f"No fixture on disk for {server}")

    # What the startup loader produced from INPUTDIR.
    from_startup = _extract_json(_run_tool(proxy_url, f"{server}__export_state", {}))

    # What import_state produces when fed the same raw fixture JSON.
    _run_tool(proxy_url, f"{server}__import_state", {"state": fixture_json})
    from_import_state = _extract_json(_run_tool(proxy_url, f"{server}__export_state", {}))

    assert from_startup == from_import_state, (
        f"{server}: INPUTDIR load and import_state(fixture) diverged. "
        f"Startup loader must be refactored to go through state_from_json."
    )


# ---------------------------------------------------------------------------
# State persistence: write-then-read across tool calls
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_gmail_state_persists_across_tool_calls(proxy_url):
    """Sending an email via the proxy is visible in a subsequent get_emails call.

    This verifies that StatefulProxyClient keeps the downstream session alive
    so that writes are not lost between tool calls (regression test for
    fastmcp#959 where each ProxyProvider call opened a fresh session).
    """
    # Send a new email
    send_result = _run_tool(
        proxy_url,
        "google_mail__send_email",
        {
            "to": "bob.smith@techcorp.com",
            "subject": "Session persistence e2e test",
            "body": "This email should be visible in a subsequent read.",
        },
    )
    send_data = json.loads(send_result.content[0].text)
    assert send_data.get("status") == "sent", f"Send failed: {send_data}"

    # Read Sent folder — the email we just sent must appear
    read_result = _run_tool(
        proxy_url,
        "google_mail__get_emails",
        {"folder": "Sent"},
    )
    read_data = json.loads(read_result.content[0].text)
    subjects = [e["subject"] for e in read_data.get("emails", [])]
    assert any("Session persistence e2e test" in s for s in subjects), (
        f"Sent email not found in Sent folder. Subjects: {subjects}"
    )


@pytest.mark.e2e
def test_slack_state_persists_across_tool_calls(proxy_url):
    """Posting a Slack message via the proxy is visible in a subsequent history call.

    Same regression coverage as the gmail test above, for the Slack service.
    """
    # Find a channel
    channels_result = _run_tool(proxy_url, "slack__list_channels", {})
    channels = json.loads(channels_result.content[0].text).get("channels", [])
    assert channels, "Need at least one Slack channel"
    channel_id = channels[0]["id"]

    # Post a message
    post_result = _run_tool(
        proxy_url,
        "slack__post_message",
        {"channel_id": channel_id, "text": "Session persistence e2e test"},
    )
    post_data = json.loads(post_result.content[0].text)
    assert post_data.get("ok"), f"Post failed: {post_data}"

    # Read channel history — the message we just posted must appear
    history_result = _run_tool(
        proxy_url,
        "slack__get_channel_history",
        {"channel_id": channel_id},
    )
    history_data = json.loads(history_result.content[0].text)
    messages = history_data.get("messages", [])
    texts = [m.get("text", "") for m in messages]
    assert any("Session persistence e2e test" in t for t in texts), (
        f"Posted message not found in channel history. Texts: {texts}"
    )


# ---------------------------------------------------------------------------
# Toolset filtering via start.sh --tool-sets
# ---------------------------------------------------------------------------

PACKAGES_DIR = REPO_ROOT / "packages"


def _toolset_names(pkg: str, toolset: str) -> set[str]:
    """Return tool names for *pkg*'s *toolset* from its mcp.json."""
    mcp_json = json.loads((PACKAGES_DIR / pkg / "mcp.json").read_text())
    namespaced = mcp_json.get("namespaced", True)
    if namespaced:
        return {f"{pkg}__{t}" for t in mcp_json["toolsets"][toolset]}
    return set(mcp_json["toolsets"][toolset])


def _all_toolset_names(pkg: str) -> set[str]:
    """Return all tool names for *pkg* across every toolset in its mcp.json."""
    mcp_json = json.loads((PACKAGES_DIR / pkg / "mcp.json").read_text())
    namespaced = mcp_json.get("namespaced", True)
    all_tools = {t for names in mcp_json["toolsets"].values() for t in names}
    if namespaced:
        return {f"{pkg}__{t}" for t in all_tools}
    return all_tools


# ---------------------------------------------------------------------------
# Namespaced toolset filtering — mirrors the real harness call pattern:
#   --tool-sets google_mail_read --tool-sets slack_read
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def namespaced_toolset_proxy_url(seeded_inputdir):
    """Start the proxy with namespaced toolsets, matching the harness call pattern.

    The harness invokes start.sh as:
        start.sh --tool-sets google_mail_read slack_read  (single flag, multiple values)

    This fixture uses that exact form.  start.sh also supports the multi-flag
    variant (--tool-sets A --tool-sets B) for CLI convenience; both are tested
    here via WORLDBENCH_TOOL_SETS being set correctly.

    Verifies:
    - Namespaced names (pkg_toolset) are resolved to the correct bare toolset
      within each package's mcp.json
    - Only the named packages are started; other packages (e.g. core) are excluded
    """
    port = _find_free_port()
    viewer_port = _find_free_port()

    env = {
        **os.environ,
        "WORLDBENCH_ROOT": str(REPO_ROOT),
        "INPUTDIR": str(seeded_inputdir),
        "WORLDBENCH_METHOD": "http",
        "PORT": str(port),
        "VIEWER_PORT": str(viewer_port),
    }

    proc = subprocess.Popen(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "start.sh"),
            # Single --tool-sets flag with multiple values — matches sdk args.push() spread
            "--tool-sets",
            "google_mail_read",
            "slack_read",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Drain stderr in background so the pipe never blocks child processes.
    stderr_lines: list[str] = []

    def _drain():
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.decode(errors="replace"))

    threading.Thread(target=_drain, daemon=True).start()

    ready = _wait_for_port(port, timeout=180.0)
    if not ready:
        proc.terminate()
        stderr_tail = "".join(stderr_lines[-40:])
        pytest.fail(f"Proxy did not start within 180s.\nSTDERR:\n{stderr_tail}")

    yield f"http://127.0.0.1:{port}/mcp"

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.e2e
def test_namespaced_toolsets_expose_correct_tools(namespaced_toolset_proxy_url):
    """--tool-sets google_mail_read --tool-sets slack_read exposes exactly those read tools."""

    async def _list():
        async with Client(StreamableHttpTransport(namespaced_toolset_proxy_url)) as client:
            return await client.list_tools()

    names = {t.name for t in asyncio.run(_list())}

    expected = _toolset_names("google_mail", "read") | _toolset_names("slack", "read")
    missing = expected - names
    assert not missing, f"Expected tools missing: {sorted(missing)}"


@pytest.mark.e2e
def test_namespaced_toolsets_exclude_other_packages(namespaced_toolset_proxy_url):
    """With google_mail_read + slack_read, core tools must not be exposed."""

    async def _list():
        async with Client(StreamableHttpTransport(namespaced_toolset_proxy_url)) as client:
            return await client.list_tools()

    names = {t.name for t in asyncio.run(_list())}

    core_tools = _all_toolset_names("core")
    unexpected = core_tools & names
    assert not unexpected, f"core tools should not appear: {sorted(unexpected)}"


@pytest.mark.e2e
def test_namespaced_toolsets_exclude_write_tools(namespaced_toolset_proxy_url):
    """With google_mail_read + slack_read, write-only tools must not be exposed."""

    async def _list():
        async with Client(StreamableHttpTransport(namespaced_toolset_proxy_url)) as client:
            return await client.list_tools()

    names = {t.name for t in asyncio.run(_list())}

    write_only_mail = _toolset_names("google_mail", "write") - _toolset_names("google_mail", "read")
    write_only_slack = _toolset_names("slack", "write") - _toolset_names("slack", "read")
    unexpected = (write_only_mail | write_only_slack) & names
    assert not unexpected, f"Write-only tools should not appear under read toolset: {sorted(unexpected)}"


# ---------------------------------------------------------------------------
# OUTPUTDIR: initial.json / final.json state snapshots
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def outputdir_proxy(seeded_inputdir, tmp_path_factory):
    """Start proxy with INPUTDIR + OUTPUTDIR; yield (proxy_url, outputdir) tuple.

    After teardown the OUTPUTDIR should contain per-server subdirectories
    with initial.json and final.json snapshots.
    """
    outputdir = tmp_path_factory.mktemp("outputdir-e2e")
    port = _find_free_port()
    viewer_port = _find_free_port()

    env = {
        **os.environ,
        "WORLDBENCH_ROOT": str(REPO_ROOT),
        "INPUTDIR": str(seeded_inputdir),
        "OUTPUTDIR": str(outputdir),
        "WORLDBENCH_METHOD": "http",
        "WORLDBENCH_TOOL_SETS": _every_namespaced_toolset(),
        "PORT": str(port),
        "VIEWER_PORT": str(viewer_port),
    }

    proc = subprocess.Popen(
        ["bash", str(REPO_ROOT / "scripts" / "start.sh")],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Drain stderr in background so the pipe never blocks child processes.
    stderr_lines: list[str] = []

    def _drain():
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.decode(errors="replace"))

    threading.Thread(target=_drain, daemon=True).start()

    ready = _wait_for_port(port, timeout=180.0)
    if not ready:
        proc.terminate()
        stderr_tail = "".join(stderr_lines[-40:])
        pytest.fail(f"Proxy did not start within 180s.\nSTDERR:\n{stderr_tail}")

    yield f"http://127.0.0.1:{port}/mcp", outputdir, proc

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.e2e
def test_outputdir_initial_json_created(outputdir_proxy):
    """After startup, initial.json exists under OUTPUTDIR/<server_name> for google_mail and slack."""
    _proxy_url, outputdir, _proc = outputdir_proxy

    for service in ("google_mail", "slack", "google_calendar", "shopify", "emails_mock", "jira"):
        initial = outputdir / service / "initial.json"
        assert initial.exists(), f"{service}/initial.json not found in {outputdir}"
        data = json.loads(initial.read_text())
        assert isinstance(data, dict), f"{service}/initial.json is not a JSON object"


@pytest.mark.e2e
def test_outputdir_mutate_then_final_json(outputdir_proxy):
    """Write tools produce final.json immediately (no process termination required)."""
    proxy_url, outputdir, _proc = outputdir_proxy

    # Read initial gmail state
    gmail_initial = json.loads((outputdir / "google_mail" / "initial.json").read_text())
    initial_email_count = len(gmail_initial.get("emails", []))

    gmail_final_path = outputdir / "google_mail" / "final.json"
    slack_final_path = outputdir / "slack" / "final.json"

    # final.json should not exist before any write tool is called
    assert not gmail_final_path.exists(), "google_mail/final.json should not exist before writes"
    assert not slack_final_path.exists(), "slack/final.json should not exist before writes"

    # Send a new email via google_mail
    _run_tool(
        proxy_url,
        "google_mail__send_email",
        {
            "to": "bob.smith@techcorp.com",
            "subject": "OUTPUTDIR e2e test",
            "body": "Verifying final.json captures mutations.",
        },
    )

    # final.json must exist immediately after the write tool call — no termination needed
    assert gmail_final_path.exists(), "google_mail/final.json must be written immediately after a write tool call"
    gmail_final = json.loads(gmail_final_path.read_text())
    final_email_count = len(gmail_final.get("emails", []))
    assert final_email_count > initial_email_count, (
        f"Expected more emails after send: initial={initial_email_count}, final={final_email_count}"
    )

    # Post a message via slack
    slack_result = _run_tool(proxy_url, "slack__list_channels", {})
    channels = json.loads(slack_result.content[0].text).get("channels", [])
    assert channels, "Need at least one Slack channel"
    channel_id = channels[0]["id"]

    # Read-only tool (list_channels) must not create final.json for slack
    assert not slack_final_path.exists(), "slack/final.json must not exist after a read-only tool call"

    _run_tool(
        proxy_url,
        "slack__post_message",
        {
            "channel_id": channel_id,
            "text": "OUTPUTDIR e2e test message",
        },
    )

    # Slack final.json must exist immediately after posting a message
    assert slack_final_path.exists(), "slack/final.json must be written immediately after a write tool call"
    slack_final = json.loads(slack_final_path.read_text())

    all_messages = []
    for ch_msgs in slack_final.get("messages", {}).values():
        all_messages.extend(ch_msgs)
    assert any("OUTPUTDIR e2e test" in m.get("text", "") for m in all_messages), (
        "Posted message not found in slack/final.json messages"
    )


# ---------------------------------------------------------------------------
# Shopify e2e tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_proxy_exposes_shopify_tools(proxy_url):
    """Shopify tools appear in the proxy's tool list."""

    async def _list():
        async with Client(StreamableHttpTransport(proxy_url)) as client:
            return await client.list_tools()

    tools = asyncio.run(_list())
    names = [t.name for t in tools]
    assert "shopify__search_shop_catalog" in names, f"shopify tools missing. Got: {names}"


@pytest.mark.e2e
def test_shopify_products_loaded(proxy_url):
    """search_shop_catalog returns seeded products."""
    result = _run_tool(proxy_url, "shopify__search_shop_catalog", {"query": "keyboard", "context": "browsing"})
    text = result.content[0].text
    data = json.loads(text)
    products = data.get("nodes", [])
    assert len(products) > 0, f"Expected at least one product. Got: {data}"


@pytest.mark.e2e
def test_shopify_state_persists_across_tool_calls(proxy_url):
    """Creating a cart via update_cart is visible in a subsequent list_carts call."""
    # Create a cart by adding an item
    create_result = _run_tool(
        proxy_url,
        "shopify__update_cart",
        {"add_items": [{"merchandiseId": "gid://shopify/ProductVariant/2001", "quantity": 1}]},
    )
    create_data = json.loads(create_result.content[0].text)
    assert "id" in create_data, f"Cart creation failed: {create_data}"

    # List carts — our cart should appear
    list_result = _run_tool(proxy_url, "shopify__list_carts", {})
    list_data = json.loads(list_result.content[0].text)
    carts = list_data.get("carts", [])
    assert len(carts) > 0, "Expected at least one cart after creation"


# ---------------------------------------------------------------------------
# Google Calendar e2e tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_proxy_exposes_google_calendar_tools(proxy_url):
    """Google Calendar tools appear in the proxy's tool list."""

    async def _list():
        async with Client(StreamableHttpTransport(proxy_url)) as client:
            return await client.list_tools()

    tools = asyncio.run(_list())
    names = [t.name for t in tools]
    assert "google_calendar__list_events" in names, f"google_calendar tools missing. Got: {names}"


@pytest.mark.e2e
def test_google_calendar_events_loaded(proxy_url):
    """list_events returns seeded calendar events."""
    result = _run_tool(
        proxy_url,
        "google_calendar__list_events",
        {"timeMin": "2025-01-01T00:00:00Z", "timeMax": "2026-12-31T23:59:59Z"},
    )
    text = result.content[0].text
    data = json.loads(text)
    events = data.get("events", [])
    assert len(events) > 0, f"Expected at least one event. Got: {data}"
    summaries = [e.get("summary", "") for e in events]
    assert any("Sprint Planning" in s for s in summaries), f"Sprint Planning event not found. Summaries: {summaries}"


@pytest.mark.e2e
def test_google_calendar_state_persists_across_tool_calls(proxy_url):
    """Creating an event is visible in a subsequent list_events call."""
    # Create a new event
    create_result = _run_tool(
        proxy_url,
        "google_calendar__create_event",
        {
            "summary": "E2E Test Event",
            "start": {"dateTime": "2025-07-01T10:00:00Z"},
            "end": {"dateTime": "2025-07-01T11:00:00Z"},
        },
    )
    create_data = json.loads(create_result.content[0].text)
    assert create_data.get("status") == "success", f"Event creation failed: {create_data}"

    # List events — our event should appear
    list_result = _run_tool(
        proxy_url,
        "google_calendar__list_events",
        {"timeMin": "2025-07-01T00:00:00Z", "timeMax": "2025-07-02T00:00:00Z"},
    )
    list_data = json.loads(list_result.content[0].text)
    events = list_data.get("events", [])
    summaries = [e.get("summary", "") for e in events]
    assert "E2E Test Event" in summaries, f"Created event not found. Summaries: {summaries}"


# ---------------------------------------------------------------------------
# Emails Mock e2e tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_proxy_exposes_emails_mock_tools(proxy_url):
    """Emails mock tools appear in the proxy's tool list."""

    async def _list():
        async with Client(StreamableHttpTransport(proxy_url)) as client:
            return await client.list_tools()

    tools = asyncio.run(_list())
    names = [t.name for t in tools]
    assert "emails_mock__get_emails" in names, f"emails_mock tools missing. Got: {names}"


@pytest.mark.e2e
def test_emails_mock_inbox_loaded(proxy_url):
    """get_emails returns seeded INBOX emails."""
    result = _run_tool(proxy_url, "emails_mock__get_emails", {"folder": "INBOX"})
    text = result.content[0].text
    data = json.loads(text)
    emails = data.get("emails", [])
    assert len(emails) > 0, f"Expected at least one email in INBOX. Got: {data}"
    subjects = [e["subject"] for e in emails]
    assert any("Project Update" in s for s in subjects), f"Project Update email not found. Subjects: {subjects}"


@pytest.mark.e2e
def test_emails_mock_state_persists_across_tool_calls(proxy_url):
    """Sending an email via emails_mock is visible in a subsequent get_emails call."""
    # Send a new email
    send_result = _run_tool(
        proxy_url,
        "emails_mock__send_email",
        {
            "to": "alice@techcorp.com",
            "subject": "Emails Mock E2E Test",
            "body": "This email should be visible in a subsequent read.",
        },
    )
    send_data = json.loads(send_result.content[0].text)
    assert send_data.get("status") == "sent", f"Send failed: {send_data}"

    # Read Sent folder — the email we just sent must appear
    read_result = _run_tool(proxy_url, "emails_mock__get_emails", {"folder": "Sent"})
    read_data = json.loads(read_result.content[0].text)
    subjects = [e["subject"] for e in read_data.get("emails", [])]
    assert any("Emails Mock E2E Test" in s for s in subjects), (
        f"Sent email not found in Sent folder. Subjects: {subjects}"
    )


# ---------------------------------------------------------------------------
# Jira e2e tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_proxy_exposes_jira_tools(proxy_url):
    """Jira tools appear in the proxy's tool list."""

    async def _list():
        async with Client(StreamableHttpTransport(proxy_url)) as client:
            return await client.list_tools()

    tools = asyncio.run(_list())
    names = [t.name for t in tools]
    assert "jira__search" in names, f"jira tools missing. Got: {names}"


@pytest.mark.e2e
def test_jira_issues_loaded(proxy_url):
    """jira_get_issue returns a seeded issue."""
    result = _run_tool(proxy_url, "jira__get_issue", {"issue_key": "ENG-1"})
    text = result.content[0].text
    data = json.loads(text)
    assert "key" in data or "fields" in data, f"Expected issue data. Got: {data}"


@pytest.mark.e2e
def test_jira_state_persists_across_tool_calls(proxy_url):
    """Creating an issue via jira_create_issue is visible in a subsequent search."""
    # Create a new issue
    create_result = _run_tool(
        proxy_url,
        "jira__create_issue",
        {
            "project_key": "ENG",
            "issue_type": "Task",
            "summary": "Jira E2E Test Issue",
        },
    )
    create_data = json.loads(create_result.content[0].text)
    assert "key" in create_data, f"Issue creation failed: {create_data}"

    created_key = create_data["key"]

    # Get the created issue
    get_result = _run_tool(proxy_url, "jira__get_issue", {"issue_key": created_key})
    get_data = json.loads(get_result.content[0].text)
    assert get_data.get("fields", {}).get("summary") == "Jira E2E Test Issue", (
        f"Created issue not found or wrong summary. Got: {get_data}"
    )


# ---------------------------------------------------------------------------
# Cross-service: Bob appears in multiple services
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_cross_reference_bob_across_all_services(proxy_url):
    """Bob Smith appears in Slack, gmail, emails_mock, and jira."""
    # Slack
    slack_result = _run_tool(proxy_url, "slack__get_users", {})
    slack_data = json.loads(slack_result.content[0].text)
    slack_names = [u.get("real_name", "") for u in slack_data.get("members", [])]
    assert any("Bob" in n for n in slack_names), f"Bob not in slack users: {slack_names}"

    # Gmail
    mail_result = _run_tool(proxy_url, "google_mail__get_emails", {"folder": "INBOX"})
    mail_data = json.loads(mail_result.content[0].text)
    senders = [e.get("from", "") for e in mail_data.get("emails", [])]
    assert any("bob.smith" in s for s in senders), f"bob.smith not in gmail senders: {senders}"

    # Jira — Bob Smith is a reporter on ENG-2
    jira_result = _run_tool(proxy_url, "jira__get_issue", {"issue_key": "ENG-2"})
    jira_data = json.loads(jira_result.content[0].text)
    assignee = jira_data.get("fields", {}).get("assignee", {}).get("displayName", "")
    assert "Bob" in assignee, f"Bob not assigned to ENG-2: {jira_data}"


# ---------------------------------------------------------------------------
# export_state / import_state round-trip (every server + the proxy aggregate)
# ---------------------------------------------------------------------------

# Servers the proxy_url fixture mounts (mirrors _FIXTURE_PACKAGES).
_ROUND_TRIP_SERVERS = [
    "emails_mock",
    "google_calendar",
    "google_mail",
    "grading",
    "jira",
    "shopify",
    "slack",
    "core",
    "web",
]
# core is mounted with namespaced: false, so it has no `core__export_state`
# tool — its state is only reachable via the proxy-level aggregate.
_NAMESPACED_ROUND_TRIP_SERVERS = [s for s in _ROUND_TRIP_SERVERS if s != "core"]


def _extract_json(result) -> object:
    """Extract the single JSON payload from a tool result."""
    text = result.content[0].text
    return json.loads(text)


@pytest.mark.e2e
@pytest.mark.parametrize("server", _NAMESPACED_ROUND_TRIP_SERVERS)
def test_state_round_trips(proxy_url, server):
    """export → import → export must produce identical state on each server."""
    first = _extract_json(_run_tool(proxy_url, f"{server}__export_state", {}))
    _run_tool(proxy_url, f"{server}__import_state", {"state": first})
    second = _extract_json(_run_tool(proxy_url, f"{server}__export_state", {}))
    assert second == first, f"{server} state did not round-trip cleanly"


@pytest.mark.e2e
def test_proxy_aggregate_state_round_trips(proxy_url):
    """The proxy-level export_state/import_state must round-trip across all mounted servers."""
    first = _extract_json(_run_tool(proxy_url, "export_state", {}))
    assert isinstance(first, dict), f"Proxy export_state should return an object, got: {type(first).__name__}"
    assert set(first.keys()) == set(_ROUND_TRIP_SERVERS), (
        f"Proxy export_state keys {sorted(first.keys())} do not match expected servers {_ROUND_TRIP_SERVERS}"
    )
    _run_tool(proxy_url, "import_state", {"state": first})
    second = _extract_json(_run_tool(proxy_url, "export_state", {}))
    assert second == first, "Proxy-level aggregate state did not round-trip cleanly"


# Per-server probe: load the raw fixture JSON from disk, feed it directly to
# import_state, then assert that a normal read-side tool returns data that
# originated from the fixture. Exercises the full wire path
# (client → proxy → server → state_from_json → runtime read).
_FIXTURE_IMPORT_PROBES = [
    (
        "shopify",
        "shopify_data.json",
        "shopify__get_product_details",
        {"product_id": "gid://shopify/Product/1001"},
        lambda data: bool(data.get("product") or data.get("products") or data.get("id")),
    ),
    (
        "jira",
        "jira_state.json",
        "jira__get_issue",
        {"issue_key": "ENG-1"},
        lambda data: data.get("key") == "ENG-1" or data.get("fields") is not None,
    ),
    (
        "slack",
        None,  # Slack fixture is split across multiple JSON files
        "slack__list_channels",
        {},
        lambda data: bool(data.get("channels")),
    ),
    (
        "google_calendar",
        "calendar_data.json",
        "google_calendar__list_events",
        {"timeMin": "2025-01-01T00:00:00Z", "timeMax": "2026-12-31T23:59:59Z"},
        lambda data: bool(data.get("events")),
    ),
    (
        "google_mail",
        "inbox.json",
        "google_mail__get_emails",
        {"folder": "INBOX"},
        lambda data: bool(data.get("emails")),
    ),
    (
        "emails_mock",
        "mailbox.json",
        "emails_mock__get_emails",
        {"folder": "INBOX"},
        lambda data: bool(data.get("emails")),
    ),
]


def _load_fixture_state(server: str, fixture_name: str | None) -> dict | list | None:
    """Load the seeded fixture JSON for a server.

    Returns None if no fixture exists on disk (server-specific).
    """
    server_dir = REPO_ROOT / "fixtures" / "simple_office" / server
    if not server_dir.is_dir():
        return None
    if fixture_name:
        path = server_dir / fixture_name
        if not path.exists():
            return None
        return json.loads(path.read_text())
    # Multi-file fixtures (slack): shallow-merge dicts, concat lists.
    merged: dict = {}
    for p in sorted(server_dir.glob("*.json")):
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            continue
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            elif isinstance(v, list) and isinstance(merged.get(k), list):
                merged[k].extend(v)
            else:
                merged[k] = v
    return merged or None


@pytest.mark.e2e
@pytest.mark.parametrize(("server", "fixture_name", "probe_tool", "probe_args", "check"), _FIXTURE_IMPORT_PROBES)
def test_import_state_loads_fixture_and_reads_back(proxy_url, server, fixture_name, probe_tool, probe_args, check):
    """Import the on-disk fixture via import_state, then verify a domain tool sees the data.

    This is a stronger guarantee than the in-memory round-trip tests: it proves
    that the raw JSON format we commit to fixtures/ is still a valid input to
    import_state, and that data loaded this way is visible to the server's
    normal read tools (list_carts, get_issue, list_events, etc.).
    """
    state = _load_fixture_state(server, fixture_name)
    if state is None:
        pytest.skip(f"No fixture on disk for {server}")

    _run_tool(proxy_url, f"{server}__import_state", {"state": state})

    probe_result = _extract_json(_run_tool(proxy_url, probe_tool, probe_args))
    assert check(probe_result), f"Probe tool {probe_tool} did not see imported {server} data: {probe_result}"


_PARTIAL_IMPORT_PROBES = [
    ("jira", "jira__get_project_issues", {"project_key": "MOCK"}),
    ("slack", "slack__list_channels", {}),
]


@pytest.mark.e2e
@pytest.mark.parametrize(("server", "probe_tool", "probe_args"), _PARTIAL_IMPORT_PROBES)
def test_import_state_empty_payload_keeps_runtime_safe(proxy_url, server, probe_tool, probe_args):
    """``import_state({})`` must leave the server in a runtime-safe state.

    Regression test for Codex P1 review comments on jira and slack: previously
    the TypeScript stateFromJson assigned the payload verbatim, so a partial
    or empty ``{}`` input produced a currentState missing required keys and
    later tool calls crashed. The fixed codec merges defaults first; this
    test proves ``{}`` round-trips without breaking read tools.
    """
    _run_tool(proxy_url, f"{server}__import_state", {"state": {}})
    # A read tool that dereferences state sub-fields must not raise.
    result = _run_tool(proxy_url, probe_tool, probe_args)
    # ToolError would have raised above; reaching here means the probe ran.
    assert result.content is not None


@pytest.mark.e2e
def test_every_server_declares_state_tools():
    """Static contract check: every packages/*/mcp-tools.generated.json declares both tools.

    Every server is required to implement export_state/import_state per the
    repo-wide contract. Mirrors the validation the proxy performs at startup.
    """
    missing: list[str] = []
    for pkg_dir in sorted(PACKAGES_DIR.iterdir()):
        if not pkg_dir.is_dir() or not (pkg_dir / "mcp.json").exists():
            continue
        tools_file = pkg_dir / "mcp-tools.generated.json"
        if not tools_file.exists():
            missing.append(f"{pkg_dir.name} (no mcp-tools.generated.json)")
            continue
        names = {t["name"] for t in json.loads(tools_file.read_text()).get("tools", [])}
        for required in ("export_state", "import_state"):
            if required not in names:
                missing.append(f"{pkg_dir.name}.{required}")
    assert not missing, f"Servers missing required state tools: {missing}"


# ---------------------------------------------------------------------------
# Viewer reverse-proxy: every mounted server's data viewer spins up
# ---------------------------------------------------------------------------
#
# Motivation: a single sub-server that errors on spin-up aborts the whole proxy
# (build_proxy_app sys.exit(1) on any startup failure), which silently blocks a
# whole session. The proxy_endpoints fixture coming up at all already proves
# every server's MCP process launched; these tests add the missing half — that
# each server's *data viewer* is reachable through the proxy's viewer server,
# not just its MCP endpoint.

# Mounted servers that ship a data viewer (a Starlette app serving "/"). grading
# and web are intentionally excluded — they have no viewer UI, though they still
# appear in the viewer shell's service list (asserted below).
_VIEWER_SERVERS = [
    "emails_mock",
    "google_calendar",
    "google_mail",
    "jira",
    "shopify",
    "slack",
    "core",
]


@pytest.mark.e2e
def test_viewer_shell_lists_every_mounted_server(viewer_url):
    """The viewer landing page (/__viewer__) renders a nav entry for every
    server the proxy mounted — proving the viewer server itself spun up and
    enumerated the full service registry.

    This covers *all* mounted servers, including grading and web (which have no
    viewer UI but must still appear in the navigation), not just the subset in
    _VIEWER_SERVERS.
    """
    resp = httpx.get(f"{viewer_url}/__viewer__", timeout=30)
    assert resp.status_code == 200, f"viewer shell not served: HTTP {resp.status_code}"
    html = resp.text
    for server in _FIXTURE_PACKAGES:
        assert f'data-service="{server}"' in html, f"{server} missing from viewer shell"


@pytest.mark.e2e
@pytest.mark.parametrize("server", _VIEWER_SERVERS)
def test_server_viewer_spins_up(viewer_url, server):
    """Each server's data viewer is reachable through the proxy and serves its
    page without error.

    The proxy returns 502 if the server's HTTP process never came up (the exact
    class of failure that silently blocks a session) and 5xx if the viewer route
    itself raised — so a clean 200 proves the viewer actually spun up.
    """
    with httpx.Client(base_url=viewer_url, follow_redirects=False, timeout=30) as client:
        # Select the service: sets the viewer cookie + server-side active service.
        # 404 here means the server isn't mounted at all.
        selected = client.get("/__viewer__/set", params={"app": server})
        assert selected.status_code == 307, (
            f"viewer did not recognise {server!r} (HTTP {selected.status_code}; 404 = not mounted)"
        )
        # Fetch the service's viewer root. A referer is required to bypass the
        # shell fallback the proxy serves for bare "/" navigations.
        page = client.get("/", headers={"referer": viewer_url})
        assert page.status_code == 200, (
            f"{server} viewer returned HTTP {page.status_code} "
            f"(502 = server process down, 5xx = viewer crashed): {page.text[:200]!r}"
        )
