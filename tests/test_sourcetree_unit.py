#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for sourcetree.py's record framing and progress (no adb)."""
import io
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

tree = T.load_sourcetree()
i18n = tree.i18n


class _FakeProc:
    """Stand-in for the Popen `adbdevice.open_adb_shell` returns."""

    def __init__(self, stdout: bytes, stderr: bytes = b'',
                 returncode: int = 0):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode

    def wait(self):
        return self.returncode


class TestStatRecords(unittest.TestCase):
    def test_seven_field_record_is_parsed(self):
        info = tree._parse_stat_record(
            '41ed|3452|1788797977|2026-09-08 00:19:37.011092854 +0800|'
            '2770|10142|1023|/storage/emulated/0/Documents')
        self.assertEqual(info['path'], '/storage/emulated/0/Documents')
        self.assertEqual(info['size'], 3452)
        self.assertEqual(info['perm'], 0o2770)
        self.assertEqual((info['uid'], info['gid']), ('10142', '1023'))
        self.assertEqual(tree._tree_type_char(info['mode']), 'd')

    def test_basic_record_has_no_owner_columns(self):
        info = tree._parse_stat_record(
            '81a4|7|1788797977|2026-09-08 00:19:37.011092854 +0800|644|/a/b.txt')
        self.assertEqual(info['path'], '/a/b.txt')
        self.assertIsNone(info['uid'])
        self.assertIsNone(info['gid'])
        self.assertEqual(tree._tree_type_char(info['mode']), '-')

    def test_a_pipe_inside_the_path_is_kept(self):
        info = tree._parse_stat_record(
            '81a4|7|1788797977|2026-09-08 00:19:37.011092854 +0800|644|10142'
            '|1023|/data/od|d name.txt')
        self.assertEqual(info['path'], '/data/od|d name.txt')
        self.assertEqual(info['size'], 7)
        self.assertEqual((info['uid'], info['gid']), ('10142', '1023'))

    def test_a_pipe_inside_a_basic_record_path_is_kept(self):
        info = tree._parse_stat_record(
            '81a4|7|1788797977|2026-09-08 00:19:37.011092854 +0800|644'
            '|/data/od|d name.txt', with_owner=False)
        self.assertEqual(info['path'], '/data/od|d name.txt')
        self.assertIsNone(info['uid'])

    def test_a_tab_inside_the_path_is_kept(self):
        info = tree._parse_stat_record(
            '81a4|7|1788797977|2026-09-08 00:19:37.011092854 +0800|644|'
            '10142|1023|/data/ta\tb.txt')
        self.assertEqual(info['path'], '/data/ta\tb.txt')

    def test_garbage_is_rejected(self):
        for text in ('', 'not a record', 'x|y', '81a4|z|1|2|644|a|b|/p'):
            self.assertIsNone(tree._parse_stat_record(text, False), text)
            self.assertIsNone(tree._parse_stat_record(text, True), text)


class TestStreamFraming(unittest.TestCase):
    def test_newline_records_carry_the_trailer(self):
        seen = []
        proc = _FakeProc(b'one\ntwo\nthree\0__ANDBACKUP_RC__0\0')
        rc, _detail = tree._consume_stream(proc, b'\n', seen.append)
        self.assertEqual(seen, [b'one', b'two', b'three'])
        self.assertEqual(rc, 0)

    def test_nul_records_include_the_trailer_as_a_record(self):
        seen = []
        proc = _FakeProc(b'a\0b\0__ANDBACKUP_RC__7\0')
        rc, _detail = tree._consume_stream(proc, b'\0', seen.append)
        self.assertEqual(seen, [b'a', b'b'])
        self.assertEqual(rc, 7)

    def test_missing_trailer_reports_no_rc(self):
        seen = []
        proc = _FakeProc(b'a\0b\0')
        rc, _detail = tree._consume_stream(proc, b'\0', seen.append)
        self.assertIsNone(rc)

    def test_stderr_is_collected_for_diagnostics(self):
        seen = []
        proc = _FakeProc(b'a\0\0__ANDBACKUP_RC__1\0', stderr=b'boom\n')
        rc, detail = tree._consume_stream(proc, b'\0', seen.append)
        self.assertEqual(rc, 1)
        self.assertEqual(detail, 'boom')


class TestProgress(unittest.TestCase):
    def setUp(self):
        i18n.set_language('en')
        self.addCleanup(i18n.set_language, None)

    def _progress(self, log_level='info', interval='5'):
        with mock.patch.object(tree.paxck, 'LiveLine') as live:
            live.return_value.stream = io.StringIO()
            live.return_value.live = False
            return tree._TreeProgress(log_level, interval), live.return_value

    def test_quiet_and_error_never_print(self):
        for level in ('quiet', 'error'):
            progress, live = self._progress(level)
            progress.note('backup.tree.progress.stat', done=1, total=2)
            self.assertFalse(live.update.called, level)

    def test_notes_are_throttled_by_the_interval(self):
        progress, live = self._progress('info', '5')
        with mock.patch.object(tree.time, 'monotonic',
                               side_effect=[100.0, 101.0, 106.0]):
            progress.note('backup.tree.progress.stat', done=1, total=3)
            progress.note('backup.tree.progress.stat', done=2, total=3)
            progress.note('backup.tree.progress.stat', done=3, total=3)
        self.assertEqual(live.update.call_count, 2)

    def test_the_message_is_translated_and_tagged(self):
        progress, live = self._progress('info', '0.1')
        progress.note('backup.tree.progress.stat', done=3, total=9)
        text = live.update.call_args[0][0]
        self.assertTrue(text.startswith(i18n.tag('progress')), text)
        self.assertIn('3/9', text)
        progress.clear()
        self.assertTrue(live.clear.called)


if __name__ == '__main__':
    unittest.main()
