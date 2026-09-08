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
  adb_source.py --adb adb /storage/emulated/0/path | paxck.py compress xz > out.tar.xz

  # 校验：自动识别 xz / gzip 压缩流，也可直接吃裸 tar 或文件路径
  paxck.py verify < out.tar.xz
  paxck.py verify -i out.tar.xz

说明：
  verify 会自动嗅探输入的魔术字节。xz 与 gzip 用标准库 lzma / gzip 原生解压，
  因此 Windows 端无需安装任何压缩工具，只要有 Python 即可完成端到端校验。
  zstd 需外部命令（Python 3.14+ 才有标准库 compression.zstd）。

退出码：
  0  成功
  1  create 参数错误 / verify 校验失败（含空归档、截断、无 SHA-256 记录）
  2  输入为 zstd 流但当前 Python 无标准库支持
  3  create 写入过程中发生不可恢复的错误，归档流已损坏
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

# pax key 名：全大写 vendor 前缀，POSIX 保留给厂商扩展，避免与未来标准冲突
PAX_KEY = 'PAXCK.checksum.sha256'
CHUNK = 1 << 20          # 1 MiB，分块哈希
BLOCKSIZE = tarfile.RECORDSIZE  # tar 记录大小 512


def binary_stdout():
    """获取二进制安全的 stdout（Windows 下 sys.stdout 是文本模式，会破坏二进制）。"""
    buf = getattr(sys.stdout, 'buffer', None)
    return buf if buf is not None else sys.stdout


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
        sys.stderr.write(
            '错误：输入是 zstd 流，但当前 Python 无标准库 zstd 支持，PATH 里也没有 zstd。\n'
            '  方案一：在主机安装 zstd\n'
            '  方案二：升级到 Python 3.14+\n'
            '  方案三：改用 xz 压缩（本脚本原生支持）\n')
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
            raise OSError(f'zstd 输入流读取失败：{self._pump_error}')
        if rc:
            detail = stderr.decode('utf-8', 'replace').strip() or f'exit {rc}'
            raise OSError(f'zstd 解压失败：{detail}')

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
    try:
        digest, actual_size = measure()
    except OSError as e:
        if strict_before_write:
            sys.stderr.write(f'[错误] 读取 {label} 失败：{e}\n')
            return 3, False
        warn(f'跳过 {label}: {e.strerror or e}')
        return 0, False

    if actual_size != expected_size:
        message = (f'{label}: 读取前后大小不一致'
                   f'（{expected_size} -> {actual_size}），文件正在被修改')
        if strict_before_write:
            sys.stderr.write(f'[错误] {message}，终止打包\n')
            return 3, False
        warn(f'跳过 {message}')
        return 0, False

    try:
        data, finish = open_stream()
    except OSError as e:
        if strict_before_write:
            sys.stderr.write(f'[错误] 再次打开 {label} 失败：{e}\n')
            return 3, False
        warn(f'跳过 {label}: {e.strerror or e}')
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
            f'[错误] 写入 {label} 时数据源失败：{write_error}\n'
            '       归档流已不可恢复，终止打包\n')
        return 3, False
    if wrapper.padded:
        sys.stderr.write(
            f'[错误] 写入 {label} 时第二遍读少了 {wrapper.padded} 字节；\n'
            '       源文件在打包期间发生变化，归档校验将失败，终止打包\n')
        return 3, False
    if extra:
        sys.stderr.write(
            f'[错误] 写入 {label} 时数据源多出 {extra} 字节；\n'
            '       源文件在打包期间发生变化，终止打包\n')
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


def cmd_create(root):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        sys.exit(f'错误：源目录不存在 -> {root}')

    # 硬链接检测：inode -> 首个已成功归档的相对路径
    seen_ino = {}
    warned = 0
    out = binary_stdout()
    # mode='w|' 表示不可 seek 的流式写入，适合管道
    tf = open_pax_writer(out)

    def warn(msg):
        sys.stderr.write(f'[WARN] {msg}\n')

    def add_symlink(full, rel, st):
        """符号链接统一走这里：名称含代理转义字节，交给 tarfile 编码。"""
        ti = tarfile.TarInfo(_encode(rel).decode('utf-8', 'surrogateescape'))
        ti.type = tarfile.SYMTYPE
        ti.linkname = os.readlink(full)
        ti.mode = stat.S_IMODE(st.st_mode)
        ti.mtime = st.st_mtime
        tf.addfile(ti)

    def walk_error(e):
        warn(f'无法遍历 {getattr(e, "filename", "?")}: {e.strerror or e}')

    try:
        # 先归档根目录自身
        st = os.lstat(root)
        ti = tarfile.TarInfo(os.path.basename(root) or '.')
        ti.type = tarfile.DIRTYPE
        ti.mode = stat.S_IMODE(st.st_mode)
        ti.mtime = st.st_mtime
        tf.addfile(ti)

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
                    continue
                ti = tarfile.TarInfo(_encode(rel).decode('utf-8', 'surrogateescape'))
                ti.type = tarfile.DIRTYPE
                ti.mode = stat.S_IMODE(st.st_mode)
                ti.mtime = st.st_mtime
                tf.addfile(ti)

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
                    continue

                if not stat.S_ISREG(st.st_mode):
                    # 设备/FIFO/socket 等不写入归档；保持流式归档可继续。
                    continue

                # 硬链接：同一 inode 第二次出现时记为 LNKTYPE
                key = (st.st_dev, st.st_ino)
                if st.st_nlink > 1 and key in seen_ino:
                    ti.type = tarfile.LNKTYPE
                    ti.linkname = seen_ino[key]
                    ti.size = 0
                    tf.addfile(ti)
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
                    continue

                # 只有完整写入成功后，才允许后续硬链接指向它
                if st.st_nlink > 1:
                    seen_ino[key] = rel

    finally:
        tf.close()
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

    sys.stderr.write(f'错误：未知压缩类型 {kind}（可选 xz / gzip / zstd / none）\n')
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
        sys.stderr.write(
            '错误：本机没有可用的 zstd 支持（无标准库 zstd，PATH 里也没有 zstd）\n'
            '  方案一：在主机安装 zstd\n'
            '  方案二：升级到 Python 3.14+\n'
            '  方案三：改用 xz 压缩（本脚本原生支持）\n')
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


