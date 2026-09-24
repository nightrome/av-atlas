#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for atomic_write.py: the new content lands in full, and a write that
fails partway leaves the old file exactly as it was, with no temp file
left behind.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import atomic_write as aw


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.dir = Path(tmpdir.name)
        self.target = self.dir / "papers_full.json"

    def test_writes_the_same_bytes_as_the_old_write_text_calls(self):
        data = [{"title": "Über Planning", "authors": ["A"]}]
        aw.write_json_atomic(self.target, data, indent=2)
        expected = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.assertEqual(self.target.read_bytes(), expected)
        self.assertNotIn(b"\r\n", self.target.read_bytes())

    def test_no_indent_matches_stats_json_format(self):
        aw.write_json_atomic(self.target, {"a": [1, 2]})
        self.assertEqual(self.target.read_text(encoding="utf-8"), '{"a": [1, 2]}')

    def test_replaces_an_existing_file(self):
        self.target.write_text("old", encoding="utf-8")
        aw.write_json_atomic(self.target, ["new"])
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), ["new"])
        self.assertEqual(list(self.dir.iterdir()), [self.target])

    def test_failed_write_keeps_the_old_file_and_cleans_up(self):
        self.target.write_text('["old"]', encoding="utf-8")
        with mock.patch.object(aw.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                aw.write_json_atomic(self.target, ["new"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), '["old"]')
        self.assertEqual(list(self.dir.iterdir()), [self.target])

    def test_interrupt_during_write_keeps_the_old_file(self):
        # Ctrl+C in the middle of a crawler's periodic save.
        self.target.write_text('["old"]', encoding="utf-8")
        with mock.patch.object(aw.os, "replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                aw.write_json_atomic(self.target, ["new"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), '["old"]')
        self.assertEqual(list(self.dir.iterdir()), [self.target])

    def test_unserializable_data_never_touches_the_disk(self):
        self.target.write_text('["old"]', encoding="utf-8")
        with self.assertRaises(TypeError):
            aw.write_json_atomic(self.target, [object()])
        self.assertEqual(self.target.read_text(encoding="utf-8"), '["old"]')
        self.assertEqual(list(self.dir.iterdir()), [self.target])

    def test_retries_a_briefly_locked_target(self):
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(src)
            if len(calls) == 1:
                raise PermissionError("in use")
            return real_replace(src, dst)

        with mock.patch.object(aw.os, "replace", side_effect=flaky_replace), \
                mock.patch.object(aw, "REPLACE_RETRY_DELAY", 0):
            aw.write_json_atomic(self.target, ["ok"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), ["ok"])


if __name__ == "__main__":
    unittest.main()
