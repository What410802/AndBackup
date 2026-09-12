#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``backup.py tree``: a detailed listing of the Android source tree.

The tree answers "what will a backup be able to read?" without writing an
archive.  Two enumeration strategies share the exec-out protocol in
``adbdevice.py``:

* ``oneshot`` -- one device-side pass: ``find`` runs the ``stat`` batches
  itself (``find ROOT -exec stat -c FORMAT -- {} +``) and the host streams the
  records back, so N entries cost a handful of adb round trips instead of N
  (measured: 5093 entries took ~17 min before, a few seconds now);
* ``per-entry`` -- the original behaviour, one ``stat`` per entry from the
  host: slow, but it works on every ROM, so it stays as the fallback.

``auto`` (the default) tries the one-shot pass and falls back when the device
refuses it or when its output disagrees with the directory listing -- a path
containing a newline, for example, cannot ride in a line-based record.

Both strategies report progress on stderr while they run; the listing itself
still goes to stdout (or to ``--tree-out``).
"""
import os
import shlex
import stat
import sys
import threading
import time

import adbdevice
import i18n
import paxck

TREE_MODES = ('auto', 'oneshot', 'per-entry')

# One entry per line: the stat fields, then the path.  The path is last and the
# host splits only the first N separators, so a path may itself contain them.
_ONESHOT_STAT = "find {root} -exec stat -c '{fmt}|%n' -- {{}} +"
_ONESHOT_STAT_FORMAT = '%f|%s|%Y|%y|%a|%u|%g'
_ONESHOT_STAT_BASIC = '%f|%s|%Y|%y|%a'

# NUL-separated (path, target) pairs: neither can contain a NUL, so nothing in
# the data can be confused with the framing.
_ONESHOT_LINKS = (
    "find {root} -type l -exec sh -c 'for p; do "
    "printf \"%s\\0\" \"$p\"; readlink -n -- \"$p\"; printf \"\\0\"; "
    "done' sh {{}} +")

_TREE_STAT = "stat -c '%f|%s|%Y|%y|%a|%u|%g' -- "
_TREE_STAT_BASIC = "stat -c '%f|%s|%Y|%y|%a' -- "

_CHUNK = 65536
_TRAILER_MARKER = b'__ANDBACKUP_RC__'


class _TreeProgress:
    """Throttled ``[PROGRESS]`` output on stderr (a live line on a TTY)."""

    def __init__(self, log_level, interval):
        try:
            self.interval = float(interval)
        except (TypeError, ValueError):
            self.interval = 5.0
        self.enabled = (str(log_level).lower() not in ('quiet', 'error')
                        and self.interval > 0)
        self._live = paxck.LiveLine()
        self._last = 0.0

    def note(self, message_key, **kwargs):
        if not self.enabled:
            return
        now = time.monotonic()
        if self._last and now - self._last < self.interval:
            return
        self._last = now
        self._live.update(i18n.tag('progress') + ' '
                          + i18n.t(message_key, **kwargs))

    def clear(self):
        self._live.clear()


class _Failed:
    """Minimal stand-in for a completed process, for error formatting."""

    def __init__(self, detail):
        self.returncode = 1
        self.stdout = b''
        self.stderr = (detail or '').encode('utf-8', 'replace')


def _consume_stream(proc, separator, handle, progress=None, message_key=None,
                    total=None):
    """Feed device records to ``handle`` as they arrive through ``proc``.

    Returns ``(rc, stderr_text)`` where ``rc`` is the remote exit code from the
    NUL status trailer (``None`` when the stream ended without one).
    """
    stderr_chunks = []

    def drain_stderr():
        if proc.stderr is not None:
            stderr_chunks.append(proc.stderr.read())

    drain = threading.Thread(target=drain_stderr, daemon=True)
    drain.start()
    buffer = b''
    tail = b''
    count = 0
    received = 0
    try:
        while True:
            chunk = proc.stdout.read(_CHUNK)
            if not chunk:
                break
            received += len(chunk)
            tail = (tail + chunk)[-128:]
            buffer += chunk
            parts = buffer.split(separator)
            buffer = parts.pop()
            for record in parts:
                if record.startswith(_TRAILER_MARKER):
                    # NUL framing delivers the status trailer as a record.
                    continue
                count += 1
                handle(record)
                if progress is not None:
                    progress.note(message_key, done=count, total=total,
                                  size=adbdevice.format_size(received))
    finally:
        proc.stdout.close()
        proc.wait()
        drain.join()

    data, rc = adbdevice.split_status_trailer(buffer)
    if data:
        # The final record has no trailing separator of its own.
        count += 1
        handle(data)
    if rc is None:
        # The trailer may already have been consumed as a NUL-framed record.
        _ignored, rc = adbdevice.split_status_trailer(tail)
    return rc, b''.join(stderr_chunks).decode('utf-8', 'replace').strip()


def _list_source_paths(adb, env, root, progress):
    """Enumerate ``root`` on the device via `find -print0`, streaming.

    Returns the NUL-separated paths plus the remote ``find`` exit code, so a
    partially unreadable tree can still be listed with a warning.
    """
    command = 'find ' + shlex.quote(root) + ' -print0'
    proc = adbdevice.open_adb_shell(adb, env, command)
    paths = []

    def keep(record):
        text = record.decode('utf-8', 'surrogateescape')
        if text:
            paths.append(text)

    rc, detail = _consume_stream(proc, b'\0', keep, progress=progress,
                                 message_key='backup.tree.progress.enumerate')
    if rc is None:
        raise RuntimeError(i18n.t('backup.err.no_remote_rc',
                                  command=repr(command)))
    if rc and not paths:
        raise RuntimeError(adbdevice.display_error(
            i18n.t('backup.err.adb_command_failed'), _Failed(detail)))
    return paths, rc


def _oneshot_entries(adb, env, root, progress, total, with_owner=True):
    """Collect ``{path: info}`` from one device-side ``find -exec stat`` pass.

    Returns ``(entries, problem)``: ``problem`` describes why the pass is
    unusable (non-zero remote exit, missing trailer, or a line that is not a
    stat record) so the caller can fall back to the per-entry strategy.
    """
    fmt = _ONESHOT_STAT_FORMAT if with_owner else _ONESHOT_STAT_BASIC
    command = _ONESHOT_STAT.format(root=shlex.quote(root), fmt=fmt)
    proc = adbdevice.open_adb_shell(adb, env, command)
    entries = {}
    bad = []

    def keep(record):
        text = record.decode('utf-8', 'surrogateescape')
        if not text:
            return
        info = _parse_stat_record(text, with_owner)
        if info is None:
            if len(bad) < 1:
                bad.append(text[:80])
            return
        entries[info['path']] = info

    rc, detail = _consume_stream(proc, b'\n', keep, progress=progress,
                                 message_key='backup.tree.progress.receive',
                                 total=total)
    if rc is None:
        return {}, i18n.t('backup.err.no_remote_rc', command=repr(command))
    if rc:
        return {}, detail or f'exit {rc}'
    if bad:
        return {}, bad[0]
    return entries, None


def _oneshot_links(adb, env, root, progress, total):
    """Collect ``{path: target}`` for the symlinks under ``root``."""
    command = _ONESHOT_LINKS.format(root=shlex.quote(root))
    proc = adbdevice.open_adb_shell(adb, env, command)
    records = []

    def keep(record):
        records.append(record.decode('utf-8', 'surrogateescape'))

    rc, _detail = _consume_stream(proc, b'\0', keep, progress=progress,
                                  message_key='backup.tree.progress.links',
                                  total=total)
    if rc:
        return {}
    links = {}
    for index in range(0, len(records) - 1, 2):
        links[records[index]] = records[index + 1]
    return links


def _parse_stat_record(text, with_owner=True):
    """Parse one ``%f|%s|%Y|%y|%a[|%u|%g]|%n`` record into an entry dict.

    ``with_owner`` mirrors the format the device was asked for: the owner
    columns are known from the command, so a ``|`` inside the path can never
    be mistaken for a field separator (the path is the last part either way).
    """
    fields = text.split('|', 7 if with_owner else 5)
    info = _parse_stat_line('|'.join(fields[:-1]))
    if info is None or not fields[-1]:
        return None
    info['path'] = fields[-1]
    return info


def _parse_stat_line(payload):
    """Parse the ``%f|%s|%Y|%y|%a[|%u|%g]`` line; None when unusable.

    Accepts the raw bytes of one ``stat`` call as well as the text of one
    one-shot record.
    """
    if isinstance(payload, bytes):
        payload = payload.decode('utf-8', 'replace')
    fields = payload.strip().split('|')
    if len(fields) not in (5, 7):
        return None
    mode_hex, size, _epoch, human, perm = fields[:5]
    uid, gid = fields[5:] if len(fields) == 7 else (None, None)
    try:
        return {'mode': int(mode_hex, 16), 'size': int(size),
                'mtime': human, 'perm': int(perm, 8), 'uid': uid, 'gid': gid}
    except ValueError:
        return None


def _stat_entry(adb, env, path):
    """Metadata for one device entry, or None when it cannot be read.

    ROMs whose ``stat`` lacks ``%u``/``%g`` fall back to the 5-field format;
    the owner columns then show ``-``.
    """
    for template in (_TREE_STAT, _TREE_STAT_BASIC):
        try:
            payload, remote_rc = adbdevice.adb_exec_shell(
                adb, env, template + shlex.quote(path))
        except RuntimeError:
            return None
        if remote_rc:
            return None
        info = _parse_stat_line(payload)
        if info is not None:
            return info
    return None


def _per_entry_entries(adb, env, paths, progress):
    """One ``stat`` per entry from the host (the compatible fallback)."""
    entries = {}
    total = len(paths)
    for index, path in enumerate(paths, 1):
        entries[path] = _stat_entry(adb, env, path) or {'path': path,
                                                        'mode': None}
        progress.note('backup.tree.progress.stat', done=index, total=total)
    return entries


def _collect(adb, env, root, settings, progress):
    """Return ``(paths, entries, links, find_rc, mode)`` for ``root``."""
    mode = str(settings.get('TREE_MODE', 'auto') or 'auto').lower()
    if mode not in TREE_MODES:
        raise RuntimeError(i18n.t('backup.err.tree_mode', mode=mode))
    paths, find_rc = _list_source_paths(adb, env, root, progress)
    known = set(paths)
    if mode in ('auto', 'oneshot'):
        problem = None
        entries = {}
        for with_owner in (True, False):
            entries, problem = _oneshot_entries(adb, env, root, progress,
                                                total=len(paths),
                                                with_owner=with_owner)
            if problem or not paths:
                break
            if not entries:
                # e.g. a ROM whose stat rejects %u/%g: retry with 5 fields.
                problem = 'empty listing'
                continue
            if not set(entries) <= known:
                # The two walks disagreed (concurrent change, or a path the
                # line-based record cannot carry): never print mixed metadata.
                problem = 'record for an unknown path'
                continue
            break
        if not problem:
            links = {}
            symlinks = [path for path, info in entries.items()
                        if info['mode'] is not None
                        and stat.S_ISLNK(info['mode'])]
            if symlinks:
                links = _oneshot_links(adb, env, root, progress,
                                       total=len(symlinks))
            return paths, entries, links, find_rc, 'oneshot'
        if mode == 'oneshot':
            raise RuntimeError(problem)
        print(i18n.tag('warn') + ' '
              + i18n.t('backup.tree.warn.fallback', err=problem),
              file=sys.stderr)
    entries = _per_entry_entries(adb, env, paths, progress)
    links = {}
    for path, info in entries.items():
        if info.get('mode') is not None and stat.S_ISLNK(info['mode']):
            try:
                link, link_rc = adbdevice.adb_exec_shell(
                    adb, env, 'readlink -n -- ' + shlex.quote(path))
            except RuntimeError:
                continue
            if link_rc == 0:
                target = link.decode('utf-8', 'surrogateescape')
                links[path] = target[:-1] if target.endswith('\n') else target
    return paths, entries, links, find_rc, 'per-entry'


def _tree_type_char(mode):
    if mode is None:
        return None
    if stat.S_ISDIR(mode):
        return 'd'
    if stat.S_ISLNK(mode):
        return 'l'
    if stat.S_ISREG(mode):
        return '-'
    return '?'


def cmd_tree(settings, out_path=None):
    """Write a detailed listing tree of the source directory (no backup)."""
    log_level = str(settings.get('LOG_LEVEL', 'info')).lower()
    adb = settings['ADB']
    source = (settings.get('SOURCE_DIR', '') or '').strip()
    if not source:
        raise RuntimeError(i18n.t('backup.err.source_dir_empty'))
    env = dict(os.environ)
    serial = adbdevice.resolve_device(adb, settings, log_level)
    env['ANDROID_SERIAL'] = serial
    result = adbdevice.run_adb(adb, ('get-state',), env)
    if result.returncode:
        raise RuntimeError(adbdevice.display_error(
            i18n.t('backup.err.adb_unavailable', serial=serial), result))

    progress = _TreeProgress(log_level, settings.get('PROGRESS_INTERVAL', '5'))
    paths, entries, links, find_rc, mode = _collect(
        adb, env, source.rstrip('/'), settings, progress)
    progress.clear()
    if not paths:
        raise RuntimeError(i18n.t('backup.err.no_entries', source=source))
    root = source.rstrip('/')
    body = []
    unreadable = 0
    owner_hidden = False
    for path in sorted(paths,
                       key=lambda p: p.encode('utf-8', 'surrogateescape')):
        relative = path[len(root):].strip('/')
        name = relative.rsplit('/', 1)[-1] if relative else root.rsplit('/', 1)[-1]
        indent = '    ' * relative.count('/')
        info = entries.get(path)
        if not info or info.get('mode') is None:
            unreadable += 1
            body.append(f'{indent}'
                        + i18n.t('backup.tree.unreadable_entry', name=name))
            continue
        if info['uid'] is None:
            owner_hidden = True
            owner = '     -:      -'
        else:
            owner = f"{info['uid']:>6}:{info['gid']:<6}"
        line = (f"{indent}{_tree_type_char(info['mode'])}{info['perm']:04o} "
                f"{owner} {adbdevice.format_size(info['size']):>9} "
                f"{info['mtime']} {name}")
        if stat.S_ISLNK(info['mode']) and path in links:
            line += ' -> ' + links[path]
        body.append(line)

    lines = [f'# source: {source}', f'# serial: {serial}',
             f'# entries: {len(paths)}', f'# listing: {mode}']
    if find_rc:
        lines.append(i18n.t('backup.tree.find_rc', rc=find_rc))
    if unreadable:
        lines.append(i18n.t('backup.tree.unreadable_count', count=unreadable))
    if owner_hidden:
        lines.append(i18n.t('backup.tree.no_owner'))
    lines.extend(body)
    text = '\n'.join(lines) + '\n'

    if out_path:
        with open(out_path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
        if log_level not in ('quiet', 'error'):
            print(i18n.tag('done') + ' ' + i18n.t(
                'backup.done.tree', path=out_path, count=len(paths)))
    else:
        sys.stdout.write(text)
        sys.stdout.flush()
    return 0
