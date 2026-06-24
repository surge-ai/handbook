"""Tests for the unified-bundle layout in core.setup.

When ``$BUNDLEDIR/files/`` is mounted (the production harness unpacks the unified
trajectory bundle there), core.setup should copy from that path instead
of the legacy ``setup_data/files/`` location. We point ``BUNDLEDIR`` at a
temp path so the test doesn't depend on the production mount path.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import setup as setup_mod  # aliased to avoid clash with pytest's xunit setup_module fixture
from core.tools import sandbox


class BundleLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.world_root = self.temp_dir / "world"
        self.workdir = self.temp_dir / "workdir"
        self.bundle_dir = self.temp_dir / "bundle"
        self.world_root.mkdir()

        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "WORLDBENCH_ROOT": str(self.world_root),
                "BUNDLEDIR": str(self.bundle_dir),
            },
            clear=False,
        )
        self._env_patch.start()
        os.environ.pop("WORLDBENCH_TASK_ID", None)

        self._original_workdir = sandbox.WORKDIR
        sandbox.WORKDIR = str(self.workdir)

    def tearDown(self):
        sandbox.WORKDIR = self._original_workdir
        self._env_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_bundle_files_take_precedence_over_legacy_setup_data(self):
        """When $BUNDLEDIR/files/ exists, it's used instead of setup_data/files/."""
        bundle_files = self.bundle_dir / "files"
        bundle_files.mkdir(parents=True)
        (bundle_files / "from_bundle.txt").write_text("from bundle")

        legacy_files = self.world_root / "setup_data" / "files"
        legacy_files.mkdir(parents=True)
        (legacy_files / "from_legacy.txt").write_text("from legacy")

        setup_mod.main()

        assert (self.workdir / "from_bundle.txt").read_text() == "from bundle"
        assert not (self.workdir / "from_legacy.txt").exists()

    def test_falls_back_to_legacy_when_bundle_dir_absent(self):
        """When BUNDLEDIR points at a path that doesn't exist on disk, the
        legacy setup_data path still works."""
        # bundle_dir intentionally not created
        legacy_files = self.world_root / "setup_data" / "files"
        legacy_files.mkdir(parents=True)
        (legacy_files / "from_legacy.txt").write_text("from legacy")

        setup_mod.main()

        assert (self.workdir / "from_legacy.txt").read_text() == "from legacy"

    def test_falls_back_to_legacy_when_bundledir_unset(self):
        """When BUNDLEDIR isn't set at all (common local-dev path), the
        legacy setup_data path is used."""
        os.environ.pop("BUNDLEDIR", None)
        legacy_files = self.world_root / "setup_data" / "files"
        legacy_files.mkdir(parents=True)
        (legacy_files / "from_legacy.txt").write_text("from legacy")

        setup_mod.main()

        assert (self.workdir / "from_legacy.txt").read_text() == "from legacy"

    def test_bundle_and_legacy_produce_equivalent_workdir(self):
        """Same files served via either layout yield the same WORKDIR contents."""
        contents = {"a.txt": "alpha", "nested/b.md": "# beta"}

        # First run: bundle layout
        bundle_files = self.bundle_dir / "files"
        for rel, body in contents.items():
            p = bundle_files / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)

        setup_mod.main()
        bundle_snapshot = {
            str(p.relative_to(self.workdir)): p.read_text() for p in self.workdir.rglob("*") if p.is_file()
        }

        # Second run: same files via legacy layout, fresh workdir
        shutil.rmtree(self.bundle_dir)
        shutil.rmtree(self.workdir, ignore_errors=True)
        legacy_files = self.world_root / "setup_data" / "files"
        for rel, body in contents.items():
            p = legacy_files / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)

        setup_mod.main()
        legacy_snapshot = {
            str(p.relative_to(self.workdir)): p.read_text() for p in self.workdir.rglob("*") if p.is_file()
        }

        assert bundle_snapshot == legacy_snapshot


if __name__ == "__main__":
    unittest.main()
