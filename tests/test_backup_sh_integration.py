#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集成测试（离线）：把 backup-android.sh 完整跑一遍。

做法是把 adb 换成“假实现”塞进 PATH 最前面。替身以本机临时目录模拟设备文件系统，
原样执行 find/stat/readlink/cat，因此能检查每个 ADB 直读环节、二进制重定向和校验。
Windows 的 .bat 版本没有 cmd 环境，按约定暂不测试。
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

FAKE_ADB = r"""#!/bin/sh
# 假 adb：用本机临时目录模拟 adb exec-out 的纯字节通道；记录 serial 以区分
# USB 默认/显式 serial 与无线 host:port 两种主控路径。
echo "serial=${ANDROID_SERIAL:-}" >> "$FAKE_ADB_LOG"
echo "adb $*" >> "$FAKE_ADB_LOG"
[ -n "${FAKE_ADB_FAIL:-}" ] && exit 1
case "$1" in
    devices)
        echo "List of devices attached"
        if [ -n "${FAKE_ADB_DEVICES:-}" ]; then
            printf '%b\n' "$FAKE_ADB_DEVICES"
        else
            printf 'FAKE-1 device\n'
        fi
        ;;
    connect)
        echo "connected to $2"
        ;;
    get-state)
        echo device
        ;;
    exec-out)
        # ADB 直读模式：把本机临时目录当作 Android 文件系统，保持 exec-out
        # 的纯字节 stdout 语义。测试的命令仅是 find/stat/readlink/cat。
        shift
        if [ "$1" = sh ] && [ "$2" = -c ]; then
            command="$3"
            wrapped=0
            case "$command" in
                *' 2>/dev/null; __andbackup_rc=$?; printf '*)
                    command="${command%% 2>/dev/null;*}"
                    wrapped=1
                    ;;
            esac
            case "$command" in
                cat\ *)
                    if [ -n "${FAKE_ADB_TRUNCATE:-}" ]; then
                        sh -c "$command" | head -c "$FAKE_ADB_TRUNCATE"
                        [ "$wrapped" -eq 1 ] && printf '\000__ANDBACKUP_RC__0\000'
                        exit 0
                    fi
                    ;;
            esac
            if [ "$wrapped" -eq 1 ]; then
                sh -c "$command"
                rc=$?
                printf '\000__ANDBACKUP_RC__%s\000' "$rc"
                exit 0
            fi
            exec sh -c "$command"
        fi
        echo "fake adb: unsupported exec-out command: $*" >&2
        exit 1
        ;;
    *)
        echo "fake adb: unsupported command: $*" >&2
        exit 1
        ;;
esac
exit 0
"""

def write_executable(path, text):
    with open(path, 'w') as fh:
        fh.write(text)
    os.chmod(path, 0o755)
    return path


