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
import time

import paxck


_STATUS_PREFIX = b'\0__ANDBACKUP_RC__'
_STATUS_RE = re.compile(rb'\0__ANDBACKUP_RC__(\d+)\0$')
_LOG_LEVELS = {'quiet': 0, 'error': 1, 'warn': 2, 'info': 3, 'debug': 4, 'trace': 5}


class _AdbProtocolError(OSError):
    """The remote shell did not return the expected status trailer."""


def _protocol_command(command):
    # Windows adb.exe may merge remote stderr into exec-out stdout.  Discard
    # it at the remote shell and append an unambiguous NUL-delimited status.
    return (f'{command} 2>/dev/null; '
            f'__andbackup_rc=$?; '
            f"printf '\\0__ANDBACKUP_RC__%s\\0' \"$__andbackup_rc\"")


def _split_status(raw, command):
    match = _STATUS_RE.search(raw)
    if not match:
        raise _AdbProtocolError(
            f'adb exec-out {command!r} 未返回有效的远端退出码')
    return raw[:match.start()], int(match.group(1))


class ProgressReporter:
    """Human-readable progress on stderr; never touches the tar stdout."""

    def __init__(self, level='info', interval=5.0, total=0):
        self.level_name = str(level or 'info').lower()
        if self.level_name not in _LOG_LEVELS:
            raise ValueError('无效日志级别：%s（可选 quiet/error/warn/info/debug/trace）' % level)
        self.level = _LOG_LEVELS[self.level_name]
        self.interval = max(0.1, float(interval))
        self.total = total
        self.done = 0
        self.bytes_read = 0
        self.current = ''
        self._last = 0.0

    def emit(self, level, message):
        if self.level >= _LOG_LEVELS[level]:
            sys.stderr.write(message + '\n')
            sys.stderr.flush()

    def start(self):
        self.emit('info', f'[进度] 已发现 {self.total} 个条目')

    def entry(self, name, size=0, skipped=False):
        self.done += 1
        suffix = '（跳过）' if skipped else ''
        self.emit('info', f'[进度] 条目 {self.done}/{self.total}，累计读取 {self._format_bytes(self.bytes_read)}：{name}{suffix}')

    def set_current(self, name):
        self.current = name
        self.emit('debug', f'[调试] 开始处理：{name}')

    def on_bytes(self, count):
        self.bytes_read += count
        now = time.monotonic()
        if self.level >= _LOG_LEVELS['info'] and now - self._last >= self.interval:
            self._last = now
            current = f'：{self.current}' if self.current else ''
            self.emit('info', f'[进度] 传输中，累计读取 {self._format_bytes(self.bytes_read)}{current}')

    @staticmethod
    def _format_bytes(value):
        units = ('B', 'KiB', 'MiB', 'GiB', 'TiB')
        amount = float(value)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f'{amount:.1f} {unit}' if unit != 'B' else f'{int(amount)} B'
            amount /= 1024

    def finish(self):
        self.emit('info', f'[进度] 完成：{self.done}/{self.total} 个条目，读取 {self._format_bytes(self.bytes_read)}')


def _adb_error(command, result):
    detail = result.stderr.decode('utf-8', 'replace').strip()
    if not detail:
        detail = f'exit {result.returncode}'
    return OSError(f'adb exec-out {command!r}: {detail}')


