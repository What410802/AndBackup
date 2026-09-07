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
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

paxck = T.load_paxck()


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

    def test_head_shorter_than_magic(self):
        # 输入可能被截断到只剩两三个字节，这里不能抛异常
        self.assertIsNone(paxck.sniff(b'\xfd'))
        self.assertIsNone(paxck.sniff(b'\x1f'))


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
        typ, link, payload, _ = self.members()['测试.d/link-to-file']
        self.assertEqual(typ, tarfile.SYMTYPE)
        self.assertEqual(link, 'readme.txt')
        self.assertIsNone(payload)

    def test_symlink_to_directory_is_symlink_not_directory(self):
        """
        回归：os.walk 把“指向目录的符号链接”放进 dirnames，
        旧实现在那里一律写 DIRTYPE，导致还原后变成一个空目录、链接语义丢失。
        """
        typ, link, payload, _ = self.members()['测试.d/link-to-dir']
        self.assertEqual(typ, tarfile.SYMTYPE)
        self.assertEqual(link, 'sub dir')
        self.assertIsNone(payload)

    def test_broken_symlink_is_preserved(self):
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
        if os.geteuid() == 0:
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

    def test_unlistable_subdirectory_is_reported(self):
        locked_dir = os.path.join(self.root, 'locked-dir')
        os.makedirs(locked_dir, exist_ok=True)
        with open(os.path.join(locked_dir, 'inner.txt'), 'wb') as fh:
            fh.write(b'inner')
        if os.geteuid() == 0:
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
        self.assertIn('错误', err.decode('utf-8', 'replace'))

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
        rc, out, err = verify_stdin(buf.getvalue())
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
