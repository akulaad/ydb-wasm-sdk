import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("sdk_export", Path(__file__).with_name("export.py"))
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


class ExportTests(unittest.TestCase):
    def test_only_source_inputs_are_selected(self):
        self.assertEqual(export.parse_files("file: File $S/util/a.h\nfile: File $B/generated.h\nfile: MissingFile $U/stdio.h\n"), {"util/a.h"})

    def test_path_traversal_rejected(self):
        for path in ("../outside", "/etc/passwd", ".git/config", "foo/../../bar", "foo//bar"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                export.safe_path(path)

    def test_recurse_pruning(self):
        data = b"LIBRARY()\nRECURSE(keep absent)\nRECURSE_FOR_TESTS(ut)\nRECURSE_ROOT_RELATIVE(shared missing)\nEND()\n"
        actual = export.prune_recurses(data, "lib/ya.make", {"lib/keep/ya.make", "shared/ya.make"})
        self.assertIn(b"    keep", actual)
        self.assertIn(b"    shared", actual)
        self.assertNotIn(b"absent", actual)
        self.assertNotIn(b"RECURSE_FOR_TESTS", actual)

    def test_sync_is_reproducible_and_deletes_only_owned_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "user.txt").write_text("owned by user")
            result = {"vendor/a": (b"one", "100644", "upstream")}
            export.synchronize(root, result, {"upstream_revision": "abc"}, "spec")
            first = (root / export.MANIFEST).read_bytes()
            export.synchronize(root, result, {"upstream_revision": "abc"}, "spec")
            self.assertEqual(first, (root / export.MANIFEST).read_bytes())
            export.synchronize(root, {}, {"upstream_revision": "def"}, "spec")
            self.assertFalse((root / "vendor/a").exists())
            self.assertEqual((root / "user.txt").read_text(), "owned by user")

    def test_local_edit_stops_entire_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export.synchronize(root, {"a": (b"original", "100644", "upstream")}, {"upstream_revision": "abc"}, "spec")
            (root / "a").write_text("manual edit")
            with self.assertRaises(ValueError):
                export.synchronize(root, {"b": (b"new", "100644", "upstream")}, {"upstream_revision": "def"}, "spec")
            self.assertEqual((root / "a").read_text(), "manual edit")
            self.assertFalse((root / "b").exists())

    def test_unowned_collision_stops_before_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export.synchronize(root, {"a": (b"old", "100644", "upstream")}, {"upstream_revision": "abc"}, "spec")
            (root / "b").write_text("user file")
            with self.assertRaises(ValueError):
                export.synchronize(root, {"b": (b"new", "100644", "upstream")}, {"upstream_revision": "def"}, "spec")
            self.assertTrue((root / "a").exists())

    def test_symlink_destination_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sdk"
            root.mkdir()
            (root / "link").symlink_to(Path(tmp), target_is_directory=True)
            with self.assertRaises(ValueError):
                export.destination(root, "link/outside")


if __name__ == "__main__":
    unittest.main()
