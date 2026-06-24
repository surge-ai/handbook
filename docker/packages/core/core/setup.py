"""Copy uploaded context files into the agent's workdir.

Lookup order for the source directory:
    1. Unified bundle: $BUNDLEDIR/files/  (when a trajectory bundle is mounted)
    2. Task-specific:  {WORLDBENCH_ROOT}/tasks/setup_data/{WORLDBENCH_TASK_ID}/
    3. Generic:        {WORLDBENCH_ROOT}/setup_data/

BUNDLEDIR is set by the parent process (``scripts/start.sh`` for local
dev, the production harness in production) and points at the unpacked bundle root.

For (2)/(3) the source files live under a ``files/`` subdirectory; for (1)
the bundle root already has ``files/`` as a peer of ``services/``, ``memory/``,
and ``meta/``. In all cases, the contents of ``files/`` are copied into
``sandbox.WORKDIR`` (``/workdir``).
"""

import os
import shutil
import sys
from pathlib import Path

from core.privilege import chown_tree_to_target, ensure_workdir
from core.tools import sandbox


def _resolve_files_dir(world_root: Path, task_id: str | None) -> Path | None:
    """Return the source ``files/`` directory, or None if no setup data exists."""
    bundle_dir = os.environ.get("BUNDLEDIR")
    if bundle_dir:
        bundle_files = Path(bundle_dir) / "files"
        if bundle_files.is_dir():
            return bundle_files

    task_setup_dir = world_root / "tasks" / "setup_data" / task_id if task_id else None
    if task_setup_dir and task_setup_dir.exists():
        setup_dir = task_setup_dir
    else:
        setup_dir = world_root / "setup_data"
        if not setup_dir.exists():
            return None

    files_dir = setup_dir / "files"
    return files_dir if files_dir.is_dir() else None


def main() -> None:
    # Setup runs entirely as root: the source tree (e.g. /app/setup_data/files/,
    # extracted by the production harness as root) is not readable by uid 1000, so we copy
    # before dropping privileges and then chown the result to SETUID/SETGID so
    # the model user can modify it. This script then exits — the actual MCP
    # server is a separate process invocation that does its own privilege drop.
    ensure_workdir()
    task_id = os.environ.get("WORLDBENCH_TASK_ID")
    world_root = Path(os.environ.get("WORLDBENCH_ROOT", os.getcwd()))

    files_dir = _resolve_files_dir(world_root, task_id)
    if files_dir is None:
        print("No setup data found, skipping")
        sys.exit(0)

    # Refuse symlinks and hardlinks anywhere in the source tree, and refuse
    # symlinks already present at the destination. We run as root here (so
    # we can read locked-down sources the model user can't see) and chown
    # the destination to uid 1000 afterwards. Three ways the protected
    # tree could be leaked back to the model otherwise:
    #   1. Source symlink `files/rubrics -> /app/tasks` — shutil.copytree's
    #      default `symlinks=False` dereferences it and materializes the
    #      target into /workdir.
    #   2. Source hardlink `files/foo` to `/app/packages/grading/x.py` —
    #      copytree sees a regular file with the same inode and writes a
    #      new copy into /workdir.
    #   3. Destination symlink `/workdir/context -> /app/packages/grading`
    #      planted by a prior model run — copytree's `dirs_exist_ok=True`
    #      follows it and writes bundle contents *into* the protected
    #      directory as root.
    workdir = Path(sandbox.WORKDIR)
    _reject_unsafe_source(files_dir)
    workdir.mkdir(parents=True, exist_ok=True)
    _reject_dest_symlinks(workdir)
    shutil.copytree(files_dir, workdir, dirs_exist_ok=True)
    chown_tree_to_target(workdir)
    print(f"Copied files from {files_dir} to {workdir}")


def _reject_unsafe_source(root: Path) -> None:
    """Exit non-zero if any entry under ``root`` is a symlink or a hardlink.

    Hardlinks are detected via ``st_nlink > 1`` on regular files. Files in
    the bundle should be unique copies; multi-link inodes mean the bundle
    points at something we didn't create, which might be a protected path
    on the same filesystem.
    """
    if root.is_symlink():
        print(f"Refusing to copy symlinked setup root: {root}", file=sys.stderr)
        sys.exit(1)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            entry = Path(dirpath) / name
            if entry.is_symlink():
                print(f"Refusing to copy symlink in setup data: {entry}", file=sys.stderr)
                sys.exit(1)
        for name in filenames:
            entry = Path(dirpath) / name
            # lstat — don't follow symlinks (already rejected above, but be defensive).
            st = entry.lstat()
            if st.st_nlink > 1:
                print(f"Refusing to copy hardlinked file in setup data: {entry}", file=sys.stderr)
                sys.exit(1)


def _reject_dest_symlinks(root: Path) -> None:
    """Exit non-zero if any entry under destination ``root`` is a symlink.

    A symlink planted in /workdir by a prior model run would let
    ``copytree(..., dirs_exist_ok=True)`` write through it as root, into
    paths the model user can't normally write — or, after chown, read.
    """
    if root.is_symlink():
        print(f"Refusing to copy into symlinked workdir: {root}", file=sys.stderr)
        sys.exit(1)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            entry = Path(dirpath) / name
            if entry.is_symlink():
                print(f"Refusing to copy over symlink in workdir: {entry}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    main()
