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
import threading
import time

import i18n
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
            i18n.t('adb.err.no_status', command=repr(command)))
    return raw[:match.start()], int(match.group(1))


class ProgressReporter:
    """Human-readable progress on stderr; never touches the tar stdout."""

    def __init__(self, level='info', interval=5.0, total=None, show_rate=False):
        self.level_name = str(level or 'info').lower()
        if self.level_name not in _LOG_LEVELS:
            raise ValueError(i18n.t('adb.err.invalid_loglevel', level=level))
        self.level = _LOG_LEVELS[self.level_name]
        self.interval = max(0.1, float(interval))
        self.total = total
        self.show_rate = bool(show_rate)
        self.done = 0
        self.list_bytes = 0
        self.file_bytes = 0
        self.current = ''
        self._last = 0.0
        self._rate_at = time.monotonic()
        self._rate_bytes = 0
        self._live = paxck.LiveLine()

    def emit(self, level, message):
        if self.level < _LOG_LEVELS[level]:
            return
        if level == 'info' and self._live.live:
            self._live.update(message)
            return
        sys.stderr.write(message + '\n')
        sys.stderr.flush()

    def start(self):
        self.emit('info', i18n.tag('progress') + ' ' + i18n.t(
            'adb.progress.discovered', count=self.total))

    def begin_listing(self):
        self.current = i18n.t('adb.progress.enum_label')
        self.emit('info', i18n.tag('progress') + ' ' + i18n.t(
            'adb.progress.enumerating'))

    def discovered(self):
        self.done += 1

    def listing_status(self):
        self.emit('info', i18n.tag('progress') + ' ' + i18n.t(
            'adb.progress.enum_running', count=self.done,
            size=self._format_bytes(self.list_bytes)))

    def finish_listing(self, total):
        self.total = total
        self.done = 0
        self.current = ''
        self.emit('info', i18n.tag('progress') + ' ' + i18n.t(
            'adb.progress.enum_done', count=total,
            size=self._format_bytes(self.list_bytes)))

    def entry(self, name, size=0, skipped=False):
        self.done += 1
        suffix = i18n.t('adb.progress.entry_skipped') if skipped else ''
        self.emit('info', i18n.tag('progress') + ' ' + i18n.t(
            'adb.progress.entry', done=self.done, total=self.total,
            size=self._format_bytes(self.total_bytes), name=name,
            suffix=suffix))

    def set_current(self, name):
        self.current = name
        self.emit('debug', i18n.tag('debug') + ' ' + i18n.t(
            'adb.debug.entry', name=name))

    @property
    def total_bytes(self):
        return self.list_bytes + self.file_bytes

    def on_listing_bytes(self, count):
        self.list_bytes += count
        self._report_transfer()

    def on_file_bytes(self, count):
        self.file_bytes += count
        self._report_transfer()

    def _report_transfer(self):
        now = time.monotonic()
        if self.level >= _LOG_LEVELS['info'] and now - self._last >= self.interval:
            elapsed = now - self._rate_at
            delta = self.total_bytes - self._rate_bytes
            rate = delta / elapsed if elapsed else 0
            self._last = now
            self._rate_at = now
            self._rate_bytes = self.total_bytes
            current = (i18n.t('adb.progress.current', name=self.current)
                       if self.current else '')
            rate_text = (i18n.t('adb.progress.rate',
                                rate=self._format_bytes(rate))
                         if self.show_rate else '')
            self.emit('info', i18n.tag('progress') + ' ' + i18n.t(
                'adb.progress.transfer', size=self._format_bytes(self.total_bytes),
                rate=rate_text, current=current))

    @staticmethod
    def _format_bytes(value):
        units = ('B', 'KiB', 'MiB', 'GiB', 'TiB')
        amount = float(value)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f'{amount:.1f} {unit}' if unit != 'B' else f'{int(amount)} B'
            amount /= 1024

    def finish(self):
        if self._live.live:
            self._live.clear()
            return
        self.emit('info', i18n.tag('progress') + ' ' + i18n.t(
            'adb.progress.finish', done=self.done, total=self.total,
            size=self._format_bytes(self.total_bytes),
            **{'list': self._format_bytes(self.list_bytes)},
            files=self._format_bytes(self.file_bytes)))


