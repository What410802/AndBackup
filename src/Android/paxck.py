#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paxck — 生成/校验带 pax 内嵌 SHA-256 的 tar 归档

设计原则（针对跨平台与抗依赖）：
  * 仅用 Python 标准库：tarfile / hashlib / os / sys / argparse
  * 全程流式：分块哈希 + 文件对象直接写入，内存占用恒定，与单个文件大小无关
  * 二进制 stdout 安全：统一走 sys.stdout.buffer，Windows 下不会因 CRLF 翻译损坏数据
  * 路径编码：使用 surrogateescape 兼容非 UTF-8 文件名（Linux/Unix 常见）
  * 不依赖 GNU tar 的 --pax-option（它只支持全局 key，无法写每文件 key）

用法：
  # 创建（写 stdout 裸 tar 流，可管道给 xz/zstd）
  paxck.py create <目录> | xz -6 -c -o out.tar.xz

  # 校验：自动识别 xz / gzip 压缩流，也可直接吃裸 tar 或文件路径
  paxck.py verify < out.tar.xz
  paxck.py verify -i out.tar.xz

说明：
  verify 会自动嗅探输入的魔术字节。xz 与 gzip 用标准库 lzma / gzip 原生解压，
  因此 Windows 端无需安装任何压缩工具，只要有 Python 即可完成端到端校验。
  zstd 需外部命令（Python 3.14+ 才有标准库 compression.zstd）。
