#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真机集成：只需要一台已授权 USB 调试的 Android 设备，不依赖 Termux/SSH。"""
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

    def _run(self, argv, **kw):
        return subprocess.run(argv, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, **kw)

    def adb_run(self, args, **kw):
        return self._run([self.adb] + args, **kw)

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
        if not cls.dev.serial():
            raise unittest.SkipTest('没有已授权的 USB 调试设备（在手机上允许调试）')


class TestDevicePreflight(DeviceCaseMixin, unittest.TestCase):
    def test_device_is_authorized(self):
        self.assertIsNotNone(self.dev.serial())

    def test_adb_shell_can_read_target_directory(self):
        text = self.dev.adb_out(['shell', 'ls', '-la', DEVICE_DIR])
        self.assertNotIn('No such file', text)
        self.assertNotIn('Permission denied', text)


class TestSourcePathOnDevice(DeviceCaseMixin, unittest.TestCase):
    """用户给定目录的通道与完整备份回归。"""

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

                pulled = self.dev.adb_run(['pull', path, work], timeout=120)
                self.assertEqual(pulled.returncode, 0,
                                 pulled.stderr.decode('utf-8', 'replace'))
                with open(os.path.join(work, os.path.basename(name)), 'rb') as fh:
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
            result = subprocess.run(
                [T.BACKUP_SH], env=env, stdout=subprocess.PIPE,
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