def _adb_error(command, result):
    detail = result.stderr.decode('utf-8', 'replace').strip()
    if not detail:
        detail = f'exit {result.returncode}'
    return OSError(i18n.t('adb.err.exec_out', command=repr(command),
                          detail=detail))


def _adb_exec_status(adb, command):
    """Run one non-PTY Android shell command and preserve stdout bytes."""
    try:
        result = subprocess.run(
            [adb, 'exec-out', 'sh', '-c', _protocol_command(command)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as e:
        raise OSError(i18n.t('adb.err.launch', adb=adb, err=e)) from e
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
        raise OSError(i18n.t('adb.err.remote_rc', command=repr(command),
                             rc=remote_rc))
    return payload


def _adb_open_command(adb, command, on_bytes=None, allow_remote_failure=False):
    try:
        proc = subprocess.Popen(
            [adb, 'exec-out', 'sh', '-c', _protocol_command(command)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise OSError(i18n.t('adb.err.launch', adb=adb, err=e)) from e
    return proc, _AdbPayloadReader(
        proc.stdout, command, on_bytes, allow_remote_failure)


def _adb_open(adb, command, on_bytes=None):
    return _adb_open_command(adb, command, on_bytes)


class _AdbPayloadReader:
    """Read stdout while withholding the trailing remote status marker."""

    def __init__(self, stream, command, on_bytes=None, allow_remote_failure=False):
        self._stream = stream
        self._command = command
        self._pending = b''
        self._eof = False
        self._status_done = False
        self._remote_rc = 0
        self._on_bytes = on_bytes
        self._allow_remote_failure = allow_remote_failure

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
                if self._remote_rc and not self._allow_remote_failure:
                    raise OSError(i18n.t('adb.err.remote_rc',
                                         command=repr(self._command),
                                         rc=self._remote_rc))
                return b''

            # The marker is only trusted after EOF; a file can legally contain
            # the marker bytes as payload.
            if self._eof:
                match = _STATUS_RE.search(self._pending)
                if not match:
                    raise _AdbProtocolError(i18n.t(
                        'adb.err.no_status', command=repr(self._command)))
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


def _list_paths(adb, root, return_status=False, reporter=None):
    command = f'find {shlex.quote(root)} -print0'
    on_bytes = reporter.on_listing_bytes if reporter else None
    proc, reader = _adb_open_command(
        adb, command, on_bytes, allow_remote_failure=True)
    paths = []
    pending = b''
    timer = None
    stop_timer = threading.Event()
    if reporter:
        reporter.begin_listing()
        def report_listing():
            while not stop_timer.wait(reporter.interval):
                reporter.listing_status()
        timer = threading.Thread(target=report_listing, daemon=True)
        timer.start()
    try:
        while True:
            chunk = reader.read(paxck.CHUNK)
            if not chunk:
                break
            pending += chunk
            records = pending.split(b'\0')
            pending = records.pop()
            for part in records:
                if part:
                    paths.append(part.decode('utf-8', 'surrogateescape'))
                    if reporter:
                        reporter.discovered()
        if pending:
            raise OSError(i18n.t('adb.err.find_not_nul'))
    finally:
        if timer:
            stop_timer.set()
            timer.join()
        _finish_adb_stream(proc, reader, command)
    if not paths:
        raise OSError(i18n.t('adb.err.no_paths', root=repr(root)))
    find_rc = reader._remote_rc
    if reporter:
        reporter.finish_listing(len(paths))
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
        raise OSError(i18n.t('adb.err.stat_unparsable', raw=repr(raw),
                             err=e)) from e


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


def write_tar(root, adb='adb', out=None, log_level='info', progress_interval=5.0,
              show_rate=False, manifest=None):
    """Stream an Android directory into the generic PAX tar writer."""
    root = root.rstrip('/') or '/'
    if not root.startswith('/') or root == '/':
        sys.stderr.write(i18n.tag('error') + ' ' + i18n.t(
            'adb.err.source_root_absolute', root=repr(root)) + '\n')
        return 1

    listed = paxck.PackedManifest(manifest)
    try:
        root_st = _lstat(adb, root)
        if not stat.S_ISDIR(root_st['mode']):
            sys.stderr.write(i18n.tag('error') + ' ' + i18n.t(
                'adb.err.source_not_directory', root=root) + '\n')
            return 1
        reporter = ProgressReporter(log_level, progress_interval, show_rate=show_rate)
        paths, find_rc = _list_paths(adb, root, return_status=True,
                                     reporter=reporter)
        if reporter.total is None:
            reporter.finish_listing(len(paths))
    except OSError as e:
        sys.stderr.write(i18n.tag('error') + ' ' + i18n.t(
            'adb.err.source_list_failed', err=e) + '\n')
        return 1

    parent = posixpath.dirname(root)
    paths.sort(key=lambda path: path.encode('utf-8', 'surrogateescape'))
    if out is None:
        out = paxck.binary_stdout()
    tf = paxck.open_pax_writer(out)
    reporter.start()

    def warn(message):
        reporter.emit('warn', i18n.tag('warn') + ' ' + message)

    incomplete = bool(find_rc)
    if find_rc:
        listed.incomplete(find_rc)
        warn(i18n.t('adb.warn.find_rc', rc=find_rc))

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
                listed.skipped(full)
                warn(i18n.t('adb.warn.skip_metadata', path=full, err=e))
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
                listed.packed(full, is_dir=True)
                reporter.entry(relative)
                continue

            if stat.S_ISLNK(mode):
                try:
                    tar_info.type = tarfile.SYMTYPE
                    tar_info.linkname = _readlink(adb, full)
                    tf.addfile(tar_info)
                except OSError as e:
                    incomplete = True
                    listed.skipped(full)
                    warn(i18n.t('adb.warn.skip_symlink', name=relative, err=e))
                    reporter.entry(relative, skipped=True)
                else:
                    listed.packed(full)
                    reporter.entry(relative)
                continue

            if not stat.S_ISREG(mode):
                listed.skipped(full)
                warn(i18n.t('adb.warn.skip_non_regular', name=relative))
                reporter.entry(relative, skipped=True)
                continue

            rc, written = paxck.write_regular(
                tf, tar_info, source_stat['size'],
                lambda path=full: _hash_file(adb, path, reporter.on_file_bytes),
                lambda path=full: _open_stream(adb, path, reporter.on_file_bytes),
                relative, False, entry_warn)
            if rc:
                return rc
            if written:
                listed.packed(full)
            else:
                listed.skipped(full)
            reporter.entry(relative, skipped=not written)
    finally:
        tf.close()
        listed.close()
    reporter.finish()
    if incomplete:
        warn(i18n.t('adb.warn.incomplete'))
    return 0


def main(argv=None):
    paxck.configure_stdio_utf8()
    i18n.set_language(i18n.resolve(cli=i18n.prescan_lang(argv)))
    parser = argparse.ArgumentParser(
        description=i18n.t('adb.cli.description'))
    parser.add_argument(
        '--version', action='version', version=f'%(prog)s {paxck.VERSION}')
    parser.add_argument('--lang', choices=i18n.LANGUAGES + (i18n.AUTO,),
                        default=None, help=i18n.lang_help())
    parser.add_argument('directory', help=i18n.t('adb.cli.directory_help'))
    parser.add_argument('--adb', default='adb',
                        help=i18n.t('adb.cli.adb_help'))
    parser.add_argument('--log-level', default='info',
                        choices=tuple(_LOG_LEVELS),
                        help=i18n.t('adb.cli.log_level_help'))
    parser.add_argument('--progress-interval', default='5', metavar='SECONDS',
                        help=i18n.t('adb.cli.progress_help'))
    parser.add_argument('--show-rate', action='store_true',
                        help=i18n.t('adb.cli.show_rate_help'))
    parser.add_argument('--packed-manifest', metavar='PATH',
                        help=i18n.t('prune.cli.manifest_help'))
    args = parser.parse_args(argv)
    return write_tar(args.directory, args.adb, log_level=args.log_level,
                     progress_interval=args.progress_interval,
                     show_rate=args.show_rate,
                     manifest=args.packed_manifest)


if __name__ == '__main__':
    sys.exit(main())
