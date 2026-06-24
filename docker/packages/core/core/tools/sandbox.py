import os
import subprocess
from collections.abc import Iterator
from typing import Any, cast

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 1800

# Agent's working directory. Created in docker/base/Dockerfile and scripts/start.sh,
# owned by the model user. Tests reassign this module attribute to point at a temp
# dir, so the annotation is `str` rather than the inferred `Literal["/workdir"]`.
WORKDIR: str = "/workdir"

# MCP_PROXY_TOKEN is injected directly into core's env by the proxy (see
# mcp_proxy/service.py:start_service) so core's own HTTP server can
# authenticate inbound requests on non-MCP routes. The credential-name filter
# in mcp_proxy does NOT catch this, so we strip it here to avoid leaking it to
# the agent's bash/python.
#
# FAKETIME_SHARED is exported by libfaketime when the proxy runs services under
# the `faketime` wrapper for --current-time. It names a /dev/shm semaphore +
# shared-memory segment that libfaketime created as root (mode 0600), used for
# cross-process monotonic-time coordination. The agent command runs as the
# model uid, so a child it spawns (bash) re-initializes libfaketime,
# can't open that root-owned semaphore, and *hangs* on the very first clock read
# (`date`, time.time(), ...). Stripping it makes libfaketime run per-process —
# the clock is still faked (LD_PRELOAD + FAKETIME are kept); we only lose the
# cross-process "time never goes backward" guarantee, which a sandbox doesn't
# need.
_PROXY_INTERNAL_ENV_KEYS = frozenset({"MCP_PROXY_TOKEN", "FAKETIME_SHARED"})

# Stripped from every agent-spawned subprocess so the server venv (/opt/venv)
# and repo (/app) can't leak into the model's shell.
_AGENT_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_AGENT_PATH_STRIP_PREFIXES = ("/opt/venv", "/app")
_AGENT_ENV_DROP = (
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
    "PYTHONPATH",
    "PYTHONHOME",
    "SETUID",
    "SETGID",
)

# Harness-internal vars that point at the locked-down /app tree, name the task,
# carry the run seed, or expose internal service ports. The agent's shell has no
# legitimate use for them, and leaking them advertises the eval layout (paths
# like INPUTDIR=/app/setup_data/…), the seed, and the viewer/proxy ports even
# though those resources are themselves locked down. Matched by PREFIX so a
# newly-added WORLDBENCH_*/BUNDLE* var is dropped automatically rather than
# silently leaking until someone remembers to list it.
_AGENT_ENV_DROP_PREFIXES = ("WORLDBENCH_", "BUNDLE")
# Exact harness vars without a shared prefix.
_AGENT_ENV_DROP_EXACT = ("INPUTDIR", "OUTPUTDIR", "PORT", "VIEWER_PORT")
# Survives the prefix sweep: the fake clock must reach the agent's shell.
_AGENT_ENV_KEEP = frozenset({"WORLDBENCH_CURRENT_TIME"})


def _sanitize_path(path: str) -> str:
    """Drop PATH entries under /opt/venv or /app, preserving the rest in order
    (e.g. a derived image's /opt/conda/bin). Falls back to a default if empty."""

    def _stripped(entry: str) -> bool:
        norm = os.path.normpath(entry)
        return any(norm == p or norm.startswith(p + os.sep) for p in _AGENT_PATH_STRIP_PREFIXES)

    kept = [entry for entry in path.split(os.pathsep) if entry and not _stripped(entry)]
    return os.pathsep.join(kept) if kept else _AGENT_DEFAULT_PATH


def _agent_env() -> dict[str, str]:
    """Inherited env with server-side venv pointers, harness-internal paths, and
    proxy-internal credentials stripped — the single chokepoint every
    agent command spawn flows through. Copies os.environ so the shell
    keeps a real /home/<user>."""
    env = os.environ.copy()
    for var in (*_AGENT_ENV_DROP, *_AGENT_ENV_DROP_EXACT, *_PROXY_INTERNAL_ENV_KEYS):
        env.pop(var, None)
    for key in list(env):
        if key not in _AGENT_ENV_KEEP and key.startswith(_AGENT_ENV_DROP_PREFIXES):
            del env[key]
    env["PATH"] = _sanitize_path(env.get("PATH", ""))
    return env


