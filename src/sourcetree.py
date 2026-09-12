#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``backup.py tree``: a detailed listing of the Android source tree.

The tree answers "what will a backup be able to read?" without writing an
archive.  Three enumeration strategies share the exec-out protocol in
``adbdevice.py``:

* ``device-python`` -- one device-side Python pass: the uploaded
  ``tree_device.py`` walks the tree with ``os.scandir``/``os.lstat``, keeps the
  whole listing in memory, reports its own progress while it walks and hands
  everything over in one write (measured: 70394 entries in ~6 s, and it gets
  metadata for entries the shell cannot ``stat`` at all);
* ``oneshot`` -- one device-side shell pass: ``find`` runs the ``stat`` batches
  itself (``find ROOT -exec stat -c FORMAT -- {} +``) and the host streams the
  records back, so N entries cost a handful of adb round trips instead of N
  (measured: 5093 entries took ~17 min before, ~1.6 s now);
* ``per-entry`` -- the original behaviour, one ``stat`` per entry from the
  host: slow, but it works on every ROM, so it stays as the last fallback.

``auto`` (the default) uses the device interpreter when its environment is
already deployed -- two cheap probes, never a download -- then the one-shot
pass, and only falls back to the per-entry loop for a *structural* failure (a
lost status trailer, or not one single record).  A non-zero device exit code is
not structural by itself: Android's ``find`` reports the failures of its
batched ``stat`` calls and toybox returns 127 for an unreadable subdirectory,
so partial results are kept and reported instead.

All strategies report progress on stderr while they run; the listing itself
still goes to stdout (or to ``--tree-out``).
"""
import os
import re
import shlex
import stat
import subprocess
import sys
import threading
import time
import uuid

import adbdevice
import android_python
import device_python
import i18n
import paxck

TREE_MODES = ('auto', 'oneshot', 'per-entry', 'device-python')
DEVICE_SCRIPT = 'tree_device.py'

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

# Records the device-side Python lister writes (see src/tree_device.py).
DEVICE_PROGRESS = '\x01P'
DEVICE_UNREADABLE = '\x02U'
DEVICE_LINK = '\x03L'

_TREE_STAT = "stat -c '%f|%s|%Y|%y|%a|%u|%g' -- "
_TREE_STAT_BASIC = "stat -c '%f|%s|%Y|%y|%a' -- "

_CHUNK = 65536
_TRAILER_MARKER = b'__ANDBACKUP_RC__'
_DEVICE_TZ = None


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

    Returns ``(entries, problem, dropped, rc)``.  ``problem`` is only set for a
    structural failure that makes the whole pass unusable -- a stream without
    the status trailer, or a non-zero exit that produced no record at all --
    because the caller falls back to the very slow per-entry strategy when it
    happens.  ``dropped`` counts lines that are not stat records: the
    fragments a path containing a newline leaves behind.  Those are reported
    but never fatal; the affected entry keeps showing as unreadable ``[?]``.

    A non-zero ``rc`` *with* records is ordinary on Android: toybox ``find``
    reports the failures of the ``stat`` calls it batches, so a directory tree
    where some subdirectories are unreadable -- ``Android/data/*`` in
    particular, whose contents are permission-denied for the shell user --
    exits ``127`` even though every readable entry was listed.
    """
    fmt = _ONESHOT_STAT_FORMAT if with_owner else _ONESHOT_STAT_BASIC
    command = _ONESHOT_STAT.format(root=shlex.quote(root), fmt=fmt)
    proc = adbdevice.open_adb_shell(adb, env, command)
    entries = {}
    dropped = []

    def keep(record):
        text = record.decode('utf-8', 'surrogateescape')
        if not text:
            return
        info = _parse_stat_record(text, with_owner)
        if info is None:
            dropped.append(text[:80])
            return
        entries[info['path']] = info

    rc, detail = _consume_stream(proc, b'\n', keep, progress=progress,
                                 message_key='backup.tree.progress.receive',
                                 total=total)
    if rc is None:
        return {}, i18n.t('backup.err.no_remote_rc', command=repr(command)), 0, rc
    if rc and not entries:
        return {}, detail or f'exit {rc}', 0, rc
    return entries, None, len(dropped), rc


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


