#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for backup.py's OUT interpretation (pure logic, no adb/device)."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

backup = T.load_backup()
plan = backup._plan_out


class _FakeTty(io.StringIO):
    """A stdin that claims to be an interactive terminal."""

    def isatty(self):
        return True


@contextlib.contextmanager
def _tty_stdin():
    """Pretend to be an interactive terminal and capture stderr."""
    err = io.StringIO()
    with mock.patch.object(sys, 'stdin', _FakeTty()), \
            mock.patch.object(sys, 'stderr', err):
        yield err


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


class TestOutputProblem(unittest.TestCase):
    """Pre-flight target checks: a bad OUT must fail before any transfer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='paxck-outproblem-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_target_is_fine(self):
        self.assertIsNone(
            backup.output_problem(os.path.join(self.tmp, 'new.tar.zst')))

    def test_missing_parent_is_fine(self):
        # The placeholder creation creates parents; the check itself is silent.
        self.assertIsNone(
            backup.output_problem(os.path.join(self.tmp, 'a', 'b', 'x.tar')))

    def test_existing_regular_file_is_fine(self):
        target = os.path.join(self.tmp, 'have.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        self.assertIsNone(backup.output_problem(target))

    def test_existing_directory_is_reported(self):
        target = os.path.join(self.tmp, 'adir')
        os.makedirs(target)
        problem = backup.output_problem(target)
        self.assertIsNotNone(problem)
        self.assertIn(target, problem)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFOs only')
    def test_special_file_is_reported(self):
        target = os.path.join(self.tmp, 'pipe')
        os.mkfifo(target)
        problem = backup.output_problem(target)
        self.assertIsNotNone(problem)
        self.assertIn(target, problem)

    @unittest.skipUnless(os.name == 'nt', 'Windows read-only attribute')
    def test_read_only_file_is_reported_on_windows(self):
        target = os.path.join(self.tmp, 'ro.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        os.chmod(target, 0o444)
        try:
            problem = backup.output_problem(target)
        finally:
            os.chmod(target, 0o666)
        self.assertIsNotNone(problem)
        self.assertIn(target, problem)

    @unittest.skipIf(os.name == 'nt', 'POSIX replaces via the parent directory')
    def test_read_only_file_is_fine_on_posix(self):
        target = os.path.join(self.tmp, 'ro.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        os.chmod(target, 0o444)
        try:
            self.assertIsNone(backup.output_problem(target))
        finally:
            os.chmod(target, 0o666)


class TestPublishArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='paxck-publish-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _partial(self, data=b'archive'):
        path = os.path.join(self.tmp, 'out.tar.zst.partial.abc123')
        with open(path, 'wb') as fh:
            fh.write(data)
        return path

    def test_success_moves_the_file(self):
        partial = self._partial()
        target = os.path.join(self.tmp, 'out.tar.zst')
        self.assertIsNone(backup.publish_archive(partial, target))
        self.assertFalse(os.path.exists(partial))
        with open(target, 'rb') as fh:
            self.assertEqual(fh.read(), b'archive')

    def test_existing_file_is_replaced(self):
        partial = self._partial(b'new')
        target = os.path.join(self.tmp, 'out.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        self.assertIsNone(backup.publish_archive(partial, target))
        with open(target, 'rb') as fh:
            self.assertEqual(fh.read(), b'new')

    def test_failure_keeps_the_verified_archive(self):
        # A directory target cannot be replaced by a file on any platform.
        partial = self._partial()
        target = os.path.join(self.tmp, 'adir')
        os.makedirs(target)
        problem = backup.publish_archive(partial, target)
        self.assertIsNotNone(problem)
        self.assertIn(target, problem)
        self.assertIn(partial, problem)
        self.assertTrue(os.path.exists(partial), 'verified archive was lost')
        with open(partial, 'rb') as fh:
            self.assertEqual(fh.read(), b'archive')


class TestChooseOutputPath(unittest.TestCase):
    """_choose_output_path creates the placeholder before the transfer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='paxck-chooseout-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_good_target_returns_a_placeholder(self):
        target = os.path.join(self.tmp, 'out.tar.zst')
        chosen, partial = backup._choose_output_path(
            target, '/sdcard/x', 'zstd', 'quiet')
        self.assertEqual(chosen, target)
        self.assertTrue(os.path.exists(partial))
        self.assertEqual(os.path.dirname(partial), self.tmp)
        self.assertTrue(os.path.basename(partial).startswith(
            'out.tar.zst.partial.'))
        os.unlink(partial)

    def test_good_target_creates_missing_parents(self):
        target = os.path.join(self.tmp, 'a', 'b', 'out.tar.zst')
        chosen, partial = backup._choose_output_path(
            target, '/sdcard/x', 'zstd', 'quiet')
        self.assertEqual(chosen, target)
        self.assertTrue(os.path.exists(partial))
        os.unlink(partial)

    def test_unusable_target_raises_and_leaves_nothing_behind(self):
        target = os.path.join(self.tmp, 'adir')
        os.makedirs(target)
        with self.assertRaises(RuntimeError) as ctx:
            backup._choose_output_path(target, '/sdcard/x', 'zstd', 'quiet')
        self.assertIn(target, str(ctx.exception))
        self.assertEqual(os.listdir(self.tmp), ['adir'])

    def test_parent_that_is_a_file_raises(self):
        blocker = os.path.join(self.tmp, 'file')
        with open(blocker, 'wb') as fh:
            fh.write(b'x')
        target = os.path.join(blocker, 'out.tar.zst')
        with self.assertRaises(RuntimeError) as ctx:
            backup._choose_output_path(target, '/sdcard/x', 'zstd', 'quiet')
        self.assertIn(blocker, str(ctx.exception))

    def test_force_accepts_an_existing_target(self):
        target = os.path.join(self.tmp, 'out.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        chosen, partial = backup._choose_output_path(
            target, '/sdcard/x', 'zstd', 'quiet', force=True)
        self.assertEqual(chosen, target)
        self.assertTrue(os.path.exists(partial))
        os.unlink(partial)

    def test_existing_target_without_force_is_not_touched(self):
        target = os.path.join(self.tmp, 'out.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        with mock.patch.object(sys, 'stdin', io.StringIO('')):
            with self.assertRaises(RuntimeError) as ctx:
                backup._choose_output_path(target, '/sdcard/x', 'zstd', 'info')
        self.assertIn(target, str(ctx.exception))
        with open(target, 'rb') as fh:
            self.assertEqual(fh.read(), b'old')
        self.assertEqual(os.listdir(self.tmp), ['out.tar.zst'])

    def test_interactive_yes_overwrites(self):
        target = os.path.join(self.tmp, 'out.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        with _tty_stdin(), mock.patch('builtins.input', return_value='y'):
            chosen, partial = backup._choose_output_path(
                target, '/sdcard/x', 'zstd', 'info')
        self.assertEqual(chosen, target)
        self.assertTrue(os.path.exists(partial))
        os.unlink(partial)

    def test_interactive_no_offers_another_path(self):
        target = os.path.join(self.tmp, 'out.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        other = os.path.join(self.tmp, 'other.tar.zst')
        with _tty_stdin() as err, mock.patch(
                'builtins.input', side_effect=['n', other]):
            chosen, partial = backup._choose_output_path(
                target, '/sdcard/x', 'zstd', 'info')
        self.assertEqual(chosen, other)
        self.assertEqual(os.path.dirname(partial), self.tmp)
        self.assertTrue(os.path.basename(partial).startswith(
            'other.tar.zst.partial.'))
        self.assertIn('保留原有文件', err.getvalue())
        with open(target, 'rb') as fh:
            self.assertEqual(fh.read(), b'old')
        self.assertEqual(sorted(os.listdir(self.tmp)),
                         sorted(['out.tar.zst', os.path.basename(partial)]))
        os.unlink(partial)

    def test_interactive_no_then_enter_gives_up(self):
        target = os.path.join(self.tmp, 'out.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        with _tty_stdin(), mock.patch('builtins.input', side_effect=['n', '']):
            with self.assertRaises(RuntimeError):
                backup._choose_output_path(target, '/sdcard/x', 'zstd', 'info')
        with open(target, 'rb') as fh:
            self.assertEqual(fh.read(), b'old')
        self.assertEqual(os.listdir(self.tmp), ['out.tar.zst'])

    def test_unanswerable_overwrite_prompt_is_not_interactive(self):
        """Windows 把 NUL/DEVNULL 的 stdin 也算作 TTY：EOF 必须按非交互处理。"""
        target = os.path.join(self.tmp, 'out.tar.zst')
        with open(target, 'wb') as fh:
            fh.write(b'old')
        with _tty_stdin() as err, mock.patch('builtins.input',
                                             side_effect=EOFError):
            with self.assertRaises(RuntimeError) as ctx:
                backup._choose_output_path(target, '/sdcard/x', 'zstd', 'info')
        self.assertIn('--force', str(ctx.exception))
        # No "kept the file" notice and no second prompt path: it simply failed.
        self.assertNotIn('保留原有文件', err.getvalue())
        self.assertEqual(os.listdir(self.tmp), ['out.tar.zst'])


class TestOverwriteCheck(unittest.TestCase):
    """_target_problem: an existing target is never replaced silently."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='paxck-overwrite-')
        self.target = os.path.join(self.tmp, 'out.tar.zst')
        with open(self.target, 'wb') as fh:
            fh.write(b'previous verified backup')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_target_needs_no_confirmation(self):
        self.assertIsNone(backup._target_problem(
            os.path.join(self.tmp, 'new.tar.zst'), False))

    def test_force_accepts_an_existing_target(self):
        self.assertIsNone(backup._target_problem(self.target, True))

    def test_existing_target_hints_at_force(self):
        problem = backup._target_problem(self.target, False)
        self.assertIn(self.target, problem)
        self.assertIn('--force', problem)
        self.assertIn('-f', problem)

    def test_an_unusable_target_is_never_forced(self):
        # --force is about "do not ask", not about writing somewhere impossible.
        directory = os.path.join(self.tmp, 'adir')
        os.makedirs(directory)
        self.assertIsNotNone(backup._target_problem(directory, True))


class TestBooleanSetting(unittest.TestCase):
    def test_accepted_spellings(self):
        for value in ('1', 'true', 'TRUE', ' True ', 'yes', 'on', True):
            self.assertTrue(backup._enabled(value), repr(value))
        for value in ('0', 'false', 'no', 'off', '', None, 'maybe'):
            self.assertFalse(backup._enabled(value), repr(value))

    def test_yaml_force_key_maps_to_the_setting(self):
        with tempfile.TemporaryDirectory(prefix='paxck-force-yaml-') as tmp:
            path = os.path.join(tmp, 'backup.yaml')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('out: ./backups/\nforce: true\n')
            values = backup.read_config(path)
            self.assertEqual(values['FORCE'], '1')
            self.assertTrue(backup._enabled(values['FORCE']))


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
