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
        self.remote_root = os.path.join(self.case, 'remote')
        os.makedirs(self.remote_root)
        self.device_python = os.path.join(self.case, 'android-python')
        with open(self.device_python, 'wb') as fh:
            fh.write(b'fake standalone python')
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
            'FAKE_ADB_REMOTE_ROOT': self.remote_root,
            'FAKE_ADB_LOG': self.log,
            'BACKUP_CONFIG_FILE': os.path.join(self.case, 'no-config.yaml'),
        })
        env.pop('ADB_SERIAL', None)
        env.pop('ADB_CONNECT', None)
        env.pop('ANDROID_SERIAL', None)
        env.pop('FAKE_ADB_FAIL', None)
        env.pop('FAKE_ADB_TRUNCATE', None)
        # Operational keys a developer may have exported in their shell must not
        # leak into tests that do not set them explicitly (e.g. SOURCE_MODE).
        env.pop('SOURCE_MODE', None)
        env.pop('DEVICE_PYTHON', None)
        env.pop('DOWNLOAD_DEVICE_PYTHON', None)
        env.pop('DEVICE_PYTHON_URL', None)
        env.pop('KEEP_ANDROID_ENV', None)
        env.pop('LOG_LEVEL', None)
        env.pop('PROGRESS_INTERVAL', None)
        env.pop('SHOW_RATE', None)
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

    def test_unknown_source_mode_is_rejected(self):
        result = self.run_script(SOURCE_MODE='bad-source-mode')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.out))

    def test_device_python_requires_device_python_path(self):
        result = self.run_script(SOURCE_MODE='device-python')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.out))
        leftovers = [name for name in os.listdir(self.case)
                     if name.startswith('out.tar.xz.partial.')]
        self.assertEqual(leftovers, [])

    def test_device_python_mode_uploads_and_round_trips(self):
        result = self.run_script(SOURCE_MODE='device-python',
                                 DEVICE_PYTHON=self.device_python,
                                 KEEP_ANDROID_ENV='0')
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        self.assertEqual(T.run_cli(['verify', self.out])[0], 0)
        members = T.list_members(T.read_bytes(self.out))
        root = posix_basename(self.source)
        self.assertEqual(members[root + '/readme.txt'][2], b'hello\n')
        self.assertFalse(any('andbackup-' in name for _, dirs, files in os.walk(self.remote_root)
                             for name in dirs + files))
        log = self.log_text()
        self.assertIn('push', log)
        self.assertIn('shell mkdir -p', log)

    def test_device_python_failure_does_not_fallback_and_cleans_remote(self):
        original = b'previous verified backup'
        with open(self.out, 'wb') as fh:
            fh.write(original)
        result = self.run_script(SOURCE_MODE='device-python',
                                 DEVICE_PYTHON=self.device_python,
                                 FAKE_ADB_DEVICE_PYTHON_FAIL='1',
                                 KEEP_ANDROID_ENV='0')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(T.read_bytes(self.out), original)
        self.assertFalse(any('andbackup-' in name for _, dirs, files in os.walk(self.remote_root)
                             for name in dirs + files))
        # Device-python mode must not invoke adb_source.py's find/stat path.
        log = self.log_text()
        self.assertNotIn('find ', log)

    def test_device_python_prefix_directory_uploads_and_round_trips(self):
        """DEVICE_PYTHON may point at a python prefix dir (bin/ + lib/)."""
        prefix = os.path.join(self.case, 'android-pyenv')
        bin_dir = os.path.join(prefix, 'bin')
        lib_dir = os.path.join(prefix, 'lib', 'python3.14')
        os.makedirs(bin_dir)
        os.makedirs(lib_dir)
        interp = os.path.join(bin_dir, 'python3.14')
        with open(interp, 'wb') as fh:
            fh.write(b'fake static python (not executed by fake adb)')
        with open(os.path.join(lib_dir, 'os.py'), 'wb') as fh:
            fh.write(b'# fake stdlib marker\n')

        result = self.run_script(SOURCE_MODE='device-python',
                                 DEVICE_PYTHON=prefix,
                                 KEEP_ANDROID_ENV='0')
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        self.assertEqual(T.run_cli(['verify', self.out])[0], 0)
        members = T.list_members(T.read_bytes(self.out))
        root = posix_basename(self.source)
        self.assertEqual(members[root + '/readme.txt'][2], b'hello\n')
        # No leftover andbackup-* temporary directory on the "device".
        self.assertFalse(any('andbackup-' in name for _, dirs, files in os.walk(self.remote_root)
                             for name in dirs + files))
        log = self.log_text()
        self.assertIn('push', log)
        self.assertIn('python.tar', log)
        self.assertIn('shell tar -xf', log)