class DeviceListing:
    """Reassemble the record stream of ``tree_device.py``.

    Records arrive NUL-framed and self-describing: the device script reports
    its own progress while it walks (``\\x01P<count>``), then hands the whole
    in-memory listing over in one write.  Unlike the shell one-shot this
    survives a newline or ``|`` in a name, so nothing has to be guessed.
    """

    def __init__(self, progress, total=None):
        self.progress = progress
        self.total = total
        self.entries = {}
        self.links = {}
        self.unreadable = []
        self._pending = None
        self._last_path = None
        self.count = 0

    def record(self, raw):
        """Consume one NUL-framed record from ``tree_device.py``.

        Records arrive as bytes straight off the pipe; str is accepted as well
        so the framing can be exercised without a device.
        """
        text = (raw.decode('utf-8', 'surrogateescape')
                if isinstance(raw, bytes) else raw)
        if text.startswith(DEVICE_PROGRESS):
            # The device supervises its own walk; show it at our own pace.
            self.progress.note('backup.tree.progress.device',
                               done=text[len(DEVICE_PROGRESS):])
            return
        if text.startswith(DEVICE_UNREADABLE):
            path = text[len(DEVICE_UNREADABLE):]
            self.unreadable.append(path)
            self.entries.setdefault(path, {'path': path, 'mode': None})
            return
        if text.startswith(DEVICE_LINK):
            # The target of the entry whose metadata came just before.
            if self._last_path is not None:
                self.links[self._last_path] = text[len(DEVICE_LINK):]
            return
        if self._pending is None:
            # A metadata record; the next one carries its path.
            self._pending = text
            return
        info = _parse_stat_line(self._pending)
        self._pending = None
        if info is None:
            return
        info['path'] = text
        self.entries[text] = info
        self._last_path = text
        self.count += 1


def _posix_timezone(stat_output):
    """``'UTC-8'`` for a ``stat -c %y`` line ending in ``+0800``, else None.

    POSIX ``TZ`` wants the offset *west* of Greenwich, hence the sign flip.
    """
    match = re.search(r'([+-])(\d{2})(\d{2})\s*$', (stat_output or '').strip())
    if match is None:
        return None
    sign, hours, minutes = match.groups()
    text = 'UTC%s%d' % ('-' if sign == '+' else '+', int(hours))
    return text + (':%d' % int(minutes) if minutes != '00' else '')


def _device_timezone(adb, env):
    """The device UTC offset as a POSIX ``TZ`` value (``UTC-8``), or None.

    ``adb shell`` does not export ``TZ`` and Android has no ``zoneinfo`` tree
    for musl to read, so the device interpreter would otherwise format every
    timestamp in UTC while ``stat`` (which asks the framework) prints ``+0800``.
    The probe costs one round trip and is cached for the life of the process.
    """
    global _DEVICE_TZ
    if _DEVICE_TZ is None:
        _DEVICE_TZ = ''
        try:
            payload, remote_rc = adbdevice.adb_exec_shell(adb, env,
                                                          'stat -c %y /')
        except RuntimeError:
            remote_rc = 1
            payload = b''
        if not remote_rc:
            _DEVICE_TZ = _posix_timezone(
                payload.decode('utf-8', 'replace')) or ''
    return _DEVICE_TZ or None


def _device_python_listing(adb, env, root, settings, progress,
                           device_env=None):
    """List ``root`` with the interpreter that lives on the device.

    ``device_env`` is used when a matching environment is already deployed
    (the ``auto`` probe passes it); otherwise the interpreter is resolved --
    downloading it if the configuration allows that -- and provisioned.
    """
    log_level = str(settings.get('LOG_LEVEL', 'info')).lower()
    if device_env is None:
        download = device_python.explicit_bool(
            settings.get('DOWNLOAD_DEVICE_PYTHON', ''))
        local_python = android_python.resolve(
            (settings.get('DEVICE_PYTHON', '') or '').strip(),
            True if download is None else download,
            (settings.get('DEVICE_PYTHON_URL', '') or '').strip(),
            quiet=log_level in ('quiet', 'error'))
        device_env = device_python.provision_env(adb, env, local_python,
                                                 log_level)
    run_dir = device_env['env_dir'] + '/run/tree-' + uuid.uuid4().hex
    remote_error = run_dir + '/stderr'
    adbdevice.run_adb_checked(adb, ('shell', 'mkdir', '-p', run_dir), env,
                              i18n.t('backup.err.mkdir_run'))
    command = (f'{shlex.quote(device_env["env_dir"] + "/" + device_env["interp"])}'
               f' {shlex.quote(device_env["env_dir"] + "/" + DEVICE_SCRIPT)}'
               f' {shlex.quote(root)} 2>{shlex.quote(remote_error)}')
    timezone = _device_timezone(adb, env)
    if timezone:
        command = 'TZ=' + shlex.quote(timezone) + ' ' + command
    listing = DeviceListing(progress)
    try:
        proc = adbdevice.open_adb_shell(adb, env, command)
        rc, _detail = _consume_stream(proc, b'\0', listing.record,
                                      progress=None)
        if rc:
            result = adbdevice.run_adb(adb, ('exec-out', 'cat', remote_error),
                                       env)
            detail = result.stdout.decode('utf-8', 'replace').strip()
            raise RuntimeError(i18n.t('backup.tree.err.device_python',
                                     detail=detail or f'exit {rc}'))
    finally:
        try:
            adbdevice.run_adb(adb, ('shell', 'rm', '-rf', run_dir), env)
        except (RuntimeError, OSError):
            pass
    paths = list(listing.entries)
    return paths, listing.entries, listing.links, 0, 'device-python'


