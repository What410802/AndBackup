#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for backup.py's OUT interpretation (pure logic, no adb/device)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

backup = T.load_backup()
plan = backup._plan_out


class TestOutPlanning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='paxck-outplan-')
        self.source = '/storage/emulated/0/DCIM'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_defaults_to_backup_plus_suffix(self):
        for compress, suffix in (('xz', '.tar.xz'), ('gzip', '.tar.gz'),
                                 ('zstd', '.tar.zst'), ('none', '.tar')):
            path, confirm = plan('', self.source, compress)
            self.assertEqual(path, os.path.abspath('backup' + suffix), compress)
            self.assertFalse(confirm)

    def test_existing_directory_derives_name_from_source_tail(self):
        target = os.path.join(self.tmp, 'outdir')
        os.makedirs(target)
        path, confirm = plan(target, self.source + '/', 'xz')
        self.assertEqual(path, os.path.join(target, 'DCIM.tar.xz'))
        self.assertFalse(confirm)

    def test_trailing_separator_treated_as_directory(self):
        target = os.path.join(self.tmp, 'new', 'out') + os.sep
        path, confirm = plan(target, '/sdcard/测试.d', 'xz')
        self.assertEqual(path, os.path.join(os.path.join(self.tmp, 'new', 'out'),
                                            '测试.d.tar.xz'))
        self.assertFalse(confirm)

    def test_exact_suffix_file_is_unchanged(self):
        for compress, suffix in (('xz', '.tar.xz'), ('none', '.tar')):
            target = os.path.join(self.tmp, 'out' + suffix)
            path, confirm = plan(target, self.source, compress)
            self.assertEqual(path, os.path.abspath(target))
            self.assertFalse(confirm)

    def test_uppercase_suffix_matches(self):
        target = os.path.join(self.tmp, 'OUT.TAR.XZ')
        path, confirm = plan(target, self.source, 'xz')
        self.assertEqual(path, os.path.abspath(target))
        self.assertFalse(confirm)

    def test_mismatched_file_returns_needs_confirm(self):
        target = os.path.join(self.tmp, 'archive.bin')
        path, confirm = plan(target, self.source, 'xz')
        self.assertEqual(path, os.path.abspath(target))
        self.assertTrue(confirm)

    def test_partial_suffix_is_a_mismatch(self):
        # backup.tar does not match xz's theoretical .tar.xz suffix.
        target = os.path.join(self.tmp, 'backup.tar')
        path, confirm = plan(target, self.source, 'xz')
        self.assertEqual(path, os.path.abspath(target))
        self.assertTrue(confirm)


if __name__ == '__main__':
    unittest.main()
