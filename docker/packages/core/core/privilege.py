"""Workdir/ownership helpers for core's root-server + per-exec-drop model.

Configured via ``packages/core/mcp.json``'s ``env`` block:

    "env": {"SETUID": "1000", "SETGID": "1000", "HOME": "/home/model"}

**The core server process stays root.** Sibling servers (slack, jira,
google_mail, grading, …) also run as root because their tools just call mocked
APIs over HTTP — no shell, no file IO, no adversarial surface. Core is
different: it hands the agent ``bash``, so each of *those commands* is dropped
to ``SETUID``/``SETGID`` at spawn time
(``core.tools.sandbox._privilege_drop_kwargs``), while the server itself
keeps root. Keeping the server root (rather than dropping the whole process to
uid 1000, as it used to) is what lets the Dockerfile close ``/opt/venv`` to uid
1000: the server reads its venv as root, and the agent — a *different* effective
uid per command — cannot. ``/app`` stays sealed to ``0700 root:root`` and the
venv lives outside ``/app`` at ``/opt/venv``. See ``docker/base/Dockerfile`` and
the README's "Sandbox model" section.

This module keeps the bits that still run in the root server: provisioning
``WORKDIR`` and restoring ownership of files copied out of the root-owned source
tree into the model-owned workdir. The actual privilege drop now happens
per-command in ``sandbox`` — there is intentionally no function here that drops
the whole process.

When the process isn't root (local dev, CI, tests) these helpers are no-ops:
they can't ``mkdir`` at ``/`` or ``chown``, and the same ``mcp.json`` ships
``SETUID``/``SETGID`` everywhere harmlessly.
"""

from __future__ import annotations

import os

from core.tools import sandbox


def _target_ids() -> tuple[int, int] | None:
    """Return ``(uid, gid)`` from ``SETUID``/``SETGID`` env, or ``None`` if neither is set.

    A missing one becomes ``-1`` (the ``os.chown`` sentinel for "leave unchanged").
    """
    setuid = os.environ.get("SETUID")
    setgid = os.environ.get("SETGID")
    if setuid is None and setgid is None:
        return None
    uid = int(setuid) if setuid is not None else -1
    gid = int(setgid) if setgid is not None else -1
    return uid, gid


def ensure_workdir() -> None:
    """Make sure WORKDIR exists with the right owner BEFORE we drop privileges.

    Some deploy environments don't pre-provision /workdir (the Dockerfile +
    start.sh do, but bundle launchers / Modal workflows may not). The setup
    hook needs to write into it, and the server needs to chdir there — so we
    create it as root and chown to SETUID/SETGID before the drop, since uid
    1000 can't mkdir at /.

    Non-root callers (CI, local dev, tests) can't mkdir at / and can't chown,
    so this is a no-op — WORKDIR there is either pre-provisioned (Docker, test
    fixtures pointing at tmp dirs) or genuinely absent, in which case any
    caller that actually needs the dir will fail loudly on its own.
    """
    if os.geteuid() != 0:
        return
    workdir = sandbox.WORKDIR
    os.makedirs(workdir, exist_ok=True)
    ids = _target_ids()
    if ids is None:
        return
    os.chown(workdir, *ids)


def chown_tree_to_target(path: str | os.PathLike[str]) -> None:
    """Recursively chown ``path`` to ``SETUID``/``SETGID``. No-op when not root.

    Used by the setup hook after copying root-owned source files into
    model-owned WORKDIR — ``shutil.copytree`` runs as root (so it can read
    locked-down sources) and the new entries inherit root ownership; this
    restores them to the unprivileged target user so the model can edit them.
    Symlinks are chowned without following.
    """
    if os.geteuid() != 0:
        return
    ids = _target_ids()
    if ids is None:
        return
    uid, gid = ids
    root_str = os.fspath(path)
    os.chown(root_str, uid, gid, follow_symlinks=False)
    for root, dirs, files in os.walk(root_str):
        for name in dirs + files:
            os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)