def posix_basename(path):
    return path.rstrip('/').rsplit('/', 1)[-1]


@unittest.skipUnless(os.name == 'nt', '需要 Windows cmd.exe')
class TestDeviceEnvCaching(unittest.TestCase):
    """Offline coverage for the cached Android device-python environment."""

    def _make_case(self):
        case = tempfile.mkdtemp(prefix='paxck-cache-')
        device_root = os.path.join(case, "测试 'quoted' dir — v2")
        os.makedirs(os.path.join(device_root, 'sub'))
        with open(os.path.join(device_root, 'readme.txt'), 'wb') as fh:
            fh.write(b'hello\n')
        remote_root = os.path.join(case, 'remote')
        os.makedirs(remote_root)
        prefix = os.path.join(case, 'android-pyenv')
        os.makedirs(os.path.join(prefix, 'bin'))
        os.makedirs(os.path.join(prefix, 'lib', 'python3.14'))
        with open(os.path.join(prefix, 'bin', 'python3.14'), 'wb') as fh:
            fh.write(b'# fake static python\n')
        with open(os.path.join(prefix, 'lib', 'python3.14', 'os.py'),
                  'wb') as fh:
            fh.write(b'# mock\n')
        return case, device_root, remote_root, prefix

    def _run(self, case, device_root, remote_root, prefix, *extra_args,
             **env_over):
        helper = os.path.join(os.path.dirname(__file__), 'fake_adb_windows.py')
        adb = os.path.join(case, 'fake-adb.cmd')
        with open(adb, 'w', encoding='ascii', newline='\r\n') as fh:
            fh.write('@echo off\r\n')
            fh.write('"%TEST_PYTHON%" "%TEST_FAKE_ADB%" %*\r\n')
        log = os.path.join(case, 'adb.log')
        source = "/storage/emulated/0/测试 'quoted' dir — v2"
        env = dict(os.environ)
        env.update({
            'ADB': adb,
            'SOURCE_DIR': source,
            'OUT': os.path.join(case, 'out.tar.xz'),
            'COMPRESS': 'xz',
            'SOURCE_MODE': 'device-python',
            'DEVICE_PYTHON': prefix,
            'PYTHON': sys.executable,
            'TEST_PYTHON': sys.executable,
            'TEST_FAKE_ADB': helper,
            'FAKE_ADB_ROOT': device_root,
            'FAKE_ADB_SOURCE': source,
            'FAKE_ADB_REMOTE_ROOT': remote_root,
            'FAKE_ADB_LOG': log,
            'BACKUP_CONFIG_FILE': os.path.join(case, 'no-config.yaml'),
        })
        for key in ('ADB_SERIAL', 'ADB_CONNECT', 'ANDROID_SERIAL',
                    'FAKE_ADB_FAIL', 'FAKE_ADB_TRUNCATE',
                    'DOWNLOAD_DEVICE_PYTHON', 'DEVICE_PYTHON_URL',
                    'KEEP_ANDROID_ENV', 'LOG_LEVEL', 'PROGRESS_INTERVAL',
                    'SHOW_RATE'):
            env.pop(key, None)
        env.update(env_over)
        return subprocess.run(
            [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', T.BACKUP_BAT,
             *extra_args], cwd=case, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=120), log

    @staticmethod
    def _env_dir(remote_root):
        # fake adb maps device /data/local/tmp/... under FAKE_ADB_REMOTE_ROOT.
        return os.path.join(remote_root, 'data', 'local', 'tmp',
                            'andbackup-pyenv')

    def _log_tail(self, log, head_len):
        with open(log, encoding='utf-8') as fh:
            text = fh.read()
        return text[head_len:]

    def test_env_kept_then_reused_without_reupload(self):
        case, device_root, remote_root, prefix = self._make_case()
        try:
            r1, log = self._run(case, device_root, remote_root, prefix,
                                KEEP_ANDROID_ENV='1')
            self.assertEqual(r1.returncode, 0, r1.stdout.decode('utf-8', 'replace'))
            self.assertTrue(os.path.isdir(self._env_dir(remote_root)))
            head = len(open(log, encoding='utf-8').read())
            r2, _ = self._run(case, device_root, remote_root, prefix,
                              KEEP_ANDROID_ENV='1')
            self.assertEqual(r2.returncode, 0, r2.stdout.decode('utf-8', 'replace'))
            log2 = self._log_tail(log, head)
            self.assertNotIn('tar -xf', log2)
            self.assertNotIn(' python.tar', log2)
        finally:
            shutil.rmtree(case, ignore_errors=True)

    def test_env_removed_by_default_in_non_interactive(self):
        case, device_root, remote_root, prefix = self._make_case()
        try:
            r, _ = self._run(case, device_root, remote_root, prefix)
            self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
            # stdin is not a TTY under subprocess: env removed by default.
            self.assertFalse(os.path.exists(self._env_dir(remote_root)))
        finally:
            shutil.rmtree(case, ignore_errors=True)

    def test_stale_env_is_replaced(self):
        case, device_root, remote_root, prefix = self._make_case()
        try:
            r1, log = self._run(case, device_root, remote_root, prefix,
                                KEEP_ANDROID_ENV='1')
            self.assertEqual(r1.returncode, 0, r1.stdout.decode('utf-8', 'replace'))
            head = len(open(log, encoding='utf-8').read())
            # Simulate a broken cached env (e.g. interpreter removed).
            shutil.rmtree(self._env_dir(remote_root))
            r2, _ = self._run(case, device_root, remote_root, prefix,
                              KEEP_ANDROID_ENV='1')
            self.assertEqual(r2.returncode, 0, r2.stdout.decode('utf-8', 'replace'))
            self.assertTrue(os.path.isdir(self._env_dir(remote_root)))
            log2 = self._log_tail(log, head)
            self.assertIn('tar -xf', log2)
        finally:
            shutil.rmtree(case, ignore_errors=True)

    def test_clean_env_command_removes_device_cache(self):
        case, device_root, remote_root, prefix = self._make_case()
        try:
            env_dir = self._env_dir(remote_root)
            os.makedirs(env_dir)
            with open(os.path.join(env_dir, 'stamp'), 'w') as fh:
                fh.write('stale\n')
            r, log = self._run(case, device_root, remote_root, prefix,
                               '--clean-env')
            self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
            self.assertFalse(os.path.exists(env_dir))
            self.assertIn('rm -rf /data/local/tmp/andbackup-pyenv',
                          self._log_tail(log, 0))
        finally:
            shutil.rmtree(case, ignore_errors=True)

    def test_clean_host_cache_command_removes_cache_dir(self):
        case, device_root, remote_root, prefix = self._make_case()
        try:
            cache = os.path.join(case, 'host-cache')
            os.makedirs(os.path.join(cache, 'andbackup', 'downloads'))
            r, _ = self._run(case, device_root, remote_root, prefix,
                             '--clean-host-cache', LOCALAPPDATA=cache)
            self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
            self.assertFalse(os.path.exists(os.path.join(cache, 'andbackup')))
        finally:
            shutil.rmtree(case, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
