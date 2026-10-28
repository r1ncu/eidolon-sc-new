"""Reading a file that may be edited while the process runs.

The rules a channel uses are data files, and the point of a data file is that it gets edited.
These tests are about the two things that make that safe: noticing the change, and refusing
the half-written version of it.
"""

import tempfile
import time
import unittest
from pathlib import Path

from core.live import Fixed, Live, source


class TestLive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "rules.md"
        self.path.write_text("第一版", encoding="utf-8")

    def write(self, text: str) -> None:
        """Write, and set the mtime to *now* so the change is visible even within a second."""
        self.path.write_text(text, encoding="utf-8")
        stamp = time.time()
        import os

        os.utime(self.path, (stamp, stamp))

    def test_the_first_read_does_not_wait_for_the_settle_window(self):
        live = Live(self.path, settle=5.0)
        self.assertEqual(live.text(), "第一版")

    def test_an_edited_file_is_picked_up(self):
        live = Live(self.path, settle=0.0)
        self.assertEqual(live.text(), "第一版")
        self.write("第二版")
        self.assertEqual(live.text(), "第二版")

    def test_an_empty_file_keeps_the_last_good_text(self):
        live = Live(self.path, settle=0.0)
        live.text()
        self.write("   ")
        self.assertEqual(live.text(), "第一版", "a truncating editor must not blank the rules")

    def test_a_missing_file_is_empty_and_does_not_raise(self):
        live = Live(self.root / "nope.md", settle=0.0)
        self.assertEqual(live.text(), "")

    def test_a_change_inside_the_settle_window_is_ignored_then_taken(self):
        live = Live(self.path, settle=1.0)
        live.text()
        self.write("第二版")  # written just now, so it looks half-finished
        self.assertEqual(live.text(), "第一版")
        live.refresh(force=True)
        self.assertEqual(live.text(), "第二版")

    def test_stamp_changes_with_the_file(self):
        live = Live(self.path, settle=0.0)
        before = live.stamp()
        self.write("第二版")
        self.assertNotEqual(before, live.stamp())


class TestSource(unittest.TestCase):
    def test_a_path_becomes_a_watched_file(self):
        made = source("/tmp/rules.md")
        self.assertIsInstance(made, Live)

    def test_text_with_newlines_is_taken_as_text(self):
        made = source("第一行\n第二行")
        self.assertIsInstance(made, Fixed)
        self.assertEqual(made.text(), "第一行\n第二行")

    def test_none_stays_none(self):
        self.assertIsNone(source(None))


if __name__ == "__main__":
    unittest.main()
