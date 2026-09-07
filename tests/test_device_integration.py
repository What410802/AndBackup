#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真机集成：需要一台已授权的 USB 或无线调试 Android 设备。"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

DEVICE_DIR = os.environ.get(
    'ANDROBACKUP_DEVICE_DIR',
    '/storage/emulated/0/Android/data/com.example.backup/测试.d')
SPACE_SLACK_BYTES = 32 * 1024 * 1024


class Device:
    """仅封装 adb 的二进制与文本通道。"""

    def __init__(self):
        self.adb = T.find_adb()
        self.target_serial = os.environ.get('ANDROBACKUP_ADB_SERIAL')

    def _run(self, argv, **kw):
        return subprocess.run(argv, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, **kw)

    def adb_run(self, args, **kw):
        command = [self.adb]
        if getattr(self, 'target_serial', None):
            command.extend(['-s', self.target_serial])
        return self._run(command + args, **kw)

    def connect(self):
        """按显式请求连接无线设备；默认不改变 ADB 连接状态。"""
        return self._run([self.adb, 'connect', self.target_serial])

    def adb_out(self, args, timeout=30):
        result = self.adb_run(args, timeout=timeout)
        return (result.stdout + result.stderr).decode('utf-8', 'replace')

    def adb_bytes(self, args, timeout=120):
        result = self.adb_run(args, timeout=timeout)
        if result.returncode:
            raise AssertionError(
                f'adb {args} 失败: {result.stderr.decode("utf-8", "replace")}')
        return result.stdout

    def serial(self):
        if getattr(self, 'target_serial', None):
            result = self._run(
                [self.adb, '-s', self.target_serial, 'get-state'])
            return (self.target_serial if result.returncode == 0 and
                    result.stdout.strip() == b'device' else None)
        for line in self.adb_out(['devices']).splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 2 and fields[1] == 'device':
                return fields[0]
        return None

    def free_bytes(self):
        """解析 toybox df -k 的 Available 列；取不到时由用例跳过空间断言。"""
        text = self.adb_out(['shell', 'df', '-k', '/data'])
        for line in reversed(text.splitlines()):
            fields = line.split()
            if len(fields) >= 4 and fields[3].isdigit():
                return int(fields[3]) * 1024
        return None


_DEVICE = None


def device():
    global _DEVICE
    if _DEVICE is None:
        _DEVICE = Device()
    return _DEVICE


class DeviceCaseMixin:
    @classmethod
    def setUpClass(cls):
        cls.dev = device()
        if not cls.dev.adb:
            raise unittest.SkipTest(
                '找不到 adb：设置 ANDROBACKUP_ADB 或把 platform-tools 加进 PATH')
        if (cls.dev.target_serial and
                os.environ.get('ANDROBACKUP_ADB_CONNECT') == '1'):
            result = cls.dev.connect()
            if result.returncode:
                detail = result.stderr.decode('utf-8', 'replace').strip()
                raise unittest.SkipTest('无法连接无线 ADB：' + detail)
        if not cls.dev.serial():
            raise unittest.SkipTest('没有已授权的 USB/无线调试设备（在手机上允许调试）')


class TestDevicePreflight(DeviceCaseMixin, unittest.TestCase):
    def test_device_is_authorized(self):
        self.assertIsNotNone(self.dev.serial())

    def test_adb_shell_can_read_target_directory(self):
        text = self.dev.adb_out(['shell', 'ls', '-la', DEVICE_DIR])
        self.assertNotIn('No such file', text)
        self.assertNotIn('Permission denied', text)


