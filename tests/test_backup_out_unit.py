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


class TestDownloadDefault(unittest.TestCase):
    def test_device_python_defaults_to_true(self):
        self.assertTrue(
            backup._download_device_python_setting({}, 'device-python'))

    def test_host_adb_defaults_to_false(self):
        self.assertFalse(
            backup._download_device_python_setting({}, 'host-adb'))

    def test_explicit_value_wins(self):
        self.assertFalse(backup._download_device_python_setting(
            {'DOWNLOAD_DEVICE_PYTHON': 'false'}, 'device-python'))
        self.assertTrue(backup._download_device_python_setting(
            {'DOWNLOAD_DEVICE_PYTHON': 'true'}, 'host-adb'))


class TestMatchHost(unittest.TestCase):
    def test_exact_ip_prefix_and_mdns_matching(self):
        devices = [('192.0.2.1:5555', 'device'),
                   ('USB-1', 'device'),
                   ('adb-X-._adb-tls-connect._tcp', 'device')]
        self.assertEqual(backup._match_host(devices, '192.0.2.1'),
                         ['192.0.2.1:5555'])
        self.assertEqual(backup._match_host(devices, '192.0.2.1:5555'),
                         ['192.0.2.1:5555'])
        self.assertEqual(
            backup._match_host(devices, 'adb-X-._adb-tls-connect._tcp'),
            ['adb-X-._adb-tls-connect._tcp'])
        self.assertEqual(backup._match_host(devices, '10.0.0.9'), [])

    def test_offline_entries_are_ignored(self):
        self.assertEqual(
            backup._match_host([('192.0.2.1:5555', 'offline')], '192.0.2.1'),
            [])


if __name__ == '__main__':
    unittest.main()
