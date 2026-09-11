#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``backup.py --list-tree``: a detailed listing of the Android source tree.

The tree answers "what will a backup be able to read?" without writing an
archive: one ``find -print0`` for the entry list, then one ``stat`` per entry
(so large trees are slow), all through the shared exec-out protocol in
``adbdevice.py``.
"""
import os
import shlex
import stat
import sys

import adbdevice
import i18n

_TREE_STAT = "stat -c '%f|%s|%Y|%y|%a|%u|%g' -- "
_TREE_STAT_BASIC = "stat -c '%f|%s|%Y|%y|%a' -- "


def _list_source_paths(adb, env, root):
    """Enumerate ``root`` on the device via `find -print0`.

    Returns the NUL-separated paths plus the remote ``find`` exit code, so a
    partially unreadable tree can still be listed with a warning.
    """
    payload, remote_rc = adbdevice.adb_exec_shell(
        adb, env, 'find ' + shlex.quote(root) + ' -print0')
    paths = [part.decode('utf-8', 'surrogateescape')
             for part in payload.split(b'\0') if part]
    return paths, remote_rc


def _parse_stat_line(payload):
    """Parse the ``%f|%s|%Y|%y|%a[|%u|%g]`` line; None when unusable."""
    fields = payload.decode('utf-8', 'replace').strip().split('|')
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


def _tree_type_char(mode):
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

    paths, find_rc = _list_source_paths(adb, env, source)
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
        info = _stat_entry(adb, env, path)
        if info is None:
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
        if stat.S_ISLNK(info['mode']):
            try:
                link, link_rc = adbdevice.adb_exec_shell(
                    adb, env, 'readlink -n -- ' + shlex.quote(path))
            except RuntimeError:
                link, link_rc = b'', 1
            if link_rc == 0:
                target = link.decode('utf-8', 'surrogateescape')
                if target.endswith('\n'):
                    target = target[:-1]
                line += ' -> ' + target
        body.append(line)

    lines = [f'# source: {source}', f'# serial: {serial}',
             f'# entries: {len(paths)}']
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