class TestSourcePathOnDevice(DeviceCaseMixin, unittest.TestCase):
    """用户给定目录的通道与完整备份回归。"""

    def test_exec_out_preserves_lf_bytes(self):
        """Windows adb.exe 的 exec-out 不能把 LF 转换为 CRLF。"""
        path = DEVICE_DIR + '/测试.txt'
        try:
            direct = self.dev.adb_bytes(['exec-out', 'cat', path])
        except AssertionError as exc:
            self.skipTest('测试文件不可读取：' + str(exc))
        if b'\n' not in direct:
            self.skipTest('测试文件不含 LF，无法验证换行字节保真')

        work = tempfile.mkdtemp(prefix='paxck-device-lf-')
        try:
            local = os.path.join(work, '测试.txt')
            pulled = self.dev.adb_run(['pull', path, local], timeout=120)
            self.assertEqual(pulled.returncode, 0,
                             pulled.stderr.decode('utf-8', 'replace'))
            with open(local, 'rb') as fh:
                self.assertEqual(direct, fh.read())
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_source_contents_stream_intact_over_adb(self):
        """同一文件连读两次，且与 adb pull 的结果逐字节相同。"""
        names = [name.strip() for name in
                 self.dev.adb_out(['shell', 'ls', '-1', DEVICE_DIR]).splitlines()
                 if name.strip()]
        self.assertTrue(names, '源目录是空的，无法验证传输')
        work = tempfile.mkdtemp(prefix='paxck-device-src-')
        try:
            for name in names:
                path = f'{DEVICE_DIR}/{name}'
                first = self.dev.adb_bytes(['exec-out', 'cat', path])
                second = self.dev.adb_bytes(['exec-out', 'cat', path])
                self.assertEqual(first, second, f'{name} 两次读取不一致')

                local = os.path.join(work, os.path.basename(name))
                pulled = self.dev.adb_run(['pull', path, local], timeout=120)
                self.assertEqual(pulled.returncode, 0,
                                 pulled.stderr.decode('utf-8', 'replace'))
                with open(local, 'rb') as fh:
                    self.assertEqual(fh.read(), first, f'{name} pull 与 cat 不一致')
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_backup_script_streams_android_data_without_device_staging(self):
        """真实脚本必须在给定 Android/data 上完成备份、验证且不消耗手机空间。"""
        free_before = self.dev.free_bytes()
        work = tempfile.mkdtemp(prefix='paxck-device-adb-out-')
        out = os.path.join(work, 'android-backup.tar.xz')
        try:
            env = dict(os.environ)
            env.update({'ADB': self.dev.adb, 'SOURCE_DIR': DEVICE_DIR, 'OUT': out})
            if os.name == 'nt':
                env.update({'PYTHON': T.py(), 'ADB_SERIAL': self.dev.serial()})
                command = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c',
                           T.BACKUP_BAT]
            else:
                command = [T.BACKUP_SH]
            result = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=300)
            self.assertEqual(result.returncode, 0,
                             result.stdout.decode('utf-8', 'replace'))
            self.assertGreater(os.path.getsize(out), 0)
            rc, _stdout, err = T.run_cli(['verify', out])
            self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))

            members = T.list_members(T.read_bytes(out))
            source = self.dev.adb_bytes(['exec-out', 'cat', DEVICE_DIR + '/测试.txt'])
            self.assertEqual(members['测试.d/测试.txt'][2], source)

            if free_before is not None:
                free_after = self.dev.free_bytes()
                self.assertIsNotNone(free_after)
                self.assertLess(
                    free_before - free_after, SPACE_SLACK_BYTES,
                    '设备可用空间异常减少，疑似在设备上落了中间文件')
        finally:
            shutil.rmtree(work, ignore_errors=True)


class TestDeviceParsingWithoutHardware(unittest.TestCase):
    def _device(self, output=''):
        result = Device.__new__(Device)
        result.adb = 'fake-adb'
        result.adb_out = lambda *args, **kwargs: output
        return result

    def test_serial_from_authorized_device(self):
        dev = self._device('List of devices attached\nUSB-TEST-SERIAL\tdevice\n\n')
        self.assertEqual(dev.serial(), 'USB-TEST-SERIAL')

    def test_serial_ignores_unauthorized_and_offline(self):
        dev = self._device('List of devices attached\nABC\tunauthorized\nXYZ\toffline\n')
        self.assertIsNone(dev.serial())

    def test_free_bytes_uses_available_kib_column(self):
        dev = self._device('Filesystem 1K-blocks Used Available Use% Mounted on\n'
                           '/dev/block/dm-1 1000 200 800 20% /data\n')
        self.assertEqual(dev.free_bytes(), 800 * 1024)

    def test_free_bytes_returns_none_for_unparseable_output(self):
        self.assertIsNone(self._device('df: permission denied\n').free_bytes())


if __name__ == '__main__':
    unittest.main()