def cmd_verify(quiet=False, infile=None):
    src = None
    tf = None
    stream = None
    # regular / 通过 / 失败 / 无记录的条目计数；nosum：普通文件但缺 SHA-256 记录
    total = ok = bad = skip = nosum = regular = 0
    failures = []
    truncated = False

    try:
        if infile:
            try:
                src = open(infile, 'rb')
            except OSError as e:
                sys.stderr.write(f'  [FAIL] 无法读取 {infile}：{e.strerror or e}\n')
                return 1
        else:
            src = _bin_in()

        try:
            stream = open_archive_stream(src)
            tf = tarfile.open(fileobj=stream, mode='r|')
        except (lzma.LZMAError, OSError, tarfile.TarError) as e:
            sys.stderr.write(f'  [FAIL] 无法解析归档：{e}\n')
            sys.stderr.write('         （若为压缩流，通常是传输不完整/被截断）\n')
            return 1

        try:
            for m in tf:
                total += 1
                ph = getattr(m, 'pax_headers', None) or {}
                digest = ph.get(PAX_KEY)

                if not m.isfile():
                    skip += 1
                    continue
                regular += 1
                if digest is None:
                    skip += 1
                    nosum += 1
                    continue

                fobj = tf.extractfile(m)
                if fobj is None:
                    bad += 1
                    failures.append(f'{m.name}: 无法读取')
                    continue

                actual, _ = sha256_stream(fobj)
                if actual == digest:
                    ok += 1
                else:
                    bad += 1
                    failures.append(
                        f'{m.name}: SHA-256 不符 (记录 {digest[:16]}…, 实际 {actual[:16]}…)')
            # tar 结束标志之前不一定已经读完压缩流。必须排空，才能触发 gzip/xz
            # 的尾部校验，并取得外部 zstd 的最终退出码。
            while stream.read(CHUNK):
                pass
            if hasattr(stream, 'finish'):
                stream.finish()
        except (lzma.LZMAError, tarfile.TarError, EOFError, OSError) as e:
            # 流在中途损坏/截断：已校验的部分仍有效，但整体必须判失败
            truncated = True
            failures.append(f'流在第 {total} 个条目后中断：{e}')
            bad += 1
    finally:
        if tf is not None:
            try:
                tf.close()
            except Exception:
                pass
        if stream is not None and stream is not src:
            try:
                stream.close()
            except Exception:
                pass
        # 只有自己打开的文件才负责关闭；stdin 不能关
        if src is not None and src is not _bin_in():
            try:
                src.close()
            except Exception:
                pass

    # 空归档是静默失败的典型：tar 执行失败时流仍合法，但一个条目都没有。
    # 这种情况必须判为失败，否则会把空归档当成有效备份。
    if total == 0:
        sys.stderr.write('  [FAIL] 归档为空（0 个条目）—— tar 很可能执行失败\n')
        return 1

    # 普通文件一条 SHA-256 记录都没有：说明这不是 paxck 生成的归档，
    # 本次校验实际上什么都没验证。若判成功，等于给第三方 tar 发了免检通行证。
    if bad == 0 and ok == 0 and nosum > 0:
        sys.stderr.write(
            f'  [FAIL] {nosum} 个普通文件全部缺少 {PAX_KEY} 记录，无法做内容校验。\n'
            '        该归档不是由 paxck create 生成的（或被剥离了 pax 扩展头）。\n')
        return 1

    # 一个普通文件都没有：不算失败（备份空目录是合法的），但要说清楚
    if regular == 0:
        sys.stderr.write('  注意：归档内没有普通文件，本次没有做内容校验\n')

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

    c = sub.add_parser('create', help='打包本机目录到 stdout')
    c.add_argument('directory')

    z = sub.add_parser('compress', help='把 stdin 压缩后写到 stdout（取代外部 xz/gzip）')
    z.add_argument('kind', choices=('xz', 'gzip', 'zstd', 'none'))

    v = sub.add_parser('verify', help='校验归档（自动识别 xz/gzip）')
    v.add_argument('path', nargs='?', help='归档路径；省略则从 stdin 读')
    v.add_argument('-i', '--input', dest='infile', help='同位置参数，归档路径')
    v.add_argument('-q', '--quiet', action='store_true')

    args = ap.parse_args()
    if args.cmd == 'create':
        return cmd_create(args.directory)
    if args.cmd == 'compress':
        return cmd_compress(args.kind)
    return cmd_verify(args.quiet, args.infile or args.path)


if __name__ == '__main__':
    sys.exit(main())
