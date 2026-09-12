#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单元测试：直接调用 paxck.py 的内部函数，不需要 adb、不需要手机。

分层：
  TestSniff*            —— 魔术字节识别与流式读取器（纯函数，最快）
  TestCreate*           —— cmd_create 的条目类型、降级与容错
  TestVerify*           —— cmd_verify 的退出码矩阵
  TestCompressCommand   —— 新增的 compress 子命令（纯 Python 压缩）
"""
import builtins
import gzip
import io
import lzma
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

paxck = T.load_paxck()
adb_source = T.load_adb_source()

sys.path.insert(0, T.SRC_DIR)

import prune  # noqa: E402
backup = T.load_backup()


# --------------------------------------------------------------------------
# 纯函数层
# --------------------------------------------------------------------------
class TestSniff(unittest.TestCase):
    def test_recognises_known_magic(self):
        self.assertEqual(paxck.sniff(b'\xfd7zXZ\x00rest'), 'xz')
        self.assertEqual(paxck.sniff(b'\x1f\x8b\x08abc'), 'gzip')
        self.assertEqual(paxck.sniff(b'\x28\xb5\x2f\xfdxx'), 'zstd')

    def test_unknown_is_none(self):
        for head in (b'', b'\x00' * 8, b'\x75\x73\x74\x61\x72', b'plain.txt'):
            self.assertIsNone(paxck.sniff(head))


class TestConfig(unittest.TestCase):
    def test_log_level_and_progress_settings(self):
        path = os.path.join(tempfile.mkdtemp(prefix='paxck-config-'), 'backup.yaml')
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('log_level: debug\nprogress_interval: 1.5\n')
            values = backup.read_config(path)
            self.assertEqual(values['LOG_LEVEL'], 'debug')
            self.assertEqual(values['PROGRESS_INTERVAL'], '1.5')
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_yaml_subset_and_boolean_values(self):
        path = os.path.join(tempfile.mkdtemp(prefix='paxck-config-'), 'backup.yaml')
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('# comment\n')
                fh.write('host: 192.0.2.1:5555\n')
                fh.write('serial: AERF6R4517018096\n')
                fh.write('source_dir: "/storage/emulated/0/测试.d"\n')
                fh.write('compress: gzip # inline comment\n')
            values = backup.read_config(path)
            self.assertEqual(values['HOST'], '192.0.2.1:5555')
            self.assertEqual(values['SERIAL'], 'AERF6R4517018096')
            self.assertEqual(values['SOURCE_DIR'], '/storage/emulated/0/测试.d')
            self.assertEqual(values['COMPRESS'], 'gzip')
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_malformed_yaml_is_rejected(self):
        path = os.path.join(tempfile.mkdtemp(prefix='paxck-config-'), 'bad.yaml')
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('- nested\n')
            with self.assertRaises(ValueError):
                backup.read_config(path)
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_list_value_is_rejected(self):
        path = os.path.join(tempfile.mkdtemp(prefix='paxck-config-'), 'bad.yaml')
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('compress: [xz, gzip]\n')
            with self.assertRaises(ValueError):
                backup.read_config(path)
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_explicit_missing_config_path_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='paxck-missing-config-') as directory:
            missing = os.path.join(directory, 'not-here.yaml')
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, '指定的配置文件不存在'):
                    backup._settings(missing)

    def test_head_shorter_than_magic(self):
        # 输入可能被截断到只剩两三个字节，这里不能抛异常
        self.assertIsNone(paxck.sniff(b'\xfd'))
        self.assertIsNone(paxck.sniff(b'\x1f'))


class TestAdbSourceMetadata(unittest.TestCase):
    def test_progress_reporter_can_show_rate(self):
        with mock.patch('sys.stderr', new_callable=io.StringIO) as err, \
             mock.patch.object(adb_source.time, 'monotonic', side_effect=(1.0, 2.0)):
            reporter = adb_source.ProgressReporter('info', 0.1, show_rate=True)
            reporter.on_file_bytes(2048)
        self.assertIn('速率', err.getvalue())
        self.assertIn('2.0 KiB/s', err.getvalue())

    def test_adb_status_protocol_separates_payload_from_remote_status(self):
        payload, rc = adb_source._split_status(
            b'path\0\0__ANDBACKUP_RC__1\0', 'find /root -print0')
        self.assertEqual(payload, b'path\0')
        self.assertEqual(rc, 1)
        self.assertIn('2>/dev/null', adb_source._protocol_command('find /root -print0'))

    def test_adb_payload_reader_hides_status_marker(self):
        stream = io.BytesIO(b'abc\0__ANDBACKUP_RC__0\0')
        reader = adb_source._AdbPayloadReader(stream, 'cat -- /file')
        self.assertEqual(reader.read(2), b'ab')
        self.assertEqual(reader.read(10), b'c')
        self.assertEqual(reader.read(1), b'')

    def test_list_paths_reports_streaming_discovery(self):
        root = '/storage/emulated/0/tree'
        raw = (root.encode() + b'\0' + (root + '/file').encode() +
               b'\0\0__ANDBACKUP_RC__0\0')
        reporter = adb_source.ProgressReporter('quiet', 1)
        proc = mock.Mock(stdout=io.BytesIO(raw), stderr=io.BytesIO(), poll=mock.Mock())
        proc.wait.return_value = 0
        with mock.patch.object(adb_source, '_adb_open_command', return_value=(
                proc, adb_source._AdbPayloadReader(proc.stdout, 'find',
                                                   reporter.on_listing_bytes,
                                                   allow_remote_failure=True))):
            paths, rc = adb_source._list_paths('adb', root, True, reporter)
        self.assertEqual(paths, [root, root + '/file'])
        self.assertEqual(rc, 0)
        self.assertEqual(reporter.total, 2)
        self.assertGreater(reporter.list_bytes, 0)

    def test_lstat_preserves_fractional_mtime_from_toybox(self):
        raw = (b'81b0|53|1788676937|2026-09-06 14:42:17.181008465 +0800|644\n')
        with mock.patch.object(adb_source, '_adb_exec', return_value=raw) as adb_exec:
            result = adb_source._lstat('adb', '/storage/emulated/0/file')
        self.assertAlmostEqual(result['mtime'], 1788676937.181008465,
                               places=6)
        self.assertEqual(result['size'], 53)
        self.assertIn("%Y|%y|", adb_exec.call_args.args[1])

    def test_source_adapter_writes_entries_with_generic_pax_writer(self):
        root = '/storage/emulated/0/tree'
        paths = [root, root + '/file.txt', root + '/link']
        metadata = {
            root: {'mode': stat.S_IFDIR | 0o755, 'size': 0,
                   'mtime': 1788676937.25, 'perm': 0o755},
            root + '/file.txt': {'mode': stat.S_IFREG | 0o640, 'size': 3,
                                 'mtime': 1788676937.5, 'perm': 0o640},
            root + '/link': {'mode': stat.S_IFLNK | 0o777, 'size': 8,
                             'mtime': 1788676937.75, 'perm': 0o777},
        }
        out = io.BytesIO()
        with mock.patch.object(adb_source, '_lstat',
                               side_effect=lambda _adb, path: metadata[path]), \
             mock.patch.object(adb_source, '_list_paths',
                               return_value=(paths, 0)), \
             mock.patch.object(adb_source, '_hash_file',
                               return_value=(T.sha256_of(b'abc'), 3)), \
             mock.patch.object(adb_source, '_open_stream',
                               return_value=(io.BytesIO(b'abc'), lambda: 0)), \
             mock.patch.object(adb_source, '_readlink', return_value='file.txt'):
            self.assertEqual(adb_source.write_tar(root, 'fake-adb', out), 0)

        members = T.list_members(out.getvalue())
        self.assertEqual(members['tree/file.txt'][2], b'abc')
        self.assertEqual(members['tree/file.txt'][3][T.PAX_KEY], T.sha256_of(b'abc'))
        self.assertEqual(members['tree/link'][0], tarfile.SYMTYPE)
        self.assertEqual(members['tree/link'][1], 'file.txt')

    def test_source_adapter_skips_unreadable_entry_and_still_succeeds(self):
        root = '/storage/emulated/0/tree'
        good = root + '/ok.txt'
        denied = root + '/denied.txt'
        metadata = {
            root: {'mode': stat.S_IFDIR | 0o755, 'size': 0, 'mtime': 1, 'perm': 0o755},
            good: {'mode': stat.S_IFREG | 0o644, 'size': 2, 'mtime': 1, 'perm': 0o644},
        }
        out = io.BytesIO()
        def lstat(_adb, path):
            if path == denied:
                raise OSError('Permission denied')
            return metadata[path]
        with mock.patch.object(adb_source, '_lstat', side_effect=lstat), \
             mock.patch.object(adb_source, '_list_paths', return_value=([root, good, denied], 0)), \
             mock.patch.object(adb_source, '_hash_file', return_value=(T.sha256_of(b'ok'), 2)), \
             mock.patch.object(adb_source, '_open_stream', return_value=(io.BytesIO(b'ok'), lambda: 0)):
            with mock.patch('sys.stderr', new_callable=io.StringIO) as err:
                # Unreadable entries are skipped with a warning; the archive is
                # still produced (only "nothing archivable" is a failure).
                self.assertEqual(adb_source.write_tar(root, 'fake-adb', out), 0)
        self.assertIn('跳过', err.getvalue())
        self.assertIn('tree/ok.txt', T.list_members(out.getvalue()))


class TestPrependReader(unittest.TestCase):
    """sniff 读掉的头部要“塞回”流里，读_semantics 必须和文件对象一致。"""

    def _make(self, data=b'0123456789ABCDEF', n=6):
        return paxck._PrependReader(data[:n], io.BytesIO(data[n:])), data

    def test_read_all(self):
        r, data = self._make()
        self.assertEqual(r.read(), data)
        self.assertEqual(r.read(), b'')

    def test_read_zero_returns_empty(self):
        r, data = self._make()
        self.assertEqual(r.read(0), b'')
        self.assertEqual(r.read(), data)

    def test_read_negative(self):
        r, data = self._make()
        self.assertEqual(r.read(-1), data)

    def test_repeated_partial_reads(self):
        r, data = self._make()
        got = b''
        while True:
            chunk = r.read(3)
            if not chunk:
                break
            got += chunk
        self.assertEqual(got, data)

    def test_partial_then_read_all(self):
        r, data = self._make()
        self.assertEqual(r.read(2), data[:2])
        self.assertEqual(r.read(), data[2:])

    def test_read_spanning_head_and_tail(self):
        r, data = self._make(n=2)
        self.assertEqual(r.read(7), data[:7])

    def test_peek_does_not_consume(self):
        r, data = self._make()
        self.assertEqual(r.peek(4), data[:4])
        self.assertEqual(r.read(4), data[:4])


class TestExactReader(unittest.TestCase):
    def test_pads_short_source(self):
        r = paxck._ExactReader(io.BytesIO(b'abc'), 6)
        self.assertEqual(r.read(), b'abc\x00\x00\x00')
        self.assertEqual(r.padded, 3)

    def test_truncates_long_source(self):
        r = paxck._ExactReader(io.BytesIO(b'abcdef'), 3)
        self.assertEqual(r.read(), b'abc')
        self.assertEqual(r.read(), b'')
        self.assertEqual(r.padded, 0)

    def test_read_zero_is_empty(self):
        r = paxck._ExactReader(io.BytesIO(b'abc'), 3)
        self.assertEqual(r.read(0), b'')
        self.assertEqual(r.read(), b'abc')

    def test_exact_size_output(self):
        r = paxck._ExactReader(io.BytesIO(b'x' * 10), 10)
        self.assertEqual(len(r.read()), 10)

    def test_short_reads_are_not_mistaken_for_eof(self):
        """管道可以合法地短读；只有真正 EOF 后才允许补零。"""
        class ShortReader:
            def __init__(self):
                self.data = io.BytesIO(b'abcdef')

            def read(self, n=-1):
                return self.data.read(min(n, 2))

        r = paxck._ExactReader(ShortReader(), 6)
        self.assertEqual(r.read(), b'abcdef')
        self.assertEqual(r.padded, 0)


class TestSha256Stream(unittest.TestCase):
    def test_digest_and_size(self):
        blob = T.deterministic_bytes(5000)
        d, size = paxck.sha256_stream(io.BytesIO(blob))
        self.assertEqual(d, T.sha256_of(blob))
        self.assertEqual(size, len(blob))

    def test_empty_stream(self):
        d, size = paxck.sha256_stream(io.BytesIO(b''))
        self.assertEqual(d, T.sha256_of(b''))
        self.assertEqual(size, 0)

    def test_across_chunk_boundary(self):
        blob = T.deterministic_bytes(paxck.CHUNK + 17)
        d, size = paxck.sha256_stream(io.BytesIO(blob))
        self.assertEqual(d, T.sha256_of(blob))
        self.assertEqual(size, len(blob))


class TestOpenArchiveStream(unittest.TestCase):
    def test_xz_via_peek(self):
        blob = lzma.compress(b'payload-xz')
        self.assertEqual(paxck.open_archive_stream(io.BytesIO(blob)).read(),
                         b'payload-xz')

    def test_gzip_via_peek(self):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb') as z:
            z.write(b'payload-gz')
        self.assertEqual(
            paxck.open_archive_stream(io.BytesIO(buf.getvalue())).read(),
            b'payload-gz')

    def test_plain_stream_passthrough(self):
        self.assertEqual(paxck.open_archive_stream(io.BytesIO(b'plain')).read(),
                         b'plain')

    def test_source_without_peek(self):
        """没有 peek() 的裸流要走 _PrependReader 分支，不能只返回头部。"""

        class NoPeek(io.RawIOBase):
            def __init__(self, data):
                self._buf = io.BytesIO(data)

            def readable(self):
                return True

            def read(self, n=-1):
                return self._buf.read(n)

        blob = lzma.compress(b'payload-no-peek')
        self.assertEqual(paxck.open_archive_stream(NoPeek(blob)).read(),
                         b'payload-no-peek')


# --------------------------------------------------------------------------
# create 层
# --------------------------------------------------------------------------
class TestCreateMembers(T.BaseCase):
    def members(self):
        return T.list_members(T.make_tar(self.root))

    def require_link(self, name):
        if not os.path.lexists(os.path.join(self.root, name)):
            self.skipTest('当前 Windows 权限不允许创建符号链接')

    def test_root_and_subdir_entries(self):
        m = self.members()
        self.assertEqual(m['测试.d'][0], tarfile.DIRTYPE)
        self.assertEqual(m['测试.d/sub dir'][0], tarfile.DIRTYPE)

    def test_file_contents(self):
        m = self.members()
        self.assertEqual(m['测试.d/readme.txt'][2], b'hello\n')
        self.assertEqual(m['测试.d/empty.bin'][2], b'')
        self.assertEqual(m['测试.d/binary.bin'][2], T.deterministic_bytes(4096))

    def test_big_file_crossing_chunk_boundary(self):
        m = self.members()
        self.assertEqual(m['测试.d/big.bin'][2],
                         T.deterministic_bytes((1 << 20) + 1234, seed=7))

    def test_symlink_to_file(self):
        self.require_link('link-to-file')
        typ, link, payload, _ = self.members()['测试.d/link-to-file']
        self.assertEqual(typ, tarfile.SYMTYPE)
        self.assertEqual(link, 'readme.txt')
        self.assertIsNone(payload)

    def test_symlink_to_directory_is_symlink_not_directory(self):
        """
        回归：os.walk 把“指向目录的符号链接”放进 dirnames，
        旧实现在那里一律写 DIRTYPE，导致还原后变成一个空目录、链接语义丢失。
        """
        self.require_link('link-to-dir')
        typ, link, payload, _ = self.members()['测试.d/link-to-dir']
        self.assertEqual(typ, tarfile.SYMTYPE)
        self.assertEqual(link, 'sub dir')
        self.assertIsNone(payload)

    def test_broken_symlink_is_preserved(self):
        self.require_link('link-broken')
        typ, link, _, _ = self.members()['测试.d/link-broken']
        self.assertEqual(typ, tarfile.SYMTYPE)
        self.assertEqual(link, '/nowhere/does/not/exist')

    def test_unicode_and_space_names(self):
        self.assertEqual(self.members()['测试.d/sub dir/笔记 — v2.md'][2],
                         b'note\n')

    def test_every_file_carries_correct_sha256(self):
        for name, (_t, _l, payload, ph) in self.members().items():
            if payload is None:
                continue
            self.assertIn(T.PAX_KEY, ph, name)
            self.assertEqual(ph[T.PAX_KEY], T.sha256_of(payload), name)

    def test_fifo_does_not_corrupt_following_entries(self):
        self.assertEqual(self.members()['测试.d/readme.txt'][2], b'hello\n')


class TestCreateInventory(T.BaseCase):
    """The trailing PAXCK.manifest member the verifier compares against."""

    def test_the_archive_ends_with_a_parsable_inventory(self):
        paxck = T.load_paxck()
        blob = T.make_tar(self.root)
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:') as tf:
            names = [m.name for m in tf]
        self.assertEqual(names[-1], paxck.INVENTORY_NAME)
        members = T.list_members(blob)
        payload = members[paxck.INVENTORY_NAME][2]
        # 清单自身的记录也要对得上（它是普通成员，和其它文件一样受保护）
        self.assertEqual(members[paxck.INVENTORY_NAME][3][T.PAX_KEY],
                         T.sha256_of(payload))
        listed = paxck.parse_inventory(payload)
        # 清单列出除自己以外的每个成员，且元信息与归档一致
        self.assertEqual(sorted(listed), sorted(n for n in names
                                                if n != paxck.INVENTORY_NAME))
        for name in listed:
            identity = paxck.member_identity(
                _member_of(blob, name, paxck.INVENTORY_NAME))
            self.assertEqual(identity, listed[name], name)

    def test_the_inventory_name_and_duplicates_are_refused(self):
        """归档根下不会出现同名文件，但写入器仍然要把这两个不变量守住。"""
        paxck = T.load_paxck()
        inventory = paxck.ArchiveInventory()
        with self.assertRaises(paxck.InventoryError):
            inventory.add(tarfile.TarInfo(paxck.INVENTORY_NAME))
        inventory.add(tarfile.TarInfo('a'))
        with self.assertRaises(paxck.InventoryError):
            inventory.add(tarfile.TarInfo('a'))


def _member_of(blob, name, inventory_name):
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:') as tf:
        for member in tf:
            if member.name == name:
                return member
    raise AssertionError('member not found: ' + name)


class TestCreateHardLinks(T.BaseCase):
    def link(self, src, dst):
        try:
            os.link(src, dst)
        except (OSError, AttributeError):
            self.skipTest('当前文件系统不支持 os.link')

    def test_second_occurrence_is_lnktype(self):
        base = os.path.join(self.root, 'aaa-hard.bin')
        dup = os.path.join(self.root, 'zzz-hard.bin')
        self.link(base, dup)
        if os.lstat(base).st_nlink < 2:
            self.skipTest('当前文件系统不维护 nlink')

        m = T.list_members(T.make_tar(self.root))
        self.assertEqual(m['测试.d/aaa-hard.bin'][0], tarfile.REGTYPE)
        self.assertEqual(m['测试.d/zzz-hard.bin'][0], tarfile.LNKTYPE)
        self.assertEqual(m['测试.d/zzz-hard.bin'][1], '测试.d/aaa-hard.bin')

    def test_link_target_never_points_at_a_skipped_entry(self):
        """
        回归：旧实现在“尝试读取之前”就把 inode 登记成链接目标，
        一旦首个文件读失败被跳过，后面的硬链接会指向一个不存在的条目。
        """
        first = os.path.join(self.root, 'aaa-locked.bin')
        second = os.path.join(self.root, 'zzz-locked.bin')
        with open(first, 'wb') as fh:
            fh.write(b'payload')
        self.link(first, second)

        real_open = builtins.open

        def open_first_fails(path, mode='r', *a, **kw):
            if path == first and 'r' in mode:
                raise PermissionError(13, 'stubbed permission denied')
            return real_open(path, mode, *a, **kw)

        builtins.open = open_first_fails
        try:
            rc, blob, err = T.run_in_process(paxck, paxck.cmd_create, self.root)
        finally:
            builtins.open = real_open

        self.assertEqual(rc, 0)
        self.assertIn('[WARN]', err)
        m = T.list_members(blob)
        self.assertNotIn('测试.d/aaa-locked.bin', m)
        # 副本必须带真实数据，而不是退化成指向已消失条目的 LNKTYPE
        self.assertEqual(m['测试.d/zzz-locked.bin'][0], tarfile.REGTYPE)
        self.assertEqual(m['测试.d/zzz-locked.bin'][2], b'payload')


class TestCreateFaultTolerance(T.BaseCase):
    def test_unreadable_file_skipped_with_warning(self):
        if os.name == 'nt' or getattr(os, 'geteuid', lambda: -1)() == 0:
            self.skipTest('root 无视文件权限位')
        locked = os.path.join(self.root, 'locked.txt')
        with open(locked, 'wb') as fh:
            fh.write(b'nope')
        os.chmod(locked, 0)
        try:
            rc, out, err = T.run_cli(['create', self.root])
        finally:
            os.chmod(locked, 0o644)

        self.assertEqual(rc, 0)
        self.assertIn(b'[WARN]', err)
        names = set(T.list_members(out))
        self.assertNotIn('测试.d/locked.txt', names)
        self.assertIn('测试.d/readme.txt', names)

    def test_file_deleted_between_passes_is_skipped(self):
        """第二遍 open 失败（文件刚被删）：头还没写出去，跳过即可，整流保持有效。"""
        victim = os.path.join(self.root, 'victim.bin')
        with open(victim, 'wb') as fh:
            fh.write(b'v' * 900)

        real_open = builtins.open
        seen = {'n': 0}

        def open_then_vanish(path, mode='r', *a, **kw):
            if path == victim and 'r' in mode:
                seen['n'] += 1
                if seen['n'] >= 2:
                    raise FileNotFoundError(2, 'stubbed vanished')
            return real_open(path, mode, *a, **kw)

        builtins.open = open_then_vanish
        try:
            rc, blob, err = T.run_in_process(paxck, paxck.cmd_create, self.root)
        finally:
            builtins.open = real_open

        self.assertEqual(rc, 0)
        self.assertIn('victim.bin', err)
        members = T.list_members(blob)
        self.assertNotIn('测试.d/victim.bin', members)
        self.assertEqual(members['测试.d/readme.txt'][2], b'hello\n')

    def test_file_shrinking_between_passes_is_padded(self):
        target = os.path.join(self.root, 'shrink.bin')
        with open(target, 'wb') as fh:
            fh.write(b'A' * 1000)

        real_open = builtins.open
        seen = {'n': 0}

        def open_shrunk(path, mode='r', *a, **kw):
            if path == target and 'r' in mode:
                seen['n'] += 1
                if seen['n'] >= 2:
                    return io.BytesIO(b'A' * 100)
            return real_open(path, mode, *a, **kw)

        builtins.open = open_shrunk
        try:
            rc, blob, err = T.run_in_process(paxck, paxck.cmd_create, self.root)
        finally:
            builtins.open = real_open

        self.assertEqual(rc, 3)
        self.assertIn('shrink.bin', err)
        members = T.list_members(blob)
        self.assertEqual(len(members['测试.d/shrink.bin'][2]), 1000)

    def test_packed_manifest_records_packed_and_skipped_entries(self):
        """--packed-manifest 同时记录已打包（P/D）与被跳过（S）的条目。"""
        if os.name == 'nt' or getattr(os, 'geteuid', lambda: -1)() == 0:
            self.skipTest('root 无视文件权限位')
        locked = os.path.join(self.root, 'locked.txt')
        with open(locked, 'wb') as fh:
            fh.write(b'nope')
        fd, manifest = tempfile.mkstemp(prefix='paxck-manifest-')
        os.close(fd)
        os.chmod(locked, 0)
        try:
            rc, _blob, _err = T.run_cli(
                ['create', self.root, '--packed-manifest', manifest])
        finally:
            os.chmod(locked, 0o644)
        self.assertEqual(rc, 0)
        with open(manifest, 'rb') as fh:
            packed, dirs, skipped, listing_ok = prune.parse_manifest(fh.read())
        os.unlink(manifest)
        self.assertTrue(listing_ok)
        self.assertIn(self.root, dirs)
        self.assertIn(locked, skipped)
        self.assertIn(os.path.join(self.root, 'readme.txt'), packed)
        self.assertNotIn(locked, packed)

    def test_unlistable_subdirectory_is_reported(self):
        locked_dir = os.path.join(self.root, 'locked-dir')
        os.makedirs(locked_dir, exist_ok=True)
        with open(os.path.join(locked_dir, 'inner.txt'), 'wb') as fh:
            fh.write(b'inner')
        if os.name == 'nt' or getattr(os, 'geteuid', lambda: -1)() == 0:
            self.skipTest('root 无视目录权限位')
        os.chmod(locked_dir, 0)
        try:
            rc, blob, err = T.run_in_process(paxck, paxck.cmd_create, self.root)
        finally:
            os.chmod(locked_dir, 0o755)

        self.assertEqual(rc, 0)
        self.assertIn('locked-dir', err)
        self.assertIn('测试.d/readme.txt', T.list_members(blob))


class TestCreateArgumentErrors(T.BaseCase):
    def test_missing_directory(self):
        rc, out, err = T.run_cli(['create', os.path.join(self.tmp, 'nope')])
        self.assertNotEqual(rc, 0)
        self.assertIn('源目录不存在', err.decode('utf-8', 'replace'))

    def test_file_instead_of_directory(self):
        rc, out, err = T.run_cli(
            ['create', os.path.join(self.root, 'readme.txt')])
        self.assertNotEqual(rc, 0)


# --------------------------------------------------------------------------
# verify 层
# --------------------------------------------------------------------------
def verify_stdin(blob):
    return T.run_cli(['verify'], stdin=blob)


class TestVerifyReturnCodes(T.BaseCase):
    def test_healthy_xz_archive(self):
        rc, out, err = verify_stdin(T.make_archive(self.root, 'xz'))
        self.assertEqual(rc, 0)
        text = err.decode('utf-8', 'replace')
        self.assertIn('失败 0', text)

    def test_healthy_gzip_archive(self):
        rc, out, err = verify_stdin(T.make_archive(self.root, 'gzip'))
        self.assertEqual(rc, 0)

    def test_healthy_plain_tar(self):
        rc, out, err = verify_stdin(T.make_tar(self.root))
        self.assertEqual(rc, 0)

    def test_empty_input_is_failure(self):
        self.assertEqual(verify_stdin(b'')[0], 1)

    def test_directory_only_archive_is_allowed_but_flagged(self):
        """备份一个空目录是合法的，但要明确告知“没有校验任何内容”。"""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as tf:
            ti = tarfile.TarInfo('only-dir')
            ti.type = tarfile.DIRTYPE
            ti.mtime = 0
            tf.addfile(ti)
        rc, out, err = T.run_cli(['verify', '--allow-missing-inventory'],
                                 stdin=buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertIn('没有做内容校验', err.decode('utf-8', 'replace'))

    def test_archive_without_any_sha256_is_failure(self):
        """
        全是普通文件却一条 SHA-256 都没有：说明这不是 paxck 生成的归档，
        此时若判成功，等于给第三方 tar 发了免检通行证。
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as tf:
            for name, body in (('a.txt', b'aaa'), ('b.txt', b'bbb')):
                ti = tarfile.TarInfo(name)
                ti.size = len(body)
                ti.mtime = 0
                tf.addfile(ti, io.BytesIO(body))
        rc, out, err = verify_stdin(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIn('无法做内容校验', err.decode('utf-8', 'replace'))

    def test_wrong_checksum_is_detected(self):
        raw = T.make_tar(self.root)
        expected = T.sha256_of(b'hello\n').encode()
        idx = raw.find(expected)
        self.assertGreater(idx, 0)
        patched = raw[:idx] + (b'0' * len(expected)) + raw[idx + len(expected):]
        rc, out, err = verify_stdin(patched)
        self.assertEqual(rc, 1)
        self.assertIn('SHA-256 不符', err.decode('utf-8', 'replace'))

    def test_flipped_payload_byte_is_detected(self):
        raw = bytearray(T.make_tar(self.root))
        marker = b'hello\n'
        idx = raw.find(marker)
        self.assertGreater(idx, 0)
        # 第一个字节块的位置要跳过 512 字节的头
        at = raw.find(marker, 512)
        if at > 0:
            raw[at] ^= 0xff
        rc, out, err = verify_stdin(bytes(raw))
        self.assertEqual(rc, 1)

    def test_truncated_xz_stream(self):
        blob = T.make_archive(self.root, 'xz')
        rc, out, err = verify_stdin(blob[: len(blob) // 2])
        self.assertEqual(rc, 1)

    def test_truncated_plain_tar(self):
        blob = T.make_tar(self.root)
        rc, out, err = verify_stdin(blob[: len(blob) // 2])
        self.assertEqual(rc, 1)

    def test_garbage_input(self):
        rc, out, err = verify_stdin(T.deterministic_bytes(4096, seed=99))
        self.assertEqual(rc, 1)

    def test_quiet_suppresses_report(self):
        rc, out, err = T.run_cli(['verify', '-q'], stdin=T.make_tar(self.root))
        self.assertEqual(rc, 0)
        self.assertEqual(err.strip(), b'')


def rebuild_tar(source, mutate=None, append=None):
    """Copy a tar member by member, then optionally append extra members.

    ``TarInfo.pax_headers`` travels with the member, so a copy keeps the
    ``PAXCK.checksum.sha256`` records exactly like the original.  ``mutate``
    returns False to drop a member, a bytes payload to replace its content
    (same length: the tar header keeps the original size), or True to keep it;
    ``append`` runs once at the end and is how a member the packer never saw
    gets planted.  Returns bytes.
    """
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source), mode='r:') as tf_in:
        with tarfile.open(fileobj=out, mode='w',
                          format=tarfile.PAX_FORMAT) as tf_out:
            for member in tf_in:
                payload = (tf_in.extractfile(member).read()
                           if member.isfile() else None)
                if mutate is not None:
                    decision = mutate(member, payload)
                    if decision is False:
                        continue
                    if isinstance(decision, (bytes, bytearray)):
                        payload = bytes(decision)
                tf_out.addfile(
                    member, io.BytesIO(payload) if payload is not None else None)
            if append is not None:
                append(tf_out)
    return out.getvalue()


def plant(tf_out, name, payload, record=True):
    """Append a member the packer never saw, optionally with a valid record."""
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    member.mode = 0o644
    member.mtime = 1700000000
    if record:
        member.pax_headers = {T.PAX_KEY: T.sha256_of(payload)}
    tf_out.addfile(member, io.BytesIO(payload))


class TestVerifyScope(T.BaseCase):
    """What `verify` protects -- see docs/flow.md 校验语义与保护范围.

    Two layers: per-file checksums for content, and the trailing inventory
    member for set membership.  These tests pin both, including the honest
    limit of the second one: it is an unsigned consistency check, so an
    attacker who rewrites the archive *and* its inventory can still hide a
    deleted member, and the escape hatch for old archives
    (``--allow-missing-inventory``) gives up the membership check by design.
    """

    def setUp(self):
        super().setUp()
        self.tree = os.path.join(self.tmp, 'scope')
        os.makedirs(os.path.join(self.tree, 'sub'))
        for name, body in (('one.txt', b'one\n'), ('two.txt', b'two\n'),
                           ('sub/three.txt', b'three\n')):
            with open(os.path.join(self.tree, *name.split('/')), 'wb') as fh:
                fh.write(body)
        self.raw = T.make_tar(self.tree)

    def verify(self, blob, *options):
        rc, out, err = T.run_cli(['verify', *options], stdin=blob)
        return rc, err.decode('utf-8', 'replace')

    def summary(self, text):
        for line in text.splitlines():
            if 'SHA-256 通过' in line:
                return line.strip()
        return text.strip()

    def test_the_no_record_column_is_counted_while_reading(self):
        """目录/链接与“普通文件缺记录”同列，但报数时分开说明。"""
        rc, text = self.verify(self.raw)
        self.assertEqual(rc, 0)
        # 5 个成员：2 个目录 + 3 个普通文件（其中 2 个嵌套在子目录里）
        self.assertEqual(
            self.summary(text),
            '共 5 个条目：SHA-256 通过 3，失败 0，无记录 2'
            '（目录/链接等 2，普通文件缺记录 0）')
        # 清单成员自身不计入总数，但会比对成员集合
        self.assertIn('成员清单：清单 5 条，归档 5 条；缺失 0，多余 0，元信息不符 0',
                      text)

    def test_a_recordless_regular_file_counts_in_the_same_column(self):
        def strip_one(member, payload):
            if member.name.endswith('one.txt'):
                member.pax_headers = {}
            return True

        rc, text = self.verify(rebuild_tar(self.raw, mutate=strip_one))
        self.assertEqual(rc, 0)          # 其它文件仍有记录，整体仍判成功
        line = self.summary(text)
        self.assertIn('通过 2', line)
        self.assertIn('目录/链接等 2，普通文件缺记录 1', line)

    def test_a_member_deleted_with_its_record_is_detected(self):
        """整条成员（连同记录）被删除：清单比对必须发现。"""
        blob = rebuild_tar(
            self.raw,
            mutate=lambda member, payload: not member.name.endswith('one.txt'))
        rc, text = self.verify(blob)
        self.assertEqual(rc, 1)
        self.assertIn('清单里有但归档中缺失的成员：scope/one.txt', text)
        self.assertIn('缺失 1，多余 0', text)

    def test_a_planted_member_is_detected_even_with_a_consistent_record(self):
        blob = rebuild_tar(
            self.raw,
            append=lambda tf: plant(tf, 'scope/extra.txt', b'planted\n'))
        rc, text = self.verify(blob)
        self.assertEqual(rc, 1)
        self.assertIn('归档里有但清单中没有的成员：scope/extra.txt', text)

    def test_a_planted_member_without_a_record_is_detected_too(self):
        blob = rebuild_tar(
            self.raw,
            append=lambda tf: plant(tf, 'scope/extra.bin', b'plant',
                                    record=False))
        rc, text = self.verify(blob)
        self.assertEqual(rc, 1)
        self.assertIn('归档里有但清单中没有的成员：scope/extra.bin', text)

    def test_changed_metadata_is_detected(self):
        """只改元信息（不动内容）也会被清单比对发现。"""
        def bump_mode(member, payload):
            if member.name.endswith('two.txt'):
                member.mode = 0o600
            return True

        rc, text = self.verify(rebuild_tar(self.raw, mutate=bump_mode))
        self.assertEqual(rc, 1)
        self.assertIn('清单与归档的同名成员不一致：scope/two.txt', text)
        self.assertIn('mode', text)

    def test_an_archive_without_the_inventory_is_refused(self):
        blob = rebuild_tar(
            self.raw, mutate=lambda member, payload: member.name != 'PAXCK.manifest')
        rc, text = self.verify(blob)
        self.assertEqual(rc, 1)
        self.assertIn('归档没有 PAXCK.manifest 成员', text)
        # 旧归档可以显式放行
        rc, text = self.verify(blob, '--allow-missing-inventory')
        self.assertEqual(rc, 0)

    def test_the_escape_hatch_gives_up_the_membership_check(self):
        """--allow-missing-inventory 只保住"内容"层，删成员不再被发现。"""
        blob = rebuild_tar(
            self.raw,
            mutate=lambda member, payload: member.name != 'PAXCK.manifest')
        rc, text = self.verify(blob, '--allow-missing-inventory')
        self.assertEqual(rc, 0)
        self.assertNotIn('成员清单', text)

    def test_editing_the_inventory_is_caught_by_its_own_record(self):
        """清单自己也是普通成员，改它的内容会被它自己的记录抓到。

        诚实的上限：清单没有签名，所以把整个归档（内容、记录、清单）重做一遍
        依然能自圆其说；那种情况只能靠签名区分，本工具没有签名。
        """
        def edit_the_inventory(member, payload):
            if member.name != 'PAXCK.manifest':
                return True
            return payload[:-1] + b'X'          # 同长度改写，只动清单内容

        rc, text = self.verify(rebuild_tar(self.raw,
                                           mutate=edit_the_inventory))
        self.assertEqual(rc, 1)
        self.assertIn('成员清单自身的 SHA-256 不符', text)
        # 干净重建的归档内部自洽，因此校验通过（见上面的上限说明）。
        rebuilt, _text = self.verify(T.make_tar(self.tree))
        self.assertEqual(rebuilt, 0)

    def test_content_change_is_still_caught(self):
        """反向保证：真正的改内容仍然一定失败。"""
        blob = self.raw.replace(b'one\n', b'ONE\n', 1)
        rc, out, err = verify_stdin(blob)
        self.assertEqual(rc, 1)
        self.assertIn('SHA-256 不符', err.decode('utf-8', 'replace'))


class TestVerifyInputHandling(unittest.TestCase):
    def test_missing_file_is_a_clean_failure(self):
        rc, out, err = T.run_cli(['verify', '/no/such/archive.tar.xz'])
        self.assertEqual(rc, 1)
        self.assertNotIn(b'Traceback', err)
        self.assertIn(b'[FAIL]', err)

    def test_directory_as_input(self):
        rc, out, err = T.run_cli(['verify', '/'])
        self.assertEqual(rc, 1)
        self.assertNotIn(b'Traceback', err)

    def test_file_path_argument_and_stdin_agree(self):
        root = T.build_tree(tempfile.mkdtemp(prefix='paxck-argtest-'))
        blob = T.make_archive(os.path.join(root, '测试.d'), 'xz')
        path = os.path.join(root, 'sample.tar.xz')
        with open(path, 'wb') as fh:
            fh.write(blob)
        try:
            self.assertEqual(T.run_cli(['verify', path])[0], 0)
            self.assertEqual(T.run_cli(['verify', '-i', path])[0], 0)
            self.assertEqual(verify_stdin(blob)[0], 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------
# extract 层
# --------------------------------------------------------------------------
class TestExtract(T.BaseCase):
    def _destination(self, name='restored'):
        return os.path.join(self.tmp, name)

    def test_healthy_archives_extract_to_new_directory(self):
        for kind in ('none', 'gzip', 'xz'):
            with self.subTest(kind=kind):
                destination = self._destination('restored-' + kind)
                blob = T.make_archive(self.root, kind)
                rc, out, err = T.run_cli(
                    ['extract', '-C', destination], stdin=blob)
                self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
                restored = os.path.join(destination, '测试.d')
                self.assertEqual(T.read_bytes(os.path.join(restored, 'readme.txt')),
                                 b'hello\n')
                self.assertEqual(
                    T.read_bytes(os.path.join(restored, 'binary.bin')),
                    T.read_bytes(os.path.join(self.root, 'binary.bin')))

    def test_file_argument_is_accepted(self):
        archive = os.path.join(self.tmp, 'input.tar.xz')
        with open(archive, 'wb') as fh:
            fh.write(T.make_archive(self.root, 'xz'))
        destination = self._destination()
        rc, out, err = T.run_cli(['extract', '-i', archive, '-C', destination])
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
        self.assertTrue(os.path.isfile(
            os.path.join(destination, '测试.d', 'readme.txt')))

    def test_missing_archive_path_is_a_clean_failure(self):
        destination = self._destination()
        rc, out, err = T.run_cli([
            'extract', '-i', os.path.join(self.tmp, 'missing.tar'),
            '-C', destination])
        self.assertEqual(rc, 1)
        self.assertIn(b'[FAIL]', err)
        self.assertNotIn(b'Traceback', err)
        self.assertFalse(os.path.lexists(destination))

    def test_bad_checksum_does_not_publish_destination(self):
        raw = T.make_tar(self.root)
        expected = T.sha256_of(b'hello\n').encode()
        idx = raw.find(expected)
        self.assertGreater(idx, 0)
        damaged = raw[:idx] + (b'0' * len(expected)) + raw[idx + len(expected):]
        destination = self._destination()
        rc, out, err = T.run_cli(['extract', '-C', destination], stdin=damaged)
        self.assertEqual(rc, 1, err.decode('utf-8', 'replace'))
        self.assertFalse(os.path.lexists(destination))

    def test_a_missing_member_blocks_recovery(self):
        """少了一个成员时不能悄悄恢复出“看起来完整”的目录。"""
        raw = rebuild_tar(
            T.make_tar(self.root),
            mutate=lambda member, payload: not member.name.endswith('readme.txt'))
        destination = self._destination()
        rc, out, err = T.run_cli(['extract', '-C', destination], stdin=raw)
        text = err.decode('utf-8', 'replace')
        self.assertEqual(rc, 1, text)
        self.assertIn('清单里有但归档中缺失的成员', text)
        self.assertFalse(os.path.lexists(destination))
        # 暂存目录也必须清掉
        self.assertEqual([n for n in os.listdir(self.tmp) if '.partial.' in n], [])

    def test_extraction_skips_the_inventory_member(self):
        """清单是记账用的，不应出现在还原结果里。"""
        destination = self._destination()
        rc, out, err = T.run_cli(['extract', '-C', destination],
                                 stdin=T.make_tar(self.root))
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
        self.assertFalse(os.path.lexists(
            os.path.join(destination, 'PAXCK.manifest')))
        self.assertFalse(os.path.lexists(os.path.join(self.tmp,
                                                      'PAXCK.manifest')))

    def test_direct_extraction_skips_the_inventory_member_too(self):
        """直接模式同样不把清单当内容写出去。"""
        destination = os.path.join(self.tmp, 'direct-restore')
        rc, out, err = T.run_cli(
            ['extract', '--direct-tarfile', '-C', destination],
            stdin=T.make_tar(self.root))
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
        self.assertFalse(os.path.lexists(
            os.path.join(destination, 'PAXCK.manifest')))
        self.assertTrue(os.path.isfile(
            os.path.join(destination, '测试.d', 'readme.txt')))

    def test_an_archive_without_an_inventory_needs_the_flag(self):
        raw = rebuild_tar(T.make_tar(self.root),
                          mutate=lambda m, d: m.name != 'PAXCK.manifest')
        destination = self._destination()
        rc, out, err = T.run_cli(['extract', '-C', destination], stdin=raw)
        self.assertEqual(rc, 1, err.decode('utf-8', 'replace'))
        self.assertIn('归档没有 PAXCK.manifest 成员', err.decode('utf-8', 'replace'))
        self.assertFalse(os.path.lexists(destination))
        rc, out, err = T.run_cli(
            ['extract', '--allow-missing-inventory', '-C', destination],
            stdin=raw)
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
        self.assertTrue(os.path.isfile(
            os.path.join(destination, '测试.d', 'readme.txt')))

    def test_path_traversal_is_rejected_before_writing(self):
        data = b'not outside the destination'
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as tf:
            member = tarfile.TarInfo('../outside.txt')
            member.size = len(data)
            member.pax_headers = {T.PAX_KEY: T.sha256_of(data)}
            tf.addfile(member, io.BytesIO(data))
        destination = self._destination()
        outside = os.path.join(self.tmp, 'outside.txt')
        rc, out, err = T.run_cli(['extract', '-C', destination], stdin=raw.getvalue())
        self.assertEqual(rc, 1, err.decode('utf-8', 'replace'))
        self.assertFalse(os.path.lexists(destination))
        self.assertFalse(os.path.lexists(outside))

    def test_existing_destination_is_never_overwritten(self):
        destination = self._destination()
        os.mkdir(destination)
        sentinel = os.path.join(destination, 'keep.txt')
        with open(sentinel, 'wb') as fh:
            fh.write(b'keep')
        rc, out, err = T.run_cli(
            ['extract', '-C', destination], stdin=T.make_tar(self.root))
        self.assertEqual(rc, 1, err.decode('utf-8', 'replace'))
        self.assertEqual(T.read_bytes(sentinel), b'keep')

    def test_hardlinks_and_symlinks_are_preserved_when_supported(self):
        first = os.path.join(self.root, 'hard-first.bin')
        second = os.path.join(self.root, 'hard-second.bin')
        with open(first, 'wb') as fh:
            fh.write(b'hard-linked payload')
        try:
            os.link(first, second)
        except OSError as e:
            self.skipTest(f'当前文件系统不能创建硬链接：{e}')
        destination = self._destination()
        rc, out, err = T.run_cli(
            ['extract', '-C', destination], stdin=T.make_tar(self.root))
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
        restored = os.path.join(destination, '测试.d')
        self.assertEqual(os.stat(os.path.join(restored, 'hard-first.bin')).st_ino,
                         os.stat(os.path.join(restored, 'hard-second.bin')).st_ino)
        source_link = os.path.join(self.root, 'link-to-file')
        restored_link = os.path.join(restored, 'link-to-file')
        if os.path.lexists(source_link):
            self.assertTrue(os.path.islink(restored_link))
            self.assertEqual(os.readlink(restored_link), os.readlink(source_link))


class TestExtractDirectTarfile(T.BaseCase):
    def test_extracts_unchecked_tar_into_an_existing_directory(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as tf:
            body = b'tarfile direct mode'
            member = tarfile.TarInfo('plain.txt')
            member.size = len(body)
            tf.addfile(member, io.BytesIO(body))

        destination = os.path.join(self.tmp, 'existing')
        os.mkdir(destination)
        sentinel = os.path.join(destination, 'keep.txt')
        with open(sentinel, 'wb') as fh:
            fh.write(b'keep')
        rc, out, err = T.run_cli(
            ['extract', '--direct-tarfile', '-C', destination],
            stdin=raw.getvalue())
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
        self.assertIn('未校验 PAX SHA-256，非原子',
                      out.decode('utf-8', 'replace'))
        self.assertEqual(T.read_bytes(sentinel), b'keep')
        self.assertEqual(T.read_bytes(os.path.join(destination, 'plain.txt')),
                         b'tarfile direct mode')

    def test_bad_direct_input_reports_tarfile_mode_and_nonzero_status(self):
        destination = os.path.join(self.tmp, 'direct-bad')
        rc, out, err = T.run_cli(
            ['extract', '--direct', '-C', destination], stdin=b'not a tar')
        self.assertEqual(rc, 1)
        self.assertIn('tarfile 直接提取失败', err.decode('utf-8', 'replace'))


class TestVersionCommands(unittest.TestCase):
    def test_paxck_version_matches_release_file(self):
        rc, out, err = T.run_cli(['--version'])
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
        expected = f'paxck {paxck.VERSION}'
        self.assertEqual(out.decode('ascii').strip(), expected)


# --------------------------------------------------------------------------
# compress 子命令
# --------------------------------------------------------------------------
class TestCompressCommand(unittest.TestCase):
    def roundtrip(self, kind, decompress):
        payload = T.deterministic_bytes(4096, seed=3)
        rc, out, err = T.run_cli(['compress', kind], stdin=payload)
        self.assertEqual(rc, 0, err)
        self.assertEqual(decompress(out), payload)

    def test_xz(self):
        self.roundtrip('xz', lzma.decompress)

    def test_gzip(self):
        self.roundtrip('gzip', gzip.decompress)

    def test_none(self):
        self.roundtrip('none', lambda b: b)

    def test_output_is_recognised_by_its_own_sniffer(self):
        payload = b'recognise me'
        rc, out, err = T.run_cli(['compress', 'xz'], stdin=payload)
        self.assertEqual(paxck.sniff(out), 'xz')
        rc, out, err = T.run_cli(['compress', 'gzip'], stdin=payload)
        self.assertEqual(paxck.sniff(out), 'gzip')

    def test_gzip_is_deterministic(self):
        payload = b'same input' * 8
        self.assertEqual(T.run_cli(['compress', 'gzip'], stdin=payload)[1],
                         T.run_cli(['compress', 'gzip'], stdin=payload)[1])

    def test_unknown_kind_is_rejected(self):
        self.assertEqual(T.run_cli(['compress', 'lzma99'], stdin=b'x')[0], 2)

    def test_pipeline_create_then_compress_then_verify(self):
        root = tempfile.mkdtemp(prefix='paxck-pipeline-')
        try:
            T.build_tree(root)
            source = os.path.join(root, '测试.d')
            raw = T.make_tar(source)
            rc, blob, err = T.run_cli(['compress', 'xz'], stdin=raw)
            self.assertEqual(rc, 0)
            self.assertEqual(T.run_cli(['verify'], stdin=blob)[0], 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