def _privilege_drop_kwargs() -> dict[str, Any]:
    """``subprocess`` kwargs that drop an agent command to the unprivileged
    sandbox user, or ``{}`` when there's nothing to drop.

    core's server process stays **root** in production so it can read the
    locked-down ``/app`` tree and keep ``/opt/venv`` closed to uid 1000. Every
    agent-facing command (``bash``/file tools) MUST
    therefore drop to ``SETUID``/``SETGID`` itself — a command left running as
    root is a sandbox escape (it could read ``/app``, the grading data, the
    venv, ``/__modal``).

    We use subprocess's ``user``/``group``/``extra_groups`` kwargs rather than a
    ``preexec_fn``: they perform the ``setgroups``/``setgid``/``setuid`` in C
    between fork and exec, which is async-signal-safe and avoids the
    multithreaded-fork deadlock ``preexec_fn`` is prone to under the async MCP
    server.

    * **Not root** (local dev, CI, tests): nothing to drop to — return ``{}``.
    * **Root, both env vars set**: return the drop kwargs.
    * **Root, env vars missing/partial**: raise. Fail closed rather than exec
      the agent's command as root.
    """
    if os.geteuid() != 0:
        return {}
    setuid = os.environ.get("SETUID")
    setgid = os.environ.get("SETGID")
    if setuid is None or setgid is None:
        raise RuntimeError(
            "core is running as root but SETUID/SETGID are not both set "
            f"(SETUID={setuid!r}, SETGID={setgid!r}); refusing to run an agent command as root"
        )
    # extra_groups=[] clears supplementary groups so the dropped command can't
    # inherit any group membership root happened to carry.
    return {"user": int(setuid), "group": int(setgid), "extra_groups": []}


# Stdlib-only reader run AS THE SANDBOX USER (uid 1000) via run_in_sandbox. The
# open() happens in the dropped subprocess, so filesystem permissions — not the
# root server — gate access: a path under /app, /opt/venv, /__modal, etc. fails
# with EACCES exactly as in the agent's own shell, and a symlink swap can't
# escalate a root read (the resolve+open are one atomic op as uid 1000, so this
# is TOCTOU-safe). Emits one JSON line so the privileged caller can reuse its
# normal in-memory processing on bytes it never opened itself.
_AGENT_READ_WINDOW_SNIPPET = r"""
import sys, os, stat, base64, json
path, offset, limit, sniff = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
try:
    # O_NONBLOCK + S_ISREG on the opened fd: refuse FIFOs/devices/sockets, whose
    # open()/read() can block on a writer that never comes (TOCTOU-safe — the
    # check is on the fd, not the path).
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise OSError("not a regular file")
    size = st.st_size
    with os.fdopen(fd, "rb") as f:
        header = f.read(min(sniff, size)) if sniff > 0 else b""
        start = min(offset, size) if offset >= 0 else 0
        f.seek(start)
        raw = f.read() if limit < 0 else f.read(limit)
    sys.stdout.write(json.dumps({
        "ok": True, "size": size, "start": start,
        "header": base64.b64encode(header).decode(),
        "raw": base64.b64encode(raw).decode(),
    }))
except Exception as e:
    sys.stdout.write(json.dumps({"ok": False, "error": str(e)}))
"""


class AgentReadError(Exception):
    """A read performed as the sandbox user failed (missing file, EACCES, …)."""


def agent_read_window(
    path: str,
    *,
    offset: int = 0,
    limit: int | None = None,
    sniff: int = 0,
) -> tuple[int, bytes, bytes, int]:
    """Read ``path`` AS THE SANDBOX USER and return ``(size, header, raw, start)``.

    ``raw`` is the ``limit`` bytes (all, if ``None``) at ``offset``; ``header`` is
    the first ``sniff`` bytes (for binary detection), or empty when ``sniff`` is
    0. Use this anywhere the (now root) server would otherwise ``open()`` an
    agent-supplied path in-process. Raises :class:`AgentReadError` on failure.
    """
    lim = -1 if limit is None else int(limit)
    result = run_in_sandbox(
        ["python3", "-c", _AGENT_READ_WINDOW_SNIPPET, path, str(offset), str(lim), str(sniff)],
        DEFAULT_TIMEOUT_SECONDS,
    )
    if result["returncode"] != 0:
        raise AgentReadError(result.get("stderr") or result.get("error") or "agent read failed")
    import json as _json

    try:
        data = _json.loads(result["stdout"])
    except (ValueError, TypeError) as e:
        raise AgentReadError(f"agent reader returned invalid output: {e}") from e
    if not data.get("ok"):
        raise AgentReadError(data.get("error", "agent read failed"))
    import base64 as _b64

    return data["size"], _b64.b64decode(data["header"]), _b64.b64decode(data["raw"]), data["start"]


# Raw byte-copier run AS THE SANDBOX USER. Unlike _AGENT_READ_WINDOW_SNIPPET it
# does NOT base64/JSON-wrap the payload — it copies the file straight to stdout
# so the parent can stream it without inflating or buffering the whole thing.
_AGENT_STREAM_SNIPPET = r"""
import sys, os, stat
path = sys.argv[1]
try:
    # O_NONBLOCK so opening a FIFO/socket with no writer can't block, and reject
    # any non-regular file (FIFO, device, socket, dir): reading one could hang
    # forever waiting on a writer. The check is on the opened fd (fstat), so it's
    # TOCTOU-safe against a path swapped after open.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            sys.stderr.write("not a regular file")
            sys.exit(1)
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
    finally:
        os.close(fd)
except Exception as e:
    sys.stderr.write(str(e))
    sys.exit(1)
"""


