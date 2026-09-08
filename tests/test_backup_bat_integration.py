#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows-only integration tests for the real cmd.exe backup launcher."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402


@unittest.skipUnless(os.name == 'nt', '需要 Windows cmd.exe')
class TestBackupBatch(unittest.TestCase):
    def setUp(self):
        self.case = tempfile.mkdtemp(prefix='paxck-bat-')
        self.device_root = os.path.join(self.case, "测试 'quoted' dir — v2")
        os.makedirs(os.path.join(self.device_root, 'sub dir'))
        self._write('readme.txt', b'hello\n')
        self._write('empty.bin', b'')
        self._write('sub dir/binary-crlf.bin', bytes(range(256)) * 8)
        self.source = "/storage/emulated/0/测试 'quoted' dir — v2"
        self.out = os.path.join(self.case, 'out.tar.xz')
        self.log = os.path.join(self.case, 'adb.log')
        self.adb = os.path.join(self.case, 'fake-adb.cmd')
        self._write_adb_wrapper()
        self.config = os.path.join(self.case, 'backup.yaml')
        with open(self.config, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('# values deliberately loaded through the shared YAML path\n')
            fh.write('adb: "' + self.adb.replace('\\', '/') + '"\n')
            fh.write('adb_serial: 192.0.2.1:5555\n')
            fh.write('adb_connect: false\n')
            fh.write('source_dir: "' + self.source + '"\n')
            fh.write('out: "' + self.out.replace('\\', '/') + '"\n')
            fh.write('compress: gzip\n')

    def tearDown(self):
        shutil.rmtree(self.case, ignore_errors=True)

    def _write(self, relative, contents):
        path = os.path.join(self.device_root, *relative.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(contents)

    def _write_adb_wrapper(self):
        self.helper = os.path.join(os.path.dirname(__file__), 'fake_adb_windows.py')
        with open(self.adb, 'w', encoding='ascii', newline='\r\n') as fh:
            fh.write('@echo off\r\n')
            fh.write('"%TEST_PYTHON%" "%TEST_FAKE_ADB%" %*\r\n')

    def env(self, **overrides):
        env = dict(os.environ)
        env.update({
            'ADB': self.adb,
            'SOURCE_DIR': self.source,
            'OUT': self.out,
            'COMPRESS': 'xz',
            'PYTHON': sys.executable,
            'TEST_PYTHON': sys.executable,
            'TEST_FAKE_ADB': self.helper,
            'FAKE_ADB_ROOT': self.device_root,
            'FAKE_ADB_SOURCE': self.source,
            'FAKE_ADB_LOG': self.log,
            'BACKUP_CONFIG_FILE': os.path.join(self.case, 'no-config.yaml'),
        })
        env.pop('ADB_SERIAL', None)
        env.pop('ADB_CONNECT', None)
        env.pop('ANDROID_SERIAL', None)
        env.pop('FAKE_ADB_FAIL', None)
        env.pop('FAKE_ADB_TRUNCATE', None)
        env.update(overrides)
        return env

    def run_script(self, **overrides):
        args = overrides.pop('_args', ())
        return subprocess.run(
            [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', T.BACKUP_BAT, *args],
            cwd=self.case, env=self.env(**overrides), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=120)

    def log_text(self):
        with open(self.log, encoding='utf-8') as fh:
            return fh.read()

    def test_xz_round_trip_is_binary_safe(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout.decode('utf-8', 'replace'))
        self.assertTrue(os.path.isfile(self.out))
        self.assertEqual(T.run_cli(['verify', self.out])[0], 0)
        members = T.list_members(T.read_bytes(self.out))
        root = posix_basename(self.source)
        self.assertEqual(members[root + '/readme.txt'][2], b'hello\n')
        self.assertEqual(members[root + '/sub dir/binary-crlf.bin'][2],
                         bytes(range(256)) * 8)

    def test_shared_yaml_config_drives_batch_launcher(self):
        env = self.env(BACKUP_CONFIG_FILE=self.config)
        for key in ('ADB', 'ADB_SERIAL', 'ADB_CONNECT', 'SOURCE_DIR', 'OUT', 'COMPRESS'):
            env.pop(key, None)
        result = subprocess.run(
            [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', T.BACKUP_BAT],
            cwd=self.case, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        self.assertTrue(os.path.isfile(self.out))
        self.assertEqual(T.run_cli(['verify', self.out])[0], 0)

    def test_command_line_config_path_overrides_environment(self):
        env = self.env(BACKUP_CONFIG_FILE=os.path.join(self.case, 'missing.yaml'))
        for key in ('ADB', 'ADB_SERIAL', 'ADB_CONNECT', 'SOURCE_DIR', 'OUT', 'COMPRESS'):
            env.pop(key, None)
        result = subprocess.run(
            [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', T.BACKUP_BAT,
             '--config', self.config],
            cwd=self.case, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        self.assertEqual(T.run_cli(['verify', self.out])[0], 0)

    def test_gzip_and_plain_tar(self):
        for kind, filename in (('gzip', 'out.tar.gz'), ('none', 'out.tar')):
            target = os.path.join(self.case, filename)
            result = self.run_script(COMPRESS=kind, OUT=target)
            self.assertEqual(result.returncode, 0,
                             result.stdout.decode('utf-8', 'replace'))
            self.assertEqual(T.run_cli(['verify', target])[0], 0)

    def test_tcp_serial_is_forwarded_to_adb_children(self):
        serial = '192.0.2.1:5555'
        result = self.run_script(ADB_SERIAL=serial, ADB_CONNECT='1')
        self.assertEqual(result.returncode, 0, result.stdout.decode('utf-8', 'replace'))
        log = self.log_text()
        self.assertIn('connect ' + serial, log)
        self.assertIn('serial=' + serial, log)

    def test_usb_serial_is_forwarded_without_tcp_connect(self):
        """USB 设备可显式选择 serial，但不能触发无线 adb connect。"""
        serial = 'USB-SERIAL-001'
        result = self.run_script(ADB_SERIAL=serial, ADB_CONNECT='0')
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        log = self.log_text()
        self.assertIn('serial=' + serial, log)
        self.assertNotIn('connect ' + serial, log)

    def test_failed_transfer_keeps_target_and_removes_partial(self):
        original = b'previous verified backup'
        with open(self.out, 'wb') as fh:
            fh.write(original)
        result = self.run_script(FAKE_ADB_TRUNCATE='3')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(T.read_bytes(self.out), original)
        leftovers = [name for name in os.listdir(self.case)
                     if name.startswith('out.tar.xz.partial.')]
        self.assertEqual(leftovers, [])

    def test_unavailable_adb_fails_before_creating_output(self):
        result = self.run_script(FAKE_ADB_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.out))

    def test_unknown_compressor_is_rejected(self):
        result = self.run_script(COMPRESS='bad-compressor')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.out))


def posix_basename(path):
    return path.rstrip('/').rsplit('/', 1)[-1]


if __name__ == '__main__':
    unittest.main()