"""

import os
import sys
import stat
import gzip
import lzma
import hashlib
import tarfile
import argparse

# pax key 名：全大写 vendor 前缀，POSIX 保留给厂商扩展，避免与未来标准冲突
PAX_KEY = 'PAXCK.checksum.sha256'
CHUNK = 1 << 20          # 1 MiB，分块哈希
BLOCKSIZE = tarfile.RECORDSIZE  # tar 记录大小 512


def _bin_out():
    """获取二进制安全的 stdout（Windows 下 sys.stdout 是文本模式，会破坏二进制）。"""
    buf = getattr(sys.stdout, 'buffer', None)
    return buf if buf is not None else sys.stdout


def _bin_in():
    buf = getattr(sys.stdin, 'buffer', None)
    return buf if buf is not None else sys.stdin


# 压缩流魔术字节
MAGIC = {
    b'\xfd7zXZ\x00': 'xz',
    b'\x1f\x8b':      'gzip',
    b'\x28\xb5\x2f\xfd': 'zstd',
}


def sniff(raw):
    """根据魔术字节判断压缩格式，无法识别则返回 None（视为裸 tar）。"""
    for magic, name in MAGIC.items():
        if raw.startswith(magic):
            return name
    return None


def open_archive_stream(fobj):
    """
    接收一个二进制流，嗅探压缩格式后返回解压后的二进制流。
    xz / gzip 用标准库原生处理；zstd 尝试 3.14+ 的 compression.zstd。
    """
    head = fobj.peek(6)[:6] if hasattr(fobj, 'peek') else None
    if head is None:
        head = fobj.read(6)
        fobj = _PrependReader(head, fobj)

    kind = sniff(head)
    if kind is None:
        return fobj                      # 裸 tar
    if kind == 'xz':
        return lzma.LZMAFile(fobj, 'rb')
    if kind == 'gzip':
        return gzip.GzipFile(fileobj=fobj, mode='rb')
    if kind == 'zstd':
        return _open_zstd(fobj)
    return fobj


def _open_zstd(fobj):
    """zstd 无标准库支持（3.14 前），尝试新模块，否则给出明确指引。"""
    try:
        from compression import zstd      # Python 3.14+
        return zstd.ZstdFile(fobj, 'rb')
    except ImportError:
        sys.stderr.write(
            '错误：输入是 zstd 流，但当前 Python 无标准库 zstd 支持。\n'
            '  方案一：外部解压后传入  zstd -dc a.tar.zst | paxck.py verify\n'
            '  方案二：升级到 Python 3.14+\n'
            '  方案三：改用 xz 压缩（本脚本原生支持）\n')
        sys.exit(2)


class _PrependReader:
    """把已读出的头部字节重新接回，供后续 sniff / 解压使用。"""
    def __init__(self, head, rest):
        self._buf = head
        self._rest = rest
        self._done = False

    def read(self, n=-1):
        if not self._done:
            chunk = self._buf + (self._rest.read(n) if n and n > 0 else b'')
            self._buf = b''
            self._done = True
            return chunk
        return self._rest.read(n)

    def peek(self, n):
        return self._buf[:n] if self._buf else self._rest.peek(n)


def sha256_stream(fobj):
    """分块计算哈希，内存占用恒定。"""
    h = hashlib.sha256()
    while True:
        b = fobj.read(CHUNK)
        if not b:
            break
        h.update(b)
    return h.hexdigest()


def _encode(name):
    """用 surrogateescape 编码路径，容忍非 UTF-8 字节的文件名。"""
    enc = sys.getfilesystemencoding()
    return name.encode(enc, 'surrogateescape')


def cmd_create(root, exclude_self=None):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        sys.exit(f'错误：源目录不存在 -> {root}')

    # 硬链接检测：inode -> 首个已归档的相对路径
    seen_ino = {}
    warned = 0
    out = _bin_out()
    # mode='w|' 表示不可 seek 的流式写入，适合管道
    tf = tarfile.open(fileobj=out, mode='w|', format=tarfile.PAX_FORMAT)

    try:
        # 先归档根目录自身
        st = os.lstat(root)
        ti = tarfile.TarInfo(os.path.basename(root) or '.')
        ti.type = tarfile.DIRTYPE
        ti.mode = stat.S_IMODE(st.st_mode)
        ti.mtime = st.st_mtime
        tf.addfile(ti)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            filenames.sort()

            # 归档子目录本身
            for d in dirnames:
                full = os.path.join(dirpath, d)
                rel = os.path.relpath(full, os.path.dirname(root))
                st = os.lstat(full)
                ti = tarfile.TarInfo(_encode(rel).decode('utf-8', 'surrogateescape'))
                ti.type = tarfile.DIRTYPE
                ti.mode = stat.S_IMODE(st.st_mode)
                ti.mtime = st.st_mtime
                tf.addfile(ti)

            for f in filenames:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, os.path.dirname(root))
                st = os.lstat(full)

                name = _encode(rel).decode('utf-8', 'surrogateescape')
                ti = tarfile.TarInfo(name)
                ti.mode = stat.S_IMODE(st.st_mode)
                ti.mtime = st.st_mtime
                ti.uid = st.st_uid
                ti.gid = st.st_gid

                if stat.S_ISLNK(st.st_mode):
                    ti.type = tarfile.SYMTYPE
                    ti.linkname = os.readlink(full)
                    tf.addfile(ti)
                    continue

                if not stat.S_ISREG(st.st_mode):
                    # 设备/FIFO 等：归档元数据，跳过数据
                    continue

                # 硬链接：同一 inode 第二次出现时记为 LNKTYPE
                key = (st.st_dev, st.st_ino)
                if st.st_nlink > 1 and key in seen_ino:
                    ti.type = tarfile.LNKTYPE
                    ti.linkname = seen_ino[key]
                    ti.size = 0
                    tf.addfile(ti)
                    continue

                ti.size = st.st_size
                if st.st_nlink > 1:
                    seen_ino[key] = rel

                # 流式哈希（第一遍读），然后让 tarfile 自己流式写入（第二遍读）
                # 任何读取失败（权限/文件消失/设备错误）都只跳过该条目，不中断整体
                try:
                    with open(full, 'rb') as fh:
                        digest = sha256_stream(fh)
                    ti.pax_headers = {PAX_KEY: digest}
                    with open(full, 'rb') as fh:
                        tf.addfile(ti, fh)
                except OSError as e:
                    warned += 1
                    sys.stderr.write(f'[WARN] 跳过 {rel}: {e.strerror or e}\n')
    finally:
        tf.close()
    return 0


def cmd_verify(quiet=False, infile=None):
    src = open(infile, 'rb') if infile else _bin_in()
    try:
        stream = open_archive_stream(src)
        tf = tarfile.open(fileobj=stream, mode='r|')
    except (lzma.LZMAError, OSError, tarfile.TarError) as e:
        sys.stderr.write(f'  [FAIL] 无法解析归档：{e}\n')
        sys.stderr.write('         （若为压缩流，通常是传输不完整/被截断）\n')
        return 1

    total = ok = bad = skip = 0
    failures = []

    truncated = False
    try:
        for m in tf:
            total += 1
            ph = getattr(m, 'pax_headers', None) or {}
            digest = ph.get(PAX_KEY)

            if not m.isfile():
                skip += 1
                continue
            if digest is None:
                skip += 1
                continue

            fobj = tf.extractfile(m)
            if fobj is None:
                bad += 1
                failures.append(f'{m.name}: 无法读取')
                continue

            actual = sha256_stream(fobj)
            if actual == digest:
                ok += 1
            else:
                bad += 1
                failures.append(f'{m.name}: SHA-256 不符 (记录 {digest[:16]}…, 实际 {actual[:16]}…)')
    except (lzma.LZMAError, tarfile.TarError, EOFError, OSError) as e:
        # 流在中途损坏/截断：已校验的部分仍有效，但整体必须判失败
        truncated = True
        failures.append(f'流在第 {total} 个条目后中断：{e}')
        bad += 1

    # 空归档是静默失败的典型：tar 执行失败时流仍合法，但一个条目都没有。
    # 这种情况必须判为失败，否则会把空归档当成有效备份。
    if total == 0:
        sys.stderr.write('  [FAIL] 归档为空（0 个条目）—— tar 很可能执行失败\n')
        return 1

    if not quiet:
        for line in failures[:50]:
            sys.stderr.write(f'  [FAIL] {line}\n')
        if len(failures) > 50:
            sys.stderr.write(f'  ... 其余 {len(failures) - 50} 条省略\n')
        if truncated:
            sys.stderr.write('  提示：归档不完整（传输中断？），请重新传输\n')
        sys.stderr.write(f'\n共 {total} 个条目：SHA-256 通过 {ok}，失败 {bad}，无记录 {skip}\n')

    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(
        prog='paxck',
        description='创建/校验带 pax 内嵌 SHA-256 的 tar 归档（流式，仅用标准库）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('create', help='打包目录到 stdout')
    c.add_argument('directory')

    v = sub.add_parser('verify', help='校验归档（自动识别 xz/gzip）')
    v.add_argument('path', nargs='?', help='归档路径；省略则从 stdin 读')
    v.add_argument('-i', '--input', dest='infile', help='同位置参数，归档路径')
    v.add_argument('-q', '--quiet', action='store_true')

    args = ap.parse_args()
    if args.cmd == 'create':
        return cmd_create(args.directory)
    return cmd_verify(args.quiet, args.infile or args.path)


if __name__ == '__main__':
    sys.exit(main())
