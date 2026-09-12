#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paxck — 生成/校验带 PAX 内嵌 SHA-256 的 tar 归档

设计原则（针对跨平台与抗依赖）：
  * 仅用 Python 标准库：tarfile / hashlib / os / sys / argparse
  * 全程流式：分块哈希 + 文件对象直接写入，内存占用恒定，与单个文件大小无关
  * 二进制 stdout 安全：统一走 sys.stdout.buffer，Windows 下不会因 CRLF 翻译损坏数据
  * 路径编码：使用 surrogateescape 兼容非 UTF-8 文件名（Linux/Unix 常见）
  * 不依赖 GNU tar 的 --pax-option（它只支持全局 key，无法写每文件 key）

用法：
  # 打包本机目录（写 stdout 裸 tar 流）
  paxck.py create <目录> > out.tar

  # 压缩：纯 Python 实现，不需要源端有任何 xz / gzip / zstd 二进制
  paxck.py create <目录> | paxck.py compress xz > out.tar.xz

  # Android 目录由独立数据源适配器提供，再复用本压缩/校验工具
  adb_source.py pack /storage/emulated/0/path | paxck.py compress xz > out.tar.xz

  # 校验：自动识别 xz / gzip 压缩流，也可直接吃裸 tar 或文件路径
  paxck.py verify < out.tar.xz
  paxck.py verify -i out.tar.xz

  # 安全提取：校验 PAX SHA-256 后原子发布到一个尚不存在的目录
  paxck.py extract -i out.tar.xz -C restored

说明：
  verify 会自动嗅探输入的魔术字节。xz 与 gzip 用标准库 lzma / gzip 原生解压，
  因此 Windows 端无需安装任何压缩工具，只要有 Python 即可完成端到端校验。
  zstd 需外部命令（Python 3.14+ 才有标准库 compression.zstd）。

退出码：
  0  成功
  1  参数错误 / verify、extract 校验失败（含空归档、截断、无 SHA-256 记录或不安全路径）
  2  输入为 zstd 流但当前 Python 无标准库支持
  3  create 写入或 extract 落盘过程中发生不可恢复的错误
"""

import os
import sys
import stat
import gzip
import lzma
import shutil
import hashlib
import tarfile
import argparse
import subprocess
import threading

import i18n

# pax key 名：全大写 vendor 前缀，POSIX 保留给厂商扩展，避免与未来标准冲突
PAX_KEY = 'PAXCK.checksum.sha256'
CHUNK = 1 << 20          # 1 MiB，分块哈希
BLOCKSIZE = tarfile.RECORDSIZE  # tar 记录大小 512


def _read_version():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'VERSION')
    try:
        with open(path, encoding='ascii') as fh:
            return fh.read().strip() or '0.2.0'
    except OSError:
        return '0.2.0'


VERSION = _read_version()


def binary_stdout():
    """获取二进制安全的 stdout（Windows 下 sys.stdout 是文本模式，会破坏二进制）。"""
    buf = getattr(sys.stdout, 'buffer', None)
    return buf if buf is not None else sys.stdout


def configure_stdio_utf8():
    """Force human-facing text I/O to UTF-8.

    Archive bytes already go through ``sys.stdout.buffer`` (binary), so this
    only affects status messages. On Windows, piped stdout/stderr otherwise
    fall back to a legacy ANSI codepage (e.g. cp1252 on CI runners), which
    crashes with UnicodeEncodeError as soon as a Chinese message is written.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
        except (AttributeError, ValueError, OSError):
            pass


class LiveLine:
    """One overwritten status line on a TTY; plain newline lines otherwise.

    Progress/rate output uses this so it stays at a fixed position instead of
    scrolling, and ``clear()`` makes it disappear once the step is done.
    """

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        try:
            self.live = bool(self.stream.isatty())
        except (AttributeError, OSError):
            self.live = False

    def update(self, text):
        if self.live:
            self.stream.write('\r\x1b[K' + text)
        else:
            self.stream.write(text + '\n')
        self.stream.flush()

    def clear(self):
        if self.live:
            self.stream.write('\r\x1b[K')
            self.stream.flush()


def _bin_in():
    buf = getattr(sys.stdin, 'buffer', None)
    return buf if buf is not None else sys.stdin


