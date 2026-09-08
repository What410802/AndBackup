#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Android directory source adapter for the generic PAX archive writer."""
import posixpath
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import argparse

import paxck


def _adb_error(command, result):
    detail = result.stderr.decode('utf-8', 'replace').strip()
    if not detail:
        detail = f'exit {result.returncode}'
    return OSError(f'adb exec-out {command!r}: {detail}')


def _adb_exec(adb, command):
    """Run one non-PTY Android shell command and preserve stdout bytes."""
    try:
        result = subprocess.run(
            [adb, 'exec-out', 'sh', '-c', command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as e:
        raise OSError(f'无法启动 adb {adb!r}: {e}') from e
    if result.returncode:
        raise _adb_error(command, result)
    return result.stdout


def _adb_open(adb, command):
    try:
        return subprocess.Popen(
            [adb, 'exec-out', 'sh', '-c', command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise OSError(f'无法启动 adb {adb!r}: {e}') from e


def _finish_adb_stream(proc, command):
    extra = 0
    if proc.stdout is not None and not proc.stdout.closed:
        while True:
            chunk = proc.stdout.read(paxck.CHUNK)
            if not chunk:
                break
            extra += len(chunk)
        proc.stdout.close()
    stderr = proc.stderr.read() if proc.stderr is not None else b''
    rc = proc.wait()
    if rc:
        result = subprocess.CompletedProcess([], rc, stderr=stderr)
        raise _adb_error(command, result)
    return extra


def _list_paths(adb, root):
    command = f'find {shlex.quote(root)} -print0'
    raw = _adb_exec(adb, command)
    if not raw:
        raise OSError(f'adb exec-out 未列出源目录 {root!r}')
    if not raw.endswith(b'\0'):
        raise OSError('adb exec-out find 输出没有 NUL 终止，拒绝解析不完整目录清单')
    return [part.decode('utf-8', 'surrogateescape')
            for part in raw[:-1].split(b'\0') if part]


def _lstat(adb, path):
    """Read the Android entry metadata needed by the archive writer."""
    command = "stat -c '%f|%s|%Y|%y|%a' -- " + shlex.quote(path)
    raw = _adb_exec(adb, command)
    try:
        mode_hex, size, mtime_epoch, mtime_text, mode_octal = (
            raw.decode('ascii').strip().split('|'))
        mtime = float(mtime_epoch)
        fraction = re.search(r'\.(\d+)(?:\s|$)', mtime_text)
        if fraction:
            digits = fraction.group(1)
            mtime += int(digits) / (10 ** len(digits))
        return {
            'mode': int(mode_hex, 16),
            'size': int(size),
            'mtime': mtime,
            'perm': int(mode_octal, 8),
        }
    except (UnicodeDecodeError, ValueError) as e:
        raise OSError(f'无法解析 Android stat 输出 {raw!r}: {e}') from e


def _readlink(adb, path):
    raw = _adb_exec(adb, 'readlink -n -- ' + shlex.quote(path))
    return raw.decode('utf-8', 'surrogateescape')


def _hash_file(adb, path):
    command = 'cat -- ' + shlex.quote(path)
    proc = _adb_open(adb, command)
    try:
        digest, size = paxck.sha256_stream(proc.stdout)
    finally:
        _finish_adb_stream(proc, command)
    return digest, size


def _open_stream(adb, path):
    command = 'cat -- ' + shlex.quote(path)
    proc = _adb_open(adb, command)

    def finish():
        return _finish_adb_stream(proc, command)

    return proc.stdout, finish


def _tar_name(path):
    return path.encode(sys.getfilesystemencoding(), 'surrogateescape').decode(
        'utf-8', 'surrogateescape')


def write_tar(root, adb='adb', out=None):
    """Stream an Android directory into the generic PAX tar writer."""
    root = root.rstrip('/') or '/'
    if not root.startswith('/') or root == '/':
        sys.stderr.write(f'[错误] Android 源路径必须是非根绝对目录：{root!r}\n')
        return 1

    try:
        root_st = _lstat(adb, root)
        if not stat.S_ISDIR(root_st['mode']):
            sys.stderr.write(f'[错误] Android 源路径不是目录：{root}\n')
            return 1
        paths = _list_paths(adb, root)
    except OSError as e:
        sys.stderr.write(f'[错误] 无法枚举 Android 源目录：{e}\n')
        return 1

    parent = posixpath.dirname(root)
    paths.sort(key=lambda path: path.encode('utf-8', 'surrogateescape'))
    if out is None:
        out = paxck.binary_stdout()
    tf = paxck.open_pax_writer(out)

    def warn(message):
        sys.stderr.write(f'[WARN] {message}\n')

    try:
        for full in paths:
            try:
                source_stat = _lstat(adb, full)
            except OSError as e:
                sys.stderr.write(f'[错误] 读取 {full} 的元数据失败：{e}\n')
                return 3

            relative = posixpath.relpath(full, parent)
            tar_info = tarfile.TarInfo(_tar_name(relative))
            tar_info.mode = source_stat['perm']
            tar_info.mtime = source_stat['mtime']
            mode = source_stat['mode']

            if stat.S_ISDIR(mode):
                tar_info.type = tarfile.DIRTYPE
                tf.addfile(tar_info)
                continue

            if stat.S_ISLNK(mode):
                try:
                    tar_info.type = tarfile.SYMTYPE
                    tar_info.linkname = _readlink(adb, full)
                    tf.addfile(tar_info)
                except OSError as e:
                    sys.stderr.write(f'[错误] 读取符号链接 {relative} 失败：{e}\n')
                    return 3
                continue

            if not stat.S_ISREG(mode):
                warn(f'跳过非普通文件 {relative}')
                continue

            rc, _written = paxck.write_regular(
                tf, tar_info, source_stat['size'],
                lambda path=full: _hash_file(adb, path),
                lambda path=full: _open_stream(adb, path),
                relative, True, warn)
            if rc:
                return rc
    finally:
        tf.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='经 adb exec-out 把 Android 目录写为 stdout 上的裸 PAX tar')
    parser.add_argument(
        '--version', action='version', version=f'%(prog)s {paxck.VERSION}')
    parser.add_argument('directory', help='设备上的非根绝对目录')
    parser.add_argument('--adb', default='adb', help='adb 可执行文件路径（默认 adb）')
    args = parser.parse_args(argv)
    return write_tar(args.directory, args.adb)


if __name__ == '__main__':
    sys.exit(main())
