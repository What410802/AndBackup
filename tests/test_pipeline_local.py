#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集成测试（离线）：把 create / compress / verify 当成一条产线跑。

关注点：
  * create | compress 的管道组合（与 backup-android.sh 里远端那条管道同构）
  * 产出的归档能被系统 tar 读.py（互操作性）
  * 解压还原后与原目录逐字节一致（符号链接、中文名、空目录都在内）
  * 非 UTF-8 文件名（surrogateescape 兼容路径）
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

TAR = shutil.which('tar')


def restore(archive, dest):
    """用系统 tar 解压；识别 xz/gzip 与否交给 tar 自己判断（-a）。"""
    os.makedirs(dest, exist_ok=True)
    subprocess.run([TAR, '-xaf', archive, '-C', dest], check=True)


def compare_trees(origin, restored, root_name):
    """
    逐项比对“原目录”与“还原目录”，返回差异列表。

    刻意用文件系统层面的观察（而不是读归档），这样能抓到
    “归档写了、但语义不对”的问题，例如软链接被还原成普通目录。
    """
    diff = []

    def walk(base):
        out = {}
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            for n in dirnames + filenames:
                full = os.path.join(dirpath, n)
                rel = os.path.relpath(full, base)
                st = os.lstat(full)
                if os.path.islink(full):
                    out[rel] = ('link', os.readlink(full))
                elif os.path.isdir(full):
                    out[rel] = ('dir', None)
                elif os.path.isfile(full):
                    with open(full, 'rb') as fh:
                        out[rel] = ('file', T.sha256_of(fh.read()))
                else:
                    out[rel] = ('other', None)
        return out

    left = walk(os.path.join(origin, root_name))
    right = walk(os.path.join(restored, root_name))
    for key in sorted(set(left) | set(right)):
        if key not in left:
            diff.append(f'多出: {key}')
        elif key not in right:
            diff.append(f'缺失: {key}')
        elif left[key] != right[key]:
            diff.append(f'不同: {key} {left[key]} != {right[key]}')
    # FIFO 在打包时不带数据，解压后可能不存在，单独宽容处理
    return [d for d in diff if 'a-fifo' not in d]


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.case = tempfile.mkdtemp(prefix='paxck-pipeline-')
        T.build_tree(self.case)
        self.root = os.path.join(self.case, '测试.d')

    def tearDown(self):
        shutil.rmtree(self.case, ignore_errors=True)


class TestPipelineVariants(PipelineCase):
    def _pipeline(self, kind):
        """create | compress > file，和生产环境那条远端管道结构一致。"""
        out = os.path.join(self.case, f'out.tar.{kind}' if kind != 'none'
                           else 'out.tar')
        create = subprocess.Popen([T.py(), T.PAXCK, 'create', self.root],
                                  stdout=subprocess.PIPE)
        with open(out, 'wb') as fh:
            compress = subprocess.Popen(
                [T.py(), T.PAXCK, 'compress', kind],
                stdin=create.stdout, stdout=fh)
        create.stdout.close()
        compress.wait()
        self.assertEqual(create.wait(), 0)
        self.assertEqual(compress.returncode, 0)
        return out

    def test_xz_pipeline_verifies(self):
        rc, out, err = T.run_cli(['verify', self._pipeline('xz')])
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))

    def test_gzip_pipeline_verifies(self):
        rc, out, err = T.run_cli(['verify', self._pipeline('gzip')])
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))

    def test_none_pipeline_verifies(self):
        rc, out, err = T.run_cli(['verify', self._pipeline('none')])
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))

    def test_plain_tar_is_posix_readable(self):
        out = self._pipeline('none')
        r = subprocess.run([TAR, '-tf', out], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        listing = r.stdout.decode('utf-8', 'replace')
        for name in ('readme.txt', 'sub dir/deep.txt', 'link-to-file'):
            self.assertIn(name, listing)


@unittest.skipIf(TAR is None, '本机没有 GNU/BSD tar，跳过互操作测试')
class TestRestoreFidelity(PipelineCase):
    def _restore_and_compare(self, kind):
        out = os.path.join(self.case, 'restore.tar.' + kind)
        raw = T.make_tar(self.root)
        rc, blob, err = T.run_cli(['compress', kind], stdin=raw)
        self.assertEqual(rc, 0, err)
        with open(out, 'wb') as fh:
            fh.write(blob)
        dest = os.path.join(self.case, 'restored')
        restore(out, dest)
        diff = compare_trees(self.case, dest, '测试.d')
        self.assertEqual(diff, [], '\n'.join(diff))

    def test_xz_restores_identically(self):
        self._restore_and_compare('xz')

    def test_gzip_restores_identically(self):
        self._restore_and_compare('gzip')

    def test_symlink_to_dir_stays_a_symlink(self):
        out = os.path.join(self.case, 'r.tar.xz')
        raw = T.make_tar(self.root)
        rc, blob, err = T.run_cli(['compress', 'xz'], stdin=raw)
        with open(out, 'wb') as fh:
            fh.write(blob)
        dest = os.path.join(self.case, 'restored')
        restore(out, dest)
        target = os.path.join(dest, '测试.d', 'link-to-dir')
        self.assertTrue(os.path.islink(target), '软链接被还原成了别的东西')
        self.assertEqual(os.readlink(target), 'sub dir')


class TestNonUtf8Names(unittest.TestCase):
    def setUp(self):
        self.case = tempfile.mkdtemp(prefix='paxck-nonutf8-')
        self.root = os.path.join(self.case, 'src').encode()
        os.makedirs(self.root)

    def tearDown(self):
        shutil.rmtree(self.case, ignore_errors=True)

    def test_invalid_utf8_filename(self):
        """
        设计文档里宣称支持非 UTF-8 文件名（surrogateescape）。
        这里造一个真正含非法字节的文件名来验证，而不是靠注释担保。
        """
        bad = self.root + b'/bad\xff\xfename.txt'
        with open(bad, 'wb') as fh:
            fh.write(b'\x00\x01\x02binary')

        # os.fsdecode 会把非法字节翻译成代理区码位，这正是 paxck 依赖的机制
        text = os.fsdecode(bad)
        self.assertTrue(any('\udc80' <= ch <= '\udcff' for ch in text))

        rc, blob, err = T.run_cli(['create', self.root.decode(
            'utf-8', 'surrogateescape')])
        self.assertEqual(rc, 0, err.decode('utf-8', 'replace'))
        members = T.list_members(blob)
        names = [n for n in members
                 if n.endswith('name.txt')]
        self.assertEqual(len(names), 1, names)
        self.assertEqual(members[names[0]][2], b'\x00\x01\x02binary')


if __name__ == '__main__':
    unittest.main()