def open_pax_writer(out):
    return tarfile.open(fileobj=out, mode='w|', format=tarfile.PAX_FORMAT)


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
    """优先标准库 zstd；旧版 Python 则使用主机 PATH 里的 zstd。"""
    try:
        from compression import zstd      # Python 3.14+
        return zstd.ZstdFile(fobj, 'rb')
    except ImportError:
        exe = shutil.which('zstd')
        if exe is not None:
            return _ZstdProcessReader(fobj, exe)
        sys.stderr.write(i18n.tag('error') + ' '
                         + i18n.t('paxck.err.zstd_stream_missing') + '\n')
        for key in ('paxck.hint.install_zstd', 'paxck.hint.upgrade_python',
                    'paxck.hint.use_xz'):
            sys.stderr.write('  ' + i18n.t(key) + '\n')
        sys.exit(2)


class _ZstdProcessReader:
    """把任意二进制输入流喂给外部 zstd，并暴露可供 tarfile 消费的 read()。"""

    def __init__(self, source, exe):
        self._source = source
        self._proc = subprocess.Popen(
            [exe, '-d', '-q', '-c'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._pump_error = None
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    def _pump(self):
        try:
            _copy_stream(self._source, self._proc.stdin)
        except (OSError, BrokenPipeError) as e:
            self._pump_error = e
        finally:
            try:
                self._proc.stdin.close()
            except OSError:
                pass

    def read(self, n=-1):
        return self._proc.stdout.read(n)

    def finish(self):
        """只在 stdout 已读到 EOF 后调用，校验解压器与输入搬运是否都成功。"""
        self._pump_thread.join()
        stderr = self._proc.stderr.read()
        rc = self._proc.wait()
        if self._pump_error is not None:
            raise OSError(i18n.t('paxck.err.zstd_pump', err=self._pump_error))
        if rc:
            detail = stderr.decode('utf-8', 'replace').strip() or f'exit {rc}'
            raise OSError(i18n.t('paxck.err.zstd_decompress', detail=detail))

    def close(self):
        if self._proc.stdout is not None and not self._proc.stdout.closed:
            self._proc.stdout.close()
        if self._proc.poll() is None:
            self._proc.terminate()
        self._pump_thread.join(timeout=1)
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()


class _PrependReader:
    """
    把为嗅探魔术字节而读出的头部重新接回流的前端。

    语义严格对齐文件对象的 read()：
      read(-1 / None) -> 头部 + 剩余全部
      read(0)         -> b''
      read(n)         -> 至多 n 字节，多出的留在缓冲区供下次读取
    旧实现在 n<=0 时会丢掉剩余数据、且 read(0) 会误吐头部字节。
    """
    def __init__(self, head, rest):
        self._buf = head
        self._rest = rest

    def read(self, n=-1):
        if n is None or n < 0:
            data = self._buf + self._rest.read()
            self._buf = b''
            return data
        if n == 0:
            return b''
        if self._buf:
            if n <= len(self._buf):
                data, self._buf = self._buf[:n], self._buf[n:]
                return data
            data, self._buf = self._buf, b''
        else:
            data = b''
        return data + self._rest.read(n - len(data))

    def peek(self, n):
        return self._buf[:n] if self._buf else self._rest.peek(n)


class _ExactReader:
    """
    保证读出的字节数恰好等于 expect：不足补 0，超出截断。

    用于第二遍写入时文件被截断/变长的情况——tar 头已经写出去了，
    数据量必须和 ti.size 一致，否则整个归档流的结构会被破坏。
    """
    def __init__(self, fobj, expect):
        self._f = fobj
        self._left = expect
        self._padded = 0

    @property
    def padded(self):
        return self._padded

    def read(self, n=-1):
        if self._left <= 0 or n == 0:
            return b''
        if n is None or n < 0:
            n = self._left
        n = min(n, self._left)
        chunks = []
        left = n
        # 管道 read(n) 合法地可以短读，不能把这种短读误判为 EOF 后立刻补零。
        # 普通文件通常一次读满；adb exec-out 的 stdout 则经常分多次到达。
        while left:
            got = self._f.read(left)
            if not got:
                break
            chunks.append(got)
            left -= len(got)
        data = b''.join(chunks)
        if left:                          # 源文件比第一遍读到时更短了
            pad = b'\0' * left
            self._padded += len(pad)
            self._left -= n
            return data + pad
        self._left -= n
        return data


def sha256_stream(fobj):
    """分块计算哈希并统计长度，内存占用恒定。返回 (hexdigest, 字节数)。"""
    h = hashlib.sha256()
    size = 0
    while True:
        b = fobj.read(CHUNK)
        if not b:
            break
        size += len(b)
        h.update(b)
    return h.hexdigest(), size


def write_regular(tf, ti, expected_size, measure, open_stream, label,
                  strict_before_write, warn):
    """Write one regular-file entry independently of its local/ADB source."""
    error_tag = i18n.tag('error')
    try:
        digest, actual_size = measure()
    except OSError as e:
        if strict_before_write:
            sys.stderr.write(error_tag + ' '
                             + i18n.t('paxck.err.read_failed', label=label,
                                      err=i18n.os_error(e))
                             + '\n')
            return 3, False
        warn(i18n.t('paxck.warn.skip_read', label=label,
                    err=i18n.os_error(e)))
        return 0, False

    if actual_size != expected_size:
        message = i18n.t('paxck.warn.size_changed', label=label,
                         expected=expected_size, actual=actual_size)
        if strict_before_write:
            sys.stderr.write(error_tag + ' '
                             + i18n.t('paxck.err.abort_pack', message=message)
                             + '\n')
            return 3, False
        warn(i18n.t('paxck.warn.skip_read', label=label, err=message))
        return 0, False

    try:
        data, finish = open_stream()
    except OSError as e:
        if strict_before_write:
            sys.stderr.write(error_tag + ' '
                             + i18n.t('paxck.err.reopen_failed', label=label,
                                      err=i18n.os_error(e)) + '\n')
            return 3, False
        warn(i18n.t('paxck.warn.skip_read', label=label,
                    err=i18n.os_error(e)))
        return 0, False

    ti.size = actual_size
    ti.pax_headers = {PAX_KEY: digest}
    wrapper = _ExactReader(data, actual_size)
    write_error = None
    extra = 0
    try:
        tf.addfile(ti, wrapper)
    except OSError as e:
        write_error = e
    finally:
        try:
            extra = finish()
        except OSError as e:
            write_error = write_error or e

    if write_error is not None:
        sys.stderr.write(
            error_tag + ' '
            + i18n.t('paxck.err.write_source_failed', label=label,
                     err=write_error) + '\n'
            + '       ' + i18n.t('paxck.err.stream_unrecoverable') + '\n')
        return 3, False
    if wrapper.padded:
        sys.stderr.write(
            error_tag + ' '
            + i18n.t('paxck.err.short_read', label=label, count=wrapper.padded)
            + '\n'
            + '       ' + i18n.t('paxck.err.source_changed_verify') + '\n')
        return 3, False
    if extra:
        sys.stderr.write(
            error_tag + ' '
            + i18n.t('paxck.err.extra_bytes', label=label, count=extra) + '\n'
            + '       ' + i18n.t('paxck.err.source_changed') + '\n')
        return 3, False
    return 0, True


def _local_measure(path):
    with open(path, 'rb') as fh:
        return sha256_stream(fh)


def _local_open_stream(path):
    fh = open(path, 'rb')

    def finish():
        fh.close()
        return 0

    return fh, finish


def _encode(name):
    """用 surrogateescape 编码路径，容忍非 UTF-8 字节的文件名。"""
    enc = sys.getfilesystemencoding()
    return name.encode(enc, 'surrogateescape')


def _tar_relpath(path, start):
    """Return a portable POSIX tar name for a host filesystem path."""
    rel = os.path.relpath(path, start)
    # tar headers always use '/', even when the producer runs on Windows.
    return rel.replace('\\', '/')


class PackedManifest:
    """Record which entries were packed, for ``backup.py --prune-source``.

    Records are NUL-terminated ``<status>:<path>`` items: ``P`` a packed entry
    that is not a directory, ``D`` a packed directory, ``S`` a listed but
    unpacked entry, ``L`` a marker that the listing was incomplete.
    """

    def __init__(self, path=None):
        self._fh = open(path, 'wb') if path else None

    def packed(self, path, is_dir=False):
        self._write('D' if is_dir else 'P', path)

    def skipped(self, path):
        self._write('S', path)

    def incomplete(self, code=1):
        self._write('L', str(code))

    def _write(self, status, value):
        if self._fh is None:
            return
        self._fh.write(status.encode('ascii') + b':' +
                       value.encode('utf-8', 'surrogateescape') + b'\0')

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def cmd_create(root, manifest=None):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        sys.exit(i18n.tag('error') + ' '
                 + i18n.t('paxck.err.source_missing', root=root))

    # 硬链接检测：inode -> 首个已成功归档的相对路径
    seen_ino = {}
    warned = 0
    out = binary_stdout()
    # mode='w|' 表示不可 seek 的流式写入，适合管道
    tf = open_pax_writer(out)

    def warn(msg):
        sys.stderr.write(i18n.tag('warn') + ' ' + msg + '\n')

    def add_symlink(full, rel, st):
        """符号链接统一走这里：名称含代理转义字节，交给 tarfile 编码。"""
        ti = tarfile.TarInfo(_encode(rel).decode('utf-8', 'surrogateescape'))
        ti.type = tarfile.SYMTYPE
        ti.linkname = os.readlink(full)
        ti.mode = stat.S_IMODE(st.st_mode)
        ti.mtime = st.st_mtime
        tf.addfile(ti)

    def walk_error(e):
        warn(i18n.t('paxck.warn.walk_failed',
                    name=getattr(e, 'filename', '?'), err=i18n.os_error(e)))
        listed.incomplete(1)

    listed = PackedManifest(manifest)
    try:
        # 先归档根目录自身
        st = os.lstat(root)
        ti = tarfile.TarInfo(os.path.basename(root) or '.')
        ti.type = tarfile.DIRTYPE
        ti.mode = stat.S_IMODE(st.st_mode)
        ti.mtime = st.st_mtime
        tf.addfile(ti)
        listed.packed(root, is_dir=True)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False,
                                                    onerror=walk_error):
            dirnames.sort()
            filenames.sort()

            # 归档子目录本身。
            # 注意：os.walk 会把“指向目录的符号链接”放进 dirnames（followlinks=False
            # 只阻止递归下去），此时必须按 SYMTYPE 归档，否则还原时变成空目录。
            for d in dirnames:
                full = os.path.join(dirpath, d)
                rel = _tar_relpath(full, os.path.dirname(root))
                st = os.lstat(full)
                if stat.S_ISLNK(st.st_mode):
                    add_symlink(full, rel, st)
                    listed.packed(full)
                    continue
                ti = tarfile.TarInfo(_encode(rel).decode('utf-8', 'surrogateescape'))
                ti.type = tarfile.DIRTYPE
                ti.mode = stat.S_IMODE(st.st_mode)
                ti.mtime = st.st_mtime
                tf.addfile(ti)
                listed.packed(full, is_dir=True)

            for f in filenames:
                full = os.path.join(dirpath, f)
                rel = _tar_relpath(full, os.path.dirname(root))
                st = os.lstat(full)

                name = _encode(rel).decode('utf-8', 'surrogateescape')
                ti = tarfile.TarInfo(name)
                ti.mode = stat.S_IMODE(st.st_mode)
                ti.mtime = st.st_mtime
                ti.uid = st.st_uid
                ti.gid = st.st_gid

                if stat.S_ISLNK(st.st_mode):
                    add_symlink(full, rel, st)
                    listed.packed(full)
                    continue

                if not stat.S_ISREG(st.st_mode):
                    # 设备/FIFO/socket 等不写入归档；保持流式归档可继续。
                    listed.skipped(full)
                    continue

                # 硬链接：同一 inode 第二次出现时记为 LNKTYPE
                key = (st.st_dev, st.st_ino)
                if st.st_nlink > 1 and key in seen_ino:
                    ti.type = tarfile.LNKTYPE
                    ti.linkname = seen_ino[key]
                    ti.size = 0
                    tf.addfile(ti)
                    listed.packed(full)
                    continue

                def counted_warn(message):
                    nonlocal warned
                    warned += 1
                    warn(message)

                rc, written = write_regular(
                    tf, ti, st.st_size,
                    lambda path=full: _local_measure(path),
                    lambda path=full: _local_open_stream(path),
                    rel, False, counted_warn)
                if rc:
                    return rc
                if not written:
                    listed.skipped(full)
                    continue
                listed.packed(full)

                # 只有完整写入成功后，才允许后续硬链接指向它
                if st.st_nlink > 1:
                    seen_ino[key] = rel

    finally:
        tf.close()
        listed.close()
    return 0


def _copy_stream(src, dst):
    """按需搬运字节:内存占用恒定,与流的总长度无关。"""
    total = 0
    while True:
        b = src.read(CHUNK)
        if not b:
            break
        dst.write(b)
        total += len(b)
    return total


def cmd_compress(kind):
    """
    把 stdin 的字节流压缩后写往 stdout。

    刻意优先使用 Python 标准库，不依赖调用环境的 xz 或 gzip 二进制。
    """
    data = _bin_in()
    out = binary_stdout()

    if kind == 'none':
        _copy_stream(data, out)
        return 0

    if kind == 'xz':
        # LZMAFile.close() 会写入流尾；不能依赖 CPython 的引用计数替我们收尾。
        with lzma.LZMAFile(out, 'wb', preset=6) as z:
            _copy_stream(data, z)
        return 0

    if kind == 'gzip':
        # mtime=0：同样的输入永远得到同样的字节，便于比对重复备份
        with gzip.GzipFile(fileobj=out, mode='wb', compresslevel=6, mtime=0) as z:
            _copy_stream(data, z)
        return 0

    if kind == 'zstd':
        return _compress_zstd(data, out)

    sys.stderr.write(i18n.tag('error') + ' '
                     + i18n.t('paxck.err.unknown_compressor', kind=kind) + '\n')
    return 1


def _compress_zstd(data, out):
    """优先 3.14+ 标准库 compression.zstd；退回到主机 PATH 的 zstd。"""
    try:
        from compression import zstd                      # Python 3.14+
    except ImportError:
        zstd = None

    if zstd is not None:
        w = getattr(zstd, 'ZstdFile', None)
        if w is not None:
            with w(out, 'wb') as z:
                _copy_stream(data, z)
        else:
            with zstd.ZstdCompressor().stream_writer(out) as z:
                _copy_stream(data, z)
        return 0

    import subprocess

    exe = shutil.which('zstd')
    if exe is None:
        sys.stderr.write(i18n.tag('error') + ' '
                         + i18n.t('paxck.err.zstd_unsupported') + '\n')
        for key in ('paxck.hint.install_zstd', 'paxck.hint.upgrade_python',
                    'paxck.hint.use_xz'):
            sys.stderr.write('  ' + i18n.t(key) + '\n')
        return 2

    proc = subprocess.Popen([exe, '-12', '-c'], stdin=subprocess.PIPE, stdout=out)
    try:
        _copy_stream(data, proc.stdin)
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
    return 0 if proc.wait() == 0 else 3


def _drain_archive_stream(stream):
    """Force compressed-stream footer validation after tar's logical EOF."""
    while stream.read(CHUNK):
        pass
    if hasattr(stream, 'finish'):
        stream.finish()


# Extraction (cmd_extract / cmd_extract_direct and their helpers) moved to
# paxextract.py; paxck.py imports it lazily in main().
def main(argv=None):
    configure_stdio_utf8()
    i18n.set_language(i18n.resolve(cli=i18n.prescan_lang(argv)))
    # --lang is accepted before and after the subcommand, so help text is in the
    # requested language wherever the flag appears.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--lang', choices=i18n.LANGUAGES + (i18n.AUTO,),
                        default=None, help=i18n.lang_help())
    ap = argparse.ArgumentParser(
        prog='paxck', description=i18n.t('paxck.cli.description'),
        parents=[common])
    ap.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    sub = ap.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('create', help=i18n.t('paxck.cli.create_help'),
                       parents=[common])
    c.add_argument('directory')
    c.add_argument('--packed-manifest', metavar='PATH',
                   help=i18n.t('prune.cli.manifest_help'))

    z = sub.add_parser('compress', help=i18n.t('paxck.cli.compress_help'),
                       parents=[common])
    z.add_argument('kind', choices=('xz', 'gzip', 'zstd', 'none'))

    v = sub.add_parser('verify', help=i18n.t('paxck.cli.verify_help'),
                       parents=[common])
    v.add_argument('path', nargs='?', help=i18n.t('paxck.cli.path_help'))
    v.add_argument('-i', '--input', dest='infile',
                   help=i18n.t('paxck.cli.input_help'))
    v.add_argument('-q', '--quiet', action='store_true')

    x = sub.add_parser('extract', help=i18n.t('paxck.cli.extract_help'),
                       parents=[common])
    x.add_argument('path', nargs='?', help=i18n.t('paxck.cli.path_help'))
    x.add_argument('-i', '--input', dest='infile',
                   help=i18n.t('paxck.cli.input_help'))
    x.add_argument('-C', '--directory', required=True,
                   help=i18n.t('paxck.cli.directory_help'))
    x.add_argument('--direct-tarfile', '--direct', dest='direct',
                   action='store_true', help=i18n.t('paxck.cli.direct_help'))

    args = ap.parse_args(argv)
    if args.cmd == 'create':
        return cmd_create(args.directory, args.packed_manifest)
    if args.cmd == 'compress':
        return cmd_compress(args.kind)
    # Verification and extraction live in their own modules so the writer (the
    # part uploaded to the device in device-python mode) stays independent.
    import paxverify
    import paxextract
    if args.cmd == 'verify':
        return paxverify.cmd_verify(args.quiet, args.infile or args.path)
    if args.direct:
        return paxextract.cmd_extract_direct(args.infile or args.path,
                                             args.directory)
    return paxextract.cmd_extract(args.infile or args.path, args.directory)


if __name__ == '__main__':
    sys.exit(main())
