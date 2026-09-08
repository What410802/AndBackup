#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试公共资源：定位仓库、加载 paxck 模块、构造样例目录树、造 vpn 归档。

约定（pytest / unittest 均可）：
  python3 -m pytest tests
  python3 -m unittest discover -s tests -t .
"""
import contextlib
import gzip
import io
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
SRC_DIR = os.path.join(REPO_ROOT, 'src')
PAXCK = os.path.join(SRC_DIR, 'paxck.py')
ADB_SOURCE = os.path.join(SRC_DIR, 'adb_source.py')
BACKUP_SH = os.path.join(SRC_DIR, 'backup-android.sh')
BACKUP_BAT = os.path.join(SRC_DIR, 'backup-android.bat')

PAX_KEY = 'PAXCK.checksum.sha256'


def load_paxck():
    """按模块方式加载 paxck.py（它没有 .py 后缀之外的依赖）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('paxck_under_test', PAXCK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_adb_source():
    """Load the ADB source adapter, including its sibling paxck dependency."""
    import importlib.util
    sys.path.insert(0, SRC_DIR)
    try:
        spec = importlib.util.spec_from_file_location(
            'adb_source_under_test', ADB_SOURCE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


def load_backup():
    """Load the cross-platform launcher and its sibling paxck module."""
    import importlib.util
    sys.path.insert(0, SRC_DIR)
    try:
        spec = importlib.util.spec_from_file_location(
            'backup_under_test', os.path.join(SRC_DIR, 'backup.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


def py():
    return sys.executable or 'python3'


def run_cli(args, stdin=b'', cwd=None):
    """以子进程方式跑 paxck.py，返回 (返回码, stdout, stderr)。"""
    p = subprocess.run([py(), PAXCK] + args, input=stdin,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


class _Capture:
    """同时接住 stdout.buffer（二进制归档）和 stderr（WARN 行）。"""

    def __init__(self):
        self.buffer = io.BytesIO()
        self.text = io.StringIO()

    def write(self, s):
        return self.text.write(s)

    def flush(self):
        pass

    def isatty(self):
        return False

    @property
    def encoding(self):
        return 'utf-8'

    def getvalue(self):
        return self.buffer.getvalue()

    def error_text(self):
        return self.text.getvalue()


@contextlib.contextmanager
def captured_output():
    cap = _Capture()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = cap, cap
    try:
        yield cap
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def run_in_process(mod, func, *args, **kwargs):
    """
    在测试进程内直接调用 paxck 的函数，返回 (返回值或 None, 归档字节, stderr 文本)。

    需要对 open / os.lstat 打桩的场景必须走这条路——子进程里的补丁是无效的。
    """
    result = None
    with captured_output() as cap:
        try:
            result = func(*args, **kwargs)
        except SystemExit as e:
            result = e.code
    return result, cap.getvalue(), cap.error_text()


def read_bytes(path):
    with open(path, 'rb') as fh:
        return fh.read()


def sha256_of(data: bytes) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def deterministic_bytes(n, seed=1):
    """可重复的伪随机字节，用来造有内容的文件（保证每次运行结果一致）。"""
    out = bytearray()
    x = seed & 0xffffffff
    while len(out) < n:
        x = (1103515245 * x + 12345) & 0x7fffffff
        out.extend(((x >> 16) & 0xff for _ in range(4)))
    return bytes(out[:n])


def build_tree(root):
    """
    造一棵覆盖各种情况的样例树，返回期望清单 {相对路径: 内容或标记}。

    覆盖：普通文件 / 空文件 / 多层子目录 / 中文名 / 带空格的名字 /
          指向文件的符号链接 / 指向目录的符号链接 / FIFO（无数据）。
    """
    def write(path, blob):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(blob)

    write(os.path.join(root, '测试.d', 'readme.txt'), 'hello\n'.encode())
    write(os.path.join(root, '测试.d', 'empty.bin'), b'')
    write(os.path.join(root, '测试.d', 'binary.bin'), deterministic_bytes(4096))
    # 跨过分块边界（CHUNK = 1 MiB），验证分块哈希而不是一次性读入
    write(os.path.join(root, '测试.d', 'big.bin'),
          deterministic_bytes((1 << 20) + 1234, seed=7))
    write(os.path.join(root, '测试.d', 'sub dir', 'deep.txt'), 'deep\n'.encode())
    write(os.path.join(root, '测试.d', 'sub dir', '笔记 — v2.md'),
          'note\n'.encode())
    # Windows needs Developer Mode or an elevated token to create symlinks.
    # Keep the common tree usable there; link-specific tests skip themselves.
    for target, name in (
            ('readme.txt', 'link-to-file'),
            ('sub dir', 'link-to-dir'),
            ('/nowhere/does/not/exist', 'link-broken')):
        try:
            os.symlink(target, os.path.join(root, '测试.d', name))
        except (AttributeError, OSError):
            pass
    try:
        os.mkfifo(os.path.join(root, '测试.d', 'a-fifo'))
    except (AttributeError, OSError):          # 不支持 FIFO 的文件系统直接跳过
        pass
    return root


def make_tar(root, extra_out=None):
    """调用 paxck create 生成裸 tar，返回字节流。"""
    rc, out, err = run_cli(['create', root])
    assert rc == 0, f'create 失败 rc={rc}: {err.decode(errors="replace")}'
    return out


def make_archive(root, kind='xz'):
    """生成 create|compress 的压缩归档。"""
    raw = make_tar(root)
    rc, out, err = run_cli(['compress', kind], stdin=raw)
    assert rc == 0, f'compress 失败 rc={rc}: {err.decode(errors="replace")}'
    return out


def list_members(blob):
    """把归档内容读成 {name: (type, payload_or_None, pax_headers)}。"""
    import io
    head = blob[:6]
    fobj = io.BytesIO(blob)
    if head.startswith(b'\xfd7zXZ\x00'):
        fobj = lzma.LZMAFile(fobj, 'rb')
    elif head.startswith(b'\x1f\x8b'):
        fobj = gzip.GzipFile(fileobj=fobj, mode='rb')
    result = {}
    with tarfile.open(fileobj=fobj, mode='r|') as tf:
        for m in tf:
            payload = None
            if m.isfile():
                payload = tf.extractfile(m).read()
            result[m.name] = (m.type, m.linkname, payload, m.pax_headers)
    return result


class BaseCase(unittest.TestCase):
    """提供临时目录与样例树的公共基类。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='paxck-test-')
        self.tree = build_tree(self.tmp)
        self.root = os.path.join(self.tmp, '测试.d')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def adb_candidates():
    """本机可能的 adb 路径。"""
    env = os.environ.get('ANDROBACKUP_ADB')
    home_guess = [
        os.path.expanduser('~/BiS.d/Code.d/third_party/android-sdk/'
                           'platform-tools/adb'),
        '/usr/bin/adb', '/usr/local/bin/adb',
    ]
    return ([env] if env else []) + home_guess + [shutil.which('adb') or '']


def find_adb():
    for c in adb_candidates():
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None