def agent_stream_file(
    path: str,
    *,
    chunk_size: int = 1 << 16,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Iterator[bytes]:
    """Yield ``path``'s bytes in ``chunk_size`` blocks, read AS THE SANDBOX USER,
    without ever holding the whole file in the root server.

    Same isolation model as :func:`agent_read_window` — a uid-1000 subprocess
    does the ``open()`` (filesystem perms gate access, TOCTOU-safe) — but the
    bytes are streamed straight through instead of materialized. Use for
    whole-file reads where buffering the entire payload would be wasteful (e.g.
    viewer downloads of large ``/workdir`` files).

    Raises :class:`AgentReadError` if the read fails, whether ``open()`` fails
    before any bytes are produced or the subprocess dies mid-stream. An empty
    file yields nothing and is not an error. The subprocess is always reaped
    (killed if the consumer stops iterating early).
    """
    os.makedirs(WORKDIR, exist_ok=True)
    # python3 (not sys.executable): the agent's subprocess runs as uid 1000 and
    # must use the system interpreter on the sanitized PATH — /opt/venv is locked
    # to root. Same partial-path resolution as run_in_sandbox.
    command = ["python3", "-c", _AGENT_STREAM_SNIPPET, path]
    # cast: the **Any privilege-drop kwargs make the type checker pick Popen's
    # text-mode overload, but we pass no text/encoding so this is binary — the
    # pipes carry bytes.
    proc = cast(
        "subprocess.Popen[bytes]",
        subprocess.Popen(
            command,
            cwd=WORKDIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_agent_env(),
            **_privilege_drop_kwargs(),
        ),
    )
    assert proc.stdout is not None
    assert proc.stderr is not None
    try:
        while True:
            chunk = proc.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk
        returncode = proc.wait(timeout=timeout)
        if returncode != 0:
            err = proc.stderr.read().decode("utf-8", errors="replace").strip()
            raise AgentReadError(err or "agent read failed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


_AGENT_LIST_DIR_SNIPPET = r"""
import sys, os, json
path = sys.argv[1]
try:
    entries = []
    with os.scandir(path) as it:
        for e in it:
            try:
                is_dir = e.is_dir()
                size = None if is_dir else e.stat().st_size
            except OSError:
                continue
            entries.append({"name": e.name, "is_dir": is_dir, "size": size})
    sys.stdout.write(json.dumps({"ok": True, "entries": entries}))
except Exception as e:
    sys.stdout.write(json.dumps({"ok": False, "error": str(e)}))
"""


def agent_list_dir(path: str) -> list[dict[str, Any]]:
    """List ``path`` AS THE SANDBOX USER, returning ``[{name, is_dir, size}, …]``.

    Companion to :func:`agent_read_window` for the (root) server's directory
    listings — the ``scandir`` runs as uid 1000 so it can't enumerate /app etc.
    Raises :class:`AgentReadError` on failure.
    """
    result = run_in_sandbox(["python3", "-c", _AGENT_LIST_DIR_SNIPPET, path], DEFAULT_TIMEOUT_SECONDS)
    if result["returncode"] != 0:
        raise AgentReadError(result.get("stderr") or result.get("error") or "agent list failed")
    import json as _json

    try:
        data = _json.loads(result["stdout"])
    except (ValueError, TypeError) as e:
        raise AgentReadError(f"agent lister returned invalid output: {e}") from e
    if not data.get("ok"):
        raise AgentReadError(data.get("error", "agent list failed"))
    return data["entries"]


def run_in_sandbox(
    command: list[str],
    timeout: int,
    input: str | bytes | None = None,
    *,
    text: bool = True,
) -> dict[str, Any]:
    """
    Run a command in the sandbox environment.

    Args:
        command: The command to run as a list of arguments.
        timeout: Timeout in seconds.
        input: Optional string or bytes to pass to stdin. Must be bytes when
            text=False.
        text: If True (default), stdout/stderr are decoded as UTF-8 strings.
            If False, they are returned as raw bytes — useful for binary file
            payloads.

    Returns:
        Dict with stdout, stderr, and returncode.
    """
    workdir = WORKDIR
    os.makedirs(workdir, exist_ok=True)

    env = _agent_env()
    drop_kwargs = _privilege_drop_kwargs()

    try:
        result = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            text=text,
            timeout=timeout,
            input=input,
            env=env,
            **drop_kwargs,
        )
    except subprocess.TimeoutExpired as e:
        if text:
            stdout = (
                (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            )
            stderr = (
                (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            )
        else:
            stdout = e.stdout or b""
            stderr = e.stderr or b""
        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": -1,
            "error": f"Command timed out after {timeout} seconds",
        }
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }
