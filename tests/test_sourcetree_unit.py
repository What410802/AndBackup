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


class _Recorder:
    """Progress stand-in that only records what it was told."""

    def __init__(self):
        self.notes = []

    def note(self, message_key, **kwargs):
        self.notes.append((message_key, kwargs))


class TestDeviceListing(unittest.TestCase):
    """The NUL-framed records `tree_device.py` sends back in one write."""

    def setUp(self):
        self.progress = _Recorder()
        self.listing = tree.DeviceListing(self.progress)

    def feed(self, *records):
        for record in records:
            self.listing.record(record)

    def test_fields_then_path_build_one_entry(self):
        self.feed('41ed|3452|1788797977|2026-09-08 00:19:37.011092854 +0800'
                  '|2770|10142|1023', '/storage/emulated/0/Documents')
        entry = self.listing.entries['/storage/emulated/0/Documents']
        self.assertEqual(entry['size'], 3452)
        self.assertEqual(entry['perm'], 0o2770)
        self.assertEqual((entry['uid'], entry['gid']), ('10142', '1023'))
        self.assertEqual(tree._tree_type_char(entry['mode']), 'd')
        self.assertEqual(self.listing.count, 1)

    def test_records_may_arrive_as_bytes(self):
        self.feed(b'81a4|1|1788797977|2026-09-08 00:19:37.011092854 +0800'
                  b'|644|0|0', b'/tmp/a')
        self.assertEqual(self.listing.entries['/tmp/a']['size'], 1)

    def test_a_newline_and_a_pipe_in_a_name_survive(self):
        # The reason this strategy exists: NUL framing cannot be fooled by a
        # name that would split the newline-framed shell one-shot.
        names = ['/tmp/new\nline', '/tmp/pi|pe', '/tmp/ta\tb']
        for name in names:
            self.feed('81a4|3|1788797977|2026-09-08 00:19:37.011092854 '
                      '+0800|600|0|0', name)
        self.assertEqual(sorted(self.listing.entries), sorted(names))

    def test_progress_records_only_report(self):
        self.feed('\x01P200')
        self.assertEqual(self.progress.notes,
                         [('backup.tree.progress.device', {'done': '200'})])
        self.assertEqual(self.listing.entries, {})

    def test_an_unreadable_entry_keeps_its_place(self):
        self.feed('\x02U/storage/emulated/0/Android/data/app/private')
        self.assertEqual(self.listing.unreadable,
                         ['/storage/emulated/0/Android/data/app/private'])
        entry = self.listing.entries[
            '/storage/emulated/0/Android/data/app/private']
        self.assertIsNone(entry['mode'])

    def test_a_link_record_attaches_to_the_entry_before_it(self):
        self.feed('a1ff|4|1788797977|2026-09-08 00:19:37.011092854 +0800'
                  '|777|0|0', '/tmp/link', '\x03L/tmp/target')
        self.assertEqual(self.listing.links, {'/tmp/link': '/tmp/target'})

    def test_a_garbage_fields_record_is_dropped_not_fatal(self):
        self.feed('not a record', '/tmp/a')
        self.feed('81a4|1|1788797977|2026-09-08 00:19:37.011092854 +0800'
                  '|644|0|0', '/tmp/b')
        self.assertEqual(list(self.listing.entries), ['/tmp/b'])
        self.assertEqual(self.listing.count, 1)


class TestDeviceTimezone(unittest.TestCase):
    def test_the_offset_is_flipped_for_posix_tz(self):
        self.assertEqual(tree._posix_timezone('2026-09-08 00:19:37 +0800'),
                         'UTC-8')
        self.assertEqual(tree._posix_timezone('2026-09-08 00:19:37 -0430'),
                         'UTC+4:30')
        self.assertEqual(tree._posix_timezone('2026-09-08 00:19:37 +0000'),
                         'UTC-0')

    def test_an_unusable_probe_answer_is_none(self):
        for text in ('', '   ', 'Permission denied', 'stat: not found'):
            self.assertIsNone(tree._posix_timezone(text), text)


if __name__ == '__main__':
    unittest.main()