"""Tests for ``core.privilege`` — the workdir-ownership helpers
(``ensure_workdir``, ``chown_tree_to_target``) used by ``core.setup``.

The per-command privilege drop lives in ``core.tools.sandbox`` now (the
server stays root and drops each agent command instead of the whole process);
its tests are in ``test_agent_env.py``.
"""

from __future__ import annotations

from unittest import mock

from core import privilege


def _patch_root(is_root: bool):
    return mock.patch("core.privilege.os.geteuid", return_value=0 if is_root else 1000)


# ---------------------------------------------------------------------------
# ensure_workdir
# ---------------------------------------------------------------------------


def test_ensure_workdir_creates_and_chowns_when_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUID", "1000")
    monkeypatch.setenv("SETGID", "1000")
    target = tmp_path / "workdir-new"
    with (
        _patch_root(True),
        mock.patch("core.privilege.os.chown") as chown,
        mock.patch("core.privilege.sandbox.WORKDIR", str(target)),
    ):
        privilege.ensure_workdir()
    assert target.is_dir()
    chown.assert_called_once_with(str(target), 1000, 1000)


def test_ensure_workdir_noop_when_not_root(tmp_path):
    """Non-root can't mkdir at / and can't chown, so the whole function is a
    no-op — WORKDIR must already exist (or the caller will fail loudly)."""
    target = tmp_path / "workdir-missing"
    with (
        _patch_root(False),
        mock.patch("core.privilege.os.chown") as chown,
        mock.patch("core.privilege.sandbox.WORKDIR", str(target)),
    ):
        privilege.ensure_workdir()
    assert not target.exists()
    chown.assert_not_called()


def test_ensure_workdir_skips_chown_when_env_vars_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("SETUID", raising=False)
    monkeypatch.delenv("SETGID", raising=False)
    target = tmp_path / "workdir"
    with (
        _patch_root(True),
        mock.patch("core.privilege.os.chown") as chown,
        mock.patch("core.privilege.sandbox.WORKDIR", str(target)),
    ):
        privilege.ensure_workdir()
    assert target.is_dir()
    chown.assert_not_called()


# ---------------------------------------------------------------------------
# chown_tree_to_target
# ---------------------------------------------------------------------------


def _make_tree(root):
    """Build a small tree: root/a.txt, root/sub/b.txt, root/sub/c.txt."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.txt").write_text("a")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("b")
    (root / "sub" / "c.txt").write_text("c")


def test_chown_tree_recursively_when_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUID", "1000")
    monkeypatch.setenv("SETGID", "1000")
    target = tmp_path / "workdir"
    _make_tree(target)
    with _patch_root(True), mock.patch("core.privilege.os.chown") as chown:
        privilege.chown_tree_to_target(target)
    paths = sorted(call.args[0] for call in chown.call_args_list)
    expected = sorted(
        [
            str(target),
            str(target / "a.txt"),
            str(target / "sub"),
            str(target / "sub" / "b.txt"),
            str(target / "sub" / "c.txt"),
        ]
    )
    assert paths == expected
    for call in chown.call_args_list:
        assert call.args[1:] == (1000, 1000)
        assert call.kwargs == {"follow_symlinks": False}


def test_chown_tree_noop_when_not_root(tmp_path, monkeypatch):
    """Local dev / CI: same env vars, but no real privilege to chown — skip."""
    monkeypatch.setenv("SETUID", "1000")
    monkeypatch.setenv("SETGID", "1000")
    target = tmp_path / "workdir"
    _make_tree(target)
    with _patch_root(False), mock.patch("core.privilege.os.chown") as chown:
        privilege.chown_tree_to_target(target)
    chown.assert_not_called()


def test_chown_tree_noop_when_env_vars_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("SETUID", raising=False)
    monkeypatch.delenv("SETGID", raising=False)
    target = tmp_path / "workdir"
    _make_tree(target)
    with _patch_root(True), mock.patch("core.privilege.os.chown") as chown:
        privilege.chown_tree_to_target(target)
    chown.assert_not_called()


def test_chown_tree_uses_minus_one_for_missing_uid_or_gid(tmp_path, monkeypatch):
    """When only one of SETUID/SETGID is set, the other side is left unchanged
    via the chown ``-1`` sentinel."""
    monkeypatch.setenv("SETUID", "1000")
    monkeypatch.delenv("SETGID", raising=False)
    target = tmp_path / "workdir"
    target.mkdir()
    (target / "f.txt").write_text("x")
    with _patch_root(True), mock.patch("core.privilege.os.chown") as chown:
        privilege.chown_tree_to_target(target)
    for call in chown.call_args_list:
        assert call.args[1:] == (1000, -1)
