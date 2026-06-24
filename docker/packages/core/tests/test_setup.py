"""Tests for core.setup — copies uploaded context files into the workdir."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import setup as setup_mod  # aliased to avoid clash with pytest's xunit setup_module fixture
from core.tools import sandbox


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.world_root = self.temp_dir / "world"
        self.workdir = self.temp_dir / "workdir"
        self.world_root.mkdir()

        self._original_workdir = sandbox.WORKDIR
        sandbox.WORKDIR = str(self.workdir)

    def tearDown(self):
        sandbox.WORKDIR = self._original_workdir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self, task_id: str | None = None) -> int:
        """Invoke setup.main() in-process and return its exit code (0 on normal return)."""
        env = {"WORLDBENCH_ROOT": str(self.world_root)}
        if task_id is not None:
            env["WORLDBENCH_TASK_ID"] = task_id
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("BUNDLEDIR", None)
            if task_id is None:
                os.environ.pop("WORLDBENCH_TASK_ID", None)
            try:
                setup_mod.main()
                return 0
            except SystemExit as e:
                return int(e.code or 0)

    def test_copies_task_specific_files_into_workdir(self):
        """Task-specific setup_data/{task_id}/files/ is copied into WORKDIR."""
        task_id = "task-abc"
        files_dir = self.world_root / "tasks" / "setup_data" / task_id / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "james.txt").write_text("james is the CEO")
        (files_dir / "nested").mkdir()
        (files_dir / "nested" / "notes.md").write_text("# notes")

        assert self._run(task_id=task_id) == 0

        assert (self.workdir / "james.txt").read_text() == "james is the CEO"
        assert (self.workdir / "nested" / "notes.md").read_text() == "# notes"

    def test_falls_back_to_generic_setup_data_when_task_dir_missing(self):
        """If no task-specific dir exists, copies from {world_root}/setup_data/files/."""
        generic_files = self.world_root / "setup_data" / "files"
        generic_files.mkdir(parents=True)
        (generic_files / "shared.txt").write_text("shared context")

        assert self._run(task_id="unknown-task") == 0
        assert (self.workdir / "shared.txt").read_text() == "shared context"

    def test_uses_generic_when_no_task_id(self):
        """With no WORLDBENCH_TASK_ID set, generic setup_data/files/ is used."""
        generic_files = self.world_root / "setup_data" / "files"
        generic_files.mkdir(parents=True)
        (generic_files / "default.txt").write_text("default")

        assert self._run(task_id=None) == 0
        assert (self.workdir / "default.txt").read_text() == "default"

    def test_merges_into_existing_workdir(self):
        """Existing workdir contents are preserved; new files are added alongside."""
        self.workdir.mkdir(parents=True)
        (self.workdir / "preexisting.txt").write_text("keep me")

        generic_files = self.world_root / "setup_data" / "files"
        generic_files.mkdir(parents=True)
        (generic_files / "new.txt").write_text("new file")

        assert self._run(task_id=None) == 0
        assert (self.workdir / "preexisting.txt").read_text() == "keep me"
        assert (self.workdir / "new.txt").read_text() == "new file"

    def test_no_setup_data_exits_cleanly(self):
        """If neither task-specific nor generic setup_data exists, exit 0 without error."""
        assert self._run(task_id="whatever") == 0

    def test_setup_data_without_files_subdir_is_noop(self):
        """setup_data/ with no files/ subdirectory is a no-op (does not error).

        Workdir IS created (ensure_workdir runs unconditionally so subsequent
        tools have something to chdir into), but no files are copied.
        """
        (self.world_root / "setup_data").mkdir()

        assert self._run(task_id=None) == 0

    def test_rejects_symlink_in_setup_files(self):
        """Symlinks in the source tree are refused — they'd let a bundle
        materialize an arbitrary host path (e.g. /app/tasks) into /workdir
        and then chown it to the model user."""
        files_dir = self.world_root / "setup_data" / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "real.txt").write_text("ok")
        # Target doesn't need to exist for the check to fire.
        os.symlink("/app/tasks", str(files_dir / "rubrics"))

        assert self._run(task_id=None) == 1
        assert not (self.workdir / "rubrics").exists()
        assert not (self.workdir / "real.txt").exists()  # copytree never ran

    def test_rejects_symlinked_files_dir_root(self):
        """A symlinked files/ root is rejected too."""
        real_dir = self.world_root / "setup_data" / "real_files"
        real_dir.mkdir(parents=True)
        (real_dir / "x.txt").write_text("x")
        os.symlink(str(real_dir), str(self.world_root / "setup_data" / "files"))

        assert self._run(task_id=None) == 1
        assert not (self.workdir / "x.txt").exists()

    def test_rejects_hardlink_in_setup_files(self):
        """Hardlinked files in the source tree are refused — they could
        point at a locked-down inode (e.g. /app/packages/grading/*) and
        slip past the symlink guard."""
        files_dir = self.world_root / "setup_data" / "files"
        files_dir.mkdir(parents=True)
        # Two paths sharing one inode: the "target" simulates a protected
        # file outside the bundle; the entry inside files/ is its hardlink.
        protected = self.temp_dir / "fake-protected.py"
        protected.write_text("rubric body")
        os.link(str(protected), str(files_dir / "rubric.py"))

        assert self._run(task_id=None) == 1
        assert not (self.workdir / "rubric.py").exists()

    def test_rejects_preexisting_workdir_symlink(self):
        """A symlink planted in /workdir before setup runs is refused —
        otherwise copytree(dirs_exist_ok=True) would write through it as
        root, into paths the model can't normally write."""
        files_dir = self.world_root / "setup_data" / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "context").mkdir()
        (files_dir / "context" / "doc.txt").write_text("payload")

        # Plant a model-controlled symlink in /workdir before setup.
        self.workdir.mkdir(parents=True)
        protected_target = self.temp_dir / "fake-protected-dir"
        protected_target.mkdir()
        os.symlink(str(protected_target), str(self.workdir / "context"))

        assert self._run(task_id=None) == 1
        # The bundle file must not have been written through the symlink.
        assert not (protected_target / "doc.txt").exists()

    def test_chowns_workdir_to_target_uid_gid_when_root(self):
        """Under (mocked) root, copied files are chowned back to SETUID:SETGID
        so the unprivileged model user can modify them."""
        generic_files = self.world_root / "setup_data" / "files"
        generic_files.mkdir(parents=True)
        (generic_files / "a.txt").write_text("hello")
        (generic_files / "sub").mkdir()
        (generic_files / "sub" / "b.txt").write_text("world")

        env = {"WORLDBENCH_ROOT": str(self.world_root), "SETUID": "1000", "SETGID": "1000"}
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch("core.privilege.os.geteuid", return_value=0),
            mock.patch("core.privilege.os.chown") as chown,
        ):
            os.environ.pop("BUNDLEDIR", None)
            os.environ.pop("WORLDBENCH_TASK_ID", None)
            setup_mod.main()

        chowned = sorted(call.args[0] for call in chown.call_args_list)
        # ensure_workdir chowns WORKDIR; chown_tree_to_target chowns it again plus
        # every entry under it. We only assert the post-copy entries are present.
        assert str(self.workdir / "a.txt") in chowned
        assert str(self.workdir / "sub") in chowned
        assert str(self.workdir / "sub" / "b.txt") in chowned
        for call in chown.call_args_list:
            assert call.args[1:] == (1000, 1000)


if __name__ == "__main__":
    unittest.main()