def _adb_exec_status(adb, command):
    """Run one non-PTY Android shell command and preserve stdout bytes."""
    try:
        result = subprocess.run(
            [adb, 'exec-out', 'sh', '-c', _protocol_command(command)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as e:
        raise OSError(f'无法启动 adb {adb!r}: {e}') from e
    try:
        payload, remote_rc = _split_status(result.stdout, command)
    except OSError:
        if result.returncode:
            raise _adb_error(command, result)
        raise
    if result.returncode:
        raise _adb_error(command, result)
    return payload, remote_rc


def _adb_exec(adb, command):
    payload, remote_rc = _adb_exec_status(adb, command)
    if remote_rc:
        raise OSError(f'adb exec-out {command!r} 远端退出码 {remote_rc}')
    return payload


def _adb_open(adb, command, on_bytes=None):
    try:
        proc = subprocess.Popen(
            [adb, 'exec-out', 'sh', '-c', _protocol_command(command)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise OSError(f'无法启动 adb {adb!r}: {e}') from e
    return proc, _AdbPayloadReader(proc.stdout, command, on_bytes)


class _AdbPayloadReader:
    """Read stdout while withholding the trailing remote status marker."""

    def __init__(self, stream, command, on_bytes=None):
        self._stream = stream
        self._command = command
        self._pending = b''
        self._eof = False
        self._status_done = False
        self._remote_rc = 0
        self._on_bytes = on_bytes

    def read(self, size=-1):
        if size == 0:
            return b''
        if size < 0:
            chunks = []
            while True:
                chunk = self.read(paxck.CHUNK)
                if not chunk:
                    return b''.join(chunks)
                chunks.append(chunk)
        keep = len(_STATUS_PREFIX) + 24
        while True:
            if self._status_done:
                payload, self._pending = self._pending[:size], self._pending[size:]
                if payload:
                    if self._on_bytes:
                        self._on_bytes(len(payload))
                    return payload
                if self._remote_rc:
                    raise OSError(
                        f'adb exec-out {self._command!r} 远端退出码 {self._remote_rc}')
                return b''

            # The marker is only trusted after EOF; a file can legally contain
            # the marker bytes as payload.
            if self._eof:
                match = _STATUS_RE.search(self._pending)
                if not match:
                    raise _AdbProtocolError(
                        f'adb exec-out {self._command!r} 未返回有效的远端退出码')
                self._pending = self._pending[:match.start()]
                self._remote_rc = int(match.group(1))
                self._status_done = True
                continue

            if len(self._pending) > keep:
                release = min(size, len(self._pending) - keep)
                payload, self._pending = self._pending[:release], self._pending[release:]
                if payload and self._on_bytes:
                    self._on_bytes(len(payload))
                return payload

            chunk = self._stream.read(paxck.CHUNK)
            if not chunk:
                self._eof = True
            else:
                self._pending += chunk

    def finish(self):
        total = 0
        while True:
            chunk = self.read(paxck.CHUNK)
            if not chunk:
                return total
            total += len(chunk)


def _finish_adb_stream(proc, reader, command):
    reader_error = None
    try:
        extra = reader.finish()
    except OSError as e:
        extra = 0
        reader_error = e
    finally:
        if proc.stdout is not None and not proc.stdout.closed:
            proc.stdout.close()
    stderr = proc.stderr.read() if proc.stderr is not None else b''
    rc = proc.wait()
    if reader_error is not None:
        raise reader_error
    if rc:
        result = subprocess.CompletedProcess([], rc, stderr=stderr)
        raise _adb_error(command, result)
    return extra


def _list_paths(adb, root, return_status=False):
    command = f'find {shlex.quote(root)} -print0'
    raw, find_rc = _adb_exec_status(adb, command)
    if not raw:
        raise OSError(f'adb exec-out 未列出源目录 {root!r}')
    if not raw.endswith(b'\0'):
        raise OSError('adb exec-out find 输出没有 NUL 终止，拒绝解析不完整目录清单')
    paths = [part.decode('utf-8', 'surrogateescape')
             for part in raw[:-1].split(b'\0') if part]
    return (paths, find_rc) if return_status else paths


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


def _hash_file(adb, path, on_bytes=None):
    command = 'cat -- ' + shlex.quote(path)
    proc, reader = _adb_open(adb, command, on_bytes)
    try:
        digest, size = paxck.sha256_stream(reader)
    finally:
        _finish_adb_stream(proc, reader, command)
    return digest, size


def _open_stream(adb, path, on_bytes=None):
    command = 'cat -- ' + shlex.quote(path)
    proc, reader = _adb_open(adb, command, on_bytes)

    def finish():
        return _finish_adb_stream(proc, reader, command)

    return reader, finish


def _tar_name(path):
    return path.encode(sys.getfilesystemencoding(), 'surrogateescape').decode(
        'utf-8', 'surrogateescape')


def write_tar(root, adb='adb', out=None, log_level='info', progress_interval=5.0):
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
        listed = _list_paths(adb, root, return_status=True)
        if isinstance(listed, tuple):
            paths, find_rc = listed
        else:  # compatibility with callers/mocks implementing the old API
            paths, find_rc = listed, 0
    except OSError as e:
        sys.stderr.write(f'[错误] 无法枚举 Android 源目录：{e}\n')
        return 1

    parent = posixpath.dirname(root)
    paths.sort(key=lambda path: path.encode('utf-8', 'surrogateescape'))
    if out is None:
        out = paxck.binary_stdout()
    tf = paxck.open_pax_writer(out)
    try:
        reporter = ProgressReporter(log_level, progress_interval, len(paths))
    except (TypeError, ValueError) as e:
        sys.stderr.write(f'[错误] {e}\n')
        tf.close()
        return 1
    reporter.start()

    def warn(message):
        reporter.emit('warn', f'[WARN] {message}')

    incomplete = bool(find_rc)
    if find_rc:
        warn(f'find 枚举源目录时返回退出码 {find_rc}；无法访问的条目将被跳过')

    def entry_warn(message):
        nonlocal incomplete
        incomplete = True
        warn(message)

    try:
        for full in paths:
            reporter.set_current(full)
            try:
                source_stat = _lstat(adb, full)
            except OSError as e:
                incomplete = True
                warn(f'跳过 {full}：读取元数据失败：{e}')
                reporter.entry(full, skipped=True)
                continue

            relative = posixpath.relpath(full, parent)
            tar_info = tarfile.TarInfo(_tar_name(relative))
            tar_info.mode = source_stat['perm']
            tar_info.mtime = source_stat['mtime']
            mode = source_stat['mode']

            if stat.S_ISDIR(mode):
                tar_info.type = tarfile.DIRTYPE
                tf.addfile(tar_info)
                reporter.entry(relative)
                continue

            if stat.S_ISLNK(mode):
                try:
                    tar_info.type = tarfile.SYMTYPE
                    tar_info.linkname = _readlink(adb, full)
                    tf.addfile(tar_info)
                except OSError as e:
                    incomplete = True
                    warn(f'跳过符号链接 {relative}：{e}')
                    reporter.entry(relative, skipped=True)
                else:
                    reporter.entry(relative)
                continue

            if not stat.S_ISREG(mode):
                warn(f'跳过非普通文件 {relative}')
                reporter.entry(relative, skipped=True)
                continue

            rc, _written = paxck.write_regular(
                tf, tar_info, source_stat['size'],
                lambda path=full: _hash_file(adb, path, reporter.on_bytes),
                lambda path=full: _open_stream(adb, path, reporter.on_bytes),
                relative, False, entry_warn)
            if rc:
                return rc
            reporter.entry(relative, skipped=not _written)
    finally:
        tf.close()
    reporter.finish()
    return 3 if incomplete else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='经 adb exec-out 把 Android 目录写为 stdout 上的裸 PAX tar')
    parser.add_argument(
        '--version', action='version', version=f'%(prog)s {paxck.VERSION}')
    parser.add_argument('directory', help='设备上的非根绝对目录')
    parser.add_argument('--adb', default='adb', help='adb 可执行文件路径（默认 adb）')
    parser.add_argument('--log-level', default='info',
                        choices=tuple(_LOG_LEVELS), help='日志级别')
    parser.add_argument('--progress-interval', default='5', metavar='SECONDS',
                        help='进度输出最小间隔秒数')
    args = parser.parse_args(argv)
    return write_tar(args.directory, args.adb, log_level=args.log_level,
                     progress_interval=args.progress_interval)


if __name__ == '__main__':
    sys.exit(main())