class HarnessMixin:
    """搭一个假设备目录 + 假 adb，并给出跑 backup-android.sh 的环境。"""

    def setUp(self):
        self.case = tempfile.mkdtemp(prefix='paxck-sh-')
        self.device = os.path.join(self.case, 'device')      # “手机磁盘”
        self.bin = os.path.join(self.case, 'bin')            # 假工具
        self.log = os.path.join(self.case, 'adb.log')
        os.makedirs(self.device)
        os.makedirs(self.bin)
        write_executable(os.path.join(self.bin, 'adb'), FAKE_ADB)

        # 故意用最拧巴的目录名：中文 + 空格 + 单引号 + 省略号
        self.root_name = "测试 'quoted' dir — v2"
        self.source = os.path.join(self.device, self.root_name)
        T.build_tree(self.device)
        if not os.path.isdir(self.source):
            # build_tree 默认生成 测试.d，这里按需要改名
            os.rename(os.path.join(self.device, '测试.d'), self.source)
        else:
            with open(os.path.join(self.source, 'extra.txt'), 'wb') as fh:
                fh.write(b'extra\n')

        self.out = os.path.join(self.case, 'out.tar.xz')

    def tearDown(self):
        shutil.rmtree(self.case, ignore_errors=True)

    def env(self, **over):
        base = dict(os.environ)
        base['PATH'] = self.bin + os.pathsep + (base.get('PATH') or '/usr/bin:/bin')
        base['FAKE_ADB_LOG'] = self.log
        base.update({
            'SOURCE_DIR': self.source,
            'OUT': self.out,
            'COMPRESS': 'xz',
            # Do not let the repository's live YAML device settings affect
            # the local fake-ADB harness (especially when run from WSL).
            'BACKUP_CONFIG_FILE': '',
        })
        # Keep the transport mode under test independent of the caller's live
        # ADB environment and repository YAML.
        base.pop('ADB_SERIAL', None)
        base.pop('ADB_CONNECT', None)
        base.pop('ANDROID_SERIAL', None)
        base.pop('DEVICE', None)
        base.pop('FAKE_ADB_FAIL', None)
        base.pop('FAKE_ADB_TRUNCATE', None)
        base.pop('FAKE_ADB_DEVICES', None)
        base.pop('SOURCE_MODE', None)
        base.pop('DEVICE_PYTHON', None)
        base.pop('DOWNLOAD_DEVICE_PYTHON', None)
        base.pop('DEVICE_PYTHON_URL', None)
        base.pop('KEEP_ANDROID_ENV', None)
        base.pop('LOG_LEVEL', None)
        base.pop('PROGRESS_INTERVAL', None)
        base.pop('SHOW_RATE', None)
        base.update(over)
        return base

    def run_script(self, **over):
        return subprocess.run([T.BACKUP_SH], env=self.env(**over),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def adb_log_text(self):
        with open(self.log) as fh:
            return fh.read()


@unittest.skipIf(os.name == 'nt', 'Linux shell launcher coverage')
class TestBackupScriptHappyPath(HarnessMixin, unittest.TestCase):
    def test_full_run_produces_verified_archive(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
        self.assertTrue(os.path.isfile(self.out))
        self.assertGreater(os.path.getsize(self.out), 0)

        members = T.list_members(T.read_bytes(self.out))
        expected_root = self.root_name
        self.assertEqual(members[expected_root + '/readme.txt'][2], b'hello\n')
        self.assertEqual(members[expected_root + '/empty.bin'][2], b'')
        self.assertEqual(members[expected_root + '/sub dir/deep.txt'][2],
                         b'deep\n')

    def test_no_code_or_archive_is_staged_on_the_device(self):
        self.assertEqual(self.run_script().returncode, 0)
        log = self.adb_log_text()
        self.assertNotIn('push ', log)
        self.assertNotIn('forward ', log)

    def test_adb_exec_out_is_requested(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.assertIn('exec-out sh -c find', self.adb_log_text())

    def test_nasty_path_survives_two_layers_of_shell(self):
        """回归：目录名含空格和单引号时，ADB shell 参数仍得到正确转义。"""
        with open(os.path.join(self.source, 'space name.txt'), 'wb') as fh:
            fh.write(b'spaced\n')
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
        members = T.list_members(T.read_bytes(self.out))
        self.assertEqual(members[self.root_name + '/space name.txt'][2],
                         b'spaced\n')

    def test_shared_yaml_config_drives_shell_launcher(self):
        config = os.path.join(self.case, 'backup.yaml')
        with open(config, 'w', encoding='utf-8') as fh:
            fh.write('adb: adb\n')
            fh.write('device: ""\n')
            fh.write('source_dir: "' + self.source + '"\n')
            fh.write('out: "' + self.out + '"\n')
            fh.write('compress: gzip\n')
        env = self.env(BACKUP_CONFIG_FILE=config)
        for key in ('SOURCE_DIR', 'OUT', 'COMPRESS'):
            env.pop(key, None)
        result = subprocess.run([T.BACKUP_SH], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        self.assertEqual(T.run_cli(['verify', self.out])[0], 0)

    def test_command_line_config_path_overrides_environment(self):
        config = os.path.join(self.case, 'backup-cli.yaml')
        with open(config, 'w', encoding='utf-8') as fh:
            fh.write('adb: adb\n')
            fh.write('device: ""\n')
            fh.write('source_dir: "' + self.source + '"\n')
            fh.write('out: "' + self.out + '"\n')
            fh.write('compress: gzip\n')
        env = self.env(BACKUP_CONFIG_FILE=os.path.join(self.case, 'missing.yaml'))
        for key in ('SOURCE_DIR', 'OUT', 'COMPRESS'):
            env.pop(key, None)
        result = subprocess.run(
            [T.BACKUP_SH, '--config', config], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        self.assertEqual(T.run_cli(['verify', self.out])[0], 0)

    def test_usb_serial_is_forwarded_without_tcp_connect(self):
        """USB 设备可显式选择 serial，但不能触发无线 adb connect。"""
        serial = 'USB-SERIAL-001'
        result = self.run_script(DEVICE=serial)
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        log = self.adb_log_text()
        self.assertIn('serial=' + serial, log)
        self.assertNotIn('adb connect ', log)

    def test_wireless_tcp_serial_is_connected_and_forwarded(self):
        serial = '192.0.2.1:5555'
        result = self.run_script(DEVICE=serial)
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode('utf-8', 'replace'))
        log = self.adb_log_text()
        self.assertIn('adb connect ' + serial, log)
        self.assertIn('serial=' + serial, log)

    def test_checksums_match_source_bytes(self):
        self.assertEqual(self.run_script().returncode, 0)
        members = T.list_members(T.read_bytes(self.out))
        for name, (_t, _l, payload, ph) in members.items():
            if payload is None:
                continue
            self.assertEqual(ph[T.PAX_KEY], T.sha256_of(payload), name)


@unittest.skipIf(os.name == 'nt', 'Linux shell launcher coverage')
class TestCompressorSelection(HarnessMixin, unittest.TestCase):
    def _roundtrip(self, compress, suffix):
        r = self.run_script(COMPRESS=compress, OUT=os.path.join(
            self.case, 'out.' + suffix))
        self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
        target = os.path.join(self.case, 'out.' + suffix)
        self.assertTrue(os.path.isfile(target))
        return T.list_members(T.read_bytes(target))

    def test_xz(self):
        m = self._roundtrip('xz', 'tar.xz')
        self.assertEqual(m[self.root_name + '/readme.txt'][2], b'hello\n')

    def test_gzip(self):
        m = self._roundtrip('gzip', 'tar.gz')
        self.assertEqual(m[self.root_name + '/readme.txt'][2], b'hello\n')

    def test_none(self):
        m = self._roundtrip('none', 'tar')
        self.assertEqual(m[self.root_name + '/readme.txt'][2], b'hello\n')

    def test_default_output_name_follows_compressor(self):
        r = self.run_script(COMPRESS='gzip', OUT='')
        self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
        # OUT 为空串时脚本应当退回 backup.tar.gz（落在脚本被运行的 cwd）
        name = os.path.join(os.getcwd(), 'backup.tar.gz')
        try:
            self.assertTrue(os.path.isfile(name))
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def test_unknown_compressor_is_rejected(self):
        r = self.run_script(COMPRESS='lzma99')
        self.assertEqual(r.returncode, 1)
        self.assertIn('未知压缩类型', r.stdout.decode('utf-8', 'replace'))


@unittest.skipIf(os.name == 'nt', 'Linux shell launcher coverage')
class TestBackupScriptAndroidData(HarnessMixin, unittest.TestCase):
    """Android/data 走 ADB shell，不存在 Termux/SSH 分支。"""

    def setUp(self):
        super().setUp()
        restricted = os.path.join(
            self.device, 'Android', 'data', 'com.example.backup', self.root_name)
        os.makedirs(os.path.dirname(restricted), exist_ok=True)
        os.rename(self.source, restricted)
        self.source = restricted

    def test_streams_android_data_without_push_or_forward(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout.decode('utf-8', 'replace'))
        members = T.list_members(T.read_bytes(self.out))
        self.assertEqual(members[self.root_name + '/readme.txt'][2], b'hello\n')
        log = self.adb_log_text()
        self.assertIn('exec-out sh -c find', log)
        self.assertNotIn('push ', log)
        self.assertNotIn('forward ', log)


@unittest.skipIf(os.name == 'nt', 'Linux shell launcher coverage')
class TestBackupScriptFailurePaths(HarnessMixin, unittest.TestCase):
    def test_missing_source_directory_fails_loudly(self):
        r = self.run_script(SOURCE_DIR=os.path.join(self.device, 'no-such'))
        self.assertEqual(r.returncode, 1)
        text = r.stdout.decode('utf-8', 'replace')
        self.assertIn('传输失败', text)

    def test_adb_unavailable_fails_loudly(self):
        r = self.run_script(FAKE_ADB_FAIL='1')
        self.assertEqual(r.returncode, 1)
        text = r.stdout.decode('utf-8', 'replace')
        self.assertIn('adb 不可用', text)

    def test_truncated_adb_read_fails_loudly(self):
        size_hint = 120
        r = self.run_script(FAKE_ADB_TRUNCATE=str(size_hint))
        self.assertEqual(r.returncode, 1)
        text = r.stdout.decode('utf-8', 'replace')
        self.assertIn('传输失败', text)
        self.assertFalse(os.path.exists(self.out), '失败传输不能替换最终归档')
        leftovers = [name for name in os.listdir(self.case)
                     if name.startswith(os.path.basename(self.out) + '.partial.')]
        self.assertEqual(leftovers, [], '失败传输应清理主机端 partial 文件')

    def test_file_instead_of_source_directory_fails_loudly(self):
        """
        ADB 数据源适配器必须先验证源路径是目录；不能把普通文件误当作备份根目录。
        """
        solo = os.path.join(self.case, 'plain-file')
        with open(solo, 'wb') as fh:
            fh.write(b'not a directory')
        r = self.run_script(SOURCE_DIR=solo)
        self.assertEqual(r.returncode, 1)
        self.assertIn('传输失败', r.stdout.decode('utf-8', 'replace'))

    def test_missing_local_python_is_reported(self):
        empty_bin = os.path.join(self.case, 'empty-bin')
        os.makedirs(empty_bin, exist_ok=True)
        env = self.env(PATH=empty_bin)
        env['PATH'] = empty_bin
        r = subprocess.run([T.BACKUP_SH], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 1)
        self.assertIn('python3', r.stdout.decode('utf-8', 'replace'))

    def test_archive_is_binary_safe(self):
        """回归点：Windows 文本模式会把 0x0A 变成 0x0D 0x0A。
        Linux 侧用 CRLF 陷阱文件提前兜住这类翻译。"""
        trap = os.path.join(self.source, 'crlf-trap.bin')
        with open(trap, 'wb') as fh:
            fh.write(bytes(range(256)) * 8)
        self.assertEqual(self.run_script().returncode, 0)
        members = T.list_members(T.read_bytes(self.out))
        self.assertEqual(members[self.root_name + '/crlf-trap.bin'][2],
                         bytes(range(256)) * 8)


if __name__ == '__main__':
    unittest.main()