def _local_python_without_download(settings):
    """The interpreter this host already has, or ``None`` (never downloads)."""
    explicit = (settings.get('DEVICE_PYTHON', '') or '').strip()
    if explicit and os.path.exists(os.path.expanduser(explicit)):
        return os.path.abspath(os.path.expanduser(explicit))
    cached = android_python.default_prefix_dir()
    return cached if os.path.exists(cached) else None


def _cached_device_env(adb, env, settings):
    """The deployed device interpreter env, when it is already usable."""
    local_python = _local_python_without_download(settings)
    if not local_python:
        return None
    try:
        return device_python.cached_env_state(adb, env, local_python)
    except (OSError, RuntimeError):
        return None


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



def _collect(adb, env, root, settings, progress):
    """Return ``(paths, entries, links, find_rc, mode)`` for ``root``."""
    mode = str(settings.get('TREE_MODE', 'auto') or 'auto').lower()
    if mode not in TREE_MODES:
        raise RuntimeError(i18n.t('backup.err.tree_mode', mode=mode))
    if mode == 'device-python':
        return _device_python_listing(adb, env, root, settings, progress)
    if mode == 'auto':
        # Prefer the device interpreter when it is already deployed: one walk
        # instead of three, and metadata for unreadable entries as well.
        device_env = _cached_device_env(adb, env, settings)
        if device_env is not None:
            return _device_python_listing(adb, env, root, settings, progress,
                                          device_env=device_env)
        listing = _shell_oneshot(adb, env, root, settings, progress, mode)
        if listing is not None:
            return listing
        return _per_entry_listing(adb, env, root, progress)
    if mode == 'oneshot':
        listing = _shell_oneshot(adb, env, root, settings, progress, mode)
        if listing is None:
            raise RuntimeError(i18n.t('backup.tree.err.oneshot_unusable'))
        return listing
    return _per_entry_listing(adb, env, root, progress)


def _shell_oneshot(adb, env, root, settings, progress, mode):
    """The batched ``find -exec stat`` pass; ``None`` means "fall back".

    The pass is only abandoned for a *structural* problem (the device refused
    the command, or its stream ended without the status trailer): partial
    results are kept, because the alternative -- one ``stat`` per entry from
    the host -- costs about a hundred milliseconds per entry.  A directory like
    ``Android/data/com.tencent.mobileqq`` (70k entries, most of them
    unreadable, and constantly rewritten by the app) would otherwise re-walk
    for hours.
    """
    paths, find_rc = _list_source_paths(adb, env, root, progress)
    problem = None
    entries = {}
    dropped = 0
    for with_owner in (True, False):
        entries, problem, dropped, stat_rc = _oneshot_entries(
            adb, env, root, progress, total=len(paths), with_owner=with_owner)
        if problem or not paths:
            break
        if stat_rc:
            # Partial walk (unreadable subdirectories): keep it, report it.
            find_rc = find_rc or stat_rc
        if entries:
            break
        # e.g. a ROM whose stat rejects %u/%g: retry with those fields removed.
    if problem:
        if mode == 'oneshot':
            raise RuntimeError(problem)
        print(i18n.tag('warn') + ' '
              + i18n.t('backup.tree.warn.fallback', err=problem),
              file=sys.stderr)
        return None
    if dropped:
        print(i18n.tag('warn') + ' '
              + i18n.t('backup.tree.warn.dropped', count=dropped),
              file=sys.stderr)
    links = {}
    symlinks = [path for path, info in entries.items()
                if info['mode'] is not None and stat.S_ISLNK(info['mode'])]
    if symlinks:
        links = _oneshot_links(adb, env, root, progress, total=len(symlinks))
    return _merge(paths, entries), entries, links, find_rc, 'oneshot'


def _per_entry_listing(adb, env, root, progress):
    """One ``stat`` per entry from the host: slow, but every ROM supports it."""
    paths, find_rc = _list_source_paths(adb, env, root, progress)
    entries = {}
    total = len(paths)
    for index, path in enumerate(paths, 1):
        entries[path] = _stat_entry(adb, env, path) or {'path': path,
                                                        'mode': None}
        progress.note('backup.tree.progress.stat', done=index, total=total)
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


def _merge(paths, entries):
    """Union of the enumerated paths with the ones that carried metadata.

    A directory that other apps keep writing to (QQ, for instance) gains
    entries between the two device walks.  Those records are real, so they are
    kept rather than throwing the whole fast pass away.
    """
    known = set(paths)
    return paths + [path for path in entries if path not in known]


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
