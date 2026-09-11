#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan and run deletion of source entries that were packed successfully.

This supports ``backup.py --prune-source``: once the archive has been verified
and published, the entries that actually made it into the archive may be
removed from the source directory (typically ``Android/data/<pkg>``) to free
space on the device.

Safety rules, in order of importance:

* Only entries reported as packed are ever considered; anything the source
  adapter skipped (permission denied, changed while packing, non-regular file,
  unlistable subdirectory) is never deleted.
* The source root itself is never deleted.
* A directory is deleted only when its whole listing was complete *and* no
  skipped entry lives underneath it, so an unreadable child cannot be removed
  by accident.  Directories are removed with ``rmdir``, never ``rm -rf``: if an
  entry appeared after the listing, the directory survives.
* Deletion is deepest-first, and only ever runs after the backup was verified
  and atomically published.

The manifest is produced by the packing side (``paxck.py create`` locally or
``adb_source.py`` on device) and consists of NUL-terminated records:

    P:<path>   packed entry that is not a directory
    D:<path>   packed directory
    S:<path>   listed but not packed (skipped / unsupported)
    L:<code>   the source listing was incomplete (e.g. ``find`` exit code)
"""
import sys

import adbdevice
import i18n

PACKED = 'P'
PACKED_DIR = 'D'
SKIPPED = 'S'
LISTING_INCOMPLETE = 'L'


def parse_manifest(blob):
    """Parse manifest bytes into ``(packed, packed_dirs, skipped, listing_ok)``.

    Unknown records and empty payloads are ignored; a missing ``L`` record
    means the listing was complete.
    """
    packed = []
    packed_dirs = []
    skipped = []
    listing_ok = True
    for record in blob.split(b'\0'):
        if len(record) < 2 or record[1:2] != b':':
            continue
        status = record[:1].decode('ascii', 'replace')
        value = record[2:].decode('utf-8', 'surrogateescape')
        if not value:
            continue
        if status == PACKED:
            packed.append(value)
        elif status == PACKED_DIR:
            packed_dirs.append(value)
        elif status == SKIPPED:
            skipped.append(value)
        elif status == LISTING_INCOMPLETE:
            listing_ok = False
    return packed, packed_dirs, skipped, listing_ok


def _norm(path):
    """Normalize one source path for comparison (POSIX, no trailing slash)."""
    if len(path) > 1:
        path = path.rstrip('/')
    return path


def _is_under(path, root):
    return path.startswith(root + '/')


def build_plan(packed, packed_dirs, skipped, listing_ok, root):
    """Return ``(to_delete, kept)`` for one manifest.

    ``to_delete`` is ordered deepest-first so children are removed before their
    parents; ``kept`` counts entries that stay behind (skipped entries, the
    root, out-of-root paths and directories that still contain something
    unpacked).
    """
    root = _norm(root)
    packed = [_norm(item) for item in packed]
    packed_dirs = [_norm(item) for item in packed_dirs]
    skipped = [_norm(item) for item in skipped]
    skipped_set = set(skipped)
    kept = len(skipped_set)

    candidates = []
    for path in packed:
        if _is_under(path, root):
            candidates.append(path)
        elif path != root:
            kept += 1
    for path in packed_dirs:
        if not _is_under(path, root):
            if path != root:
                kept += 1
            continue
        if not listing_ok or _has_skipped_child(path, skipped_set):
            kept += 1
            continue
        candidates.append(path)

    # Deepest first: a directory is only removed after its children.
    to_delete = sorted(set(candidates),
                       key=lambda item: (-item.count('/'), item))
    return to_delete, kept


def _has_skipped_child(directory, skipped_set):
    prefix = directory + '/'
    return any(item.startswith(prefix) for item in skipped_set)


def describe_plan(to_delete, kept):
    """One-line, localized summary of a plan (used by prompts and logs)."""
    return i18n.t('prune.info.planned', count=len(to_delete), kept=kept)


def confirm(to_delete, kept, log_level='info'):
    """Ask once before deleting, when an interactive terminal is available.

    ``--prune-source`` on the command line *is* the authorization: the prompt
    exists only as a last chance to answer ``n`` at a real terminal.  A closed
    stdin (``input`` raises EOF, which Windows also reports for a NUL stdin
    that still looks like a TTY) therefore proceeds rather than silently
    skipping, so automation behaves predictably.
    """
    import sys
    if log_level in ('quiet', 'error') or not sys.stdin.isatty():
        return True
    print(describe_plan(to_delete, kept))
    for path in to_delete[:10]:
        print('  ' + path)
    if len(to_delete) > 10:
        print('  ...')
    try:
        answer = input(i18n.t('prune.prompt.confirm', count=len(to_delete)))
    except EOFError:
        return True
    return (answer or 'y').strip().lower() not in ('n', 'no')


def chunked(paths, size=64):
    """Yield ``paths`` in batches, keeping the deepest-first order."""
    for start in range(0, len(paths), size):
        yield paths[start:start + size]


def split_plan(to_delete, packed_dirs):
    """Split one plan into ``(files, directories)``, preserving order.

    Files and directories are deleted with different commands on purpose:
    ``rm -f`` for files/symlinks, ``rmdir`` for directories.  ``rmdir`` fails
    instead of recursing, so a directory that gained an entry after the
    listing (the source app may still be writing) is kept, never wiped.
    """
    dirs = {_norm(item) for item in packed_dirs}
    files = [path for path in to_delete if path not in dirs]
    directories = [path for path in to_delete if path in dirs]
    return files, directories


def remove_command(paths, rm='rm -f -- '):
    """Build the device shell command deleting one batch of files/symlinks."""
    import shlex
    return rm + ' '.join(shlex.quote(path) for path in paths)


def rmdir_command(paths, rmdir='rmdir -- '):
    """Build the device shell command removing the listed empty directories."""
    import shlex
    return rmdir + ' '.join(shlex.quote(path) for path in paths)


def prune_source(adb, env, manifest_path, source, log_level, dry_run=False):
    """Delete the source entries that were packed, after a verified publish.

    Only entries the packer reported as packed are considered; a directory is
    deleted only when its listing was complete and nothing underneath it was
    skipped, so an unreadable child cannot disappear by accident.
    """
    def warn(message):
        sys.stderr.write(i18n.tag('warn') + ' ' + message + '\n')
        sys.stderr.flush()

    def info(message):
        if log_level not in ('quiet', 'error'):
            print(i18n.tag('info') + ' ' + message)

    try:
        with open(manifest_path, 'rb') as fh:
            blob = fh.read()
    except OSError:
        blob = b''
    if not blob:
        warn(i18n.t('prune.warn.no_manifest'))
        return

    packed, packed_dirs, skipped, listing_ok = parse_manifest(blob)
    to_delete, kept = build_plan(packed, packed_dirs, skipped, listing_ok,
                                 source)
    if not listing_ok:
        warn(i18n.t('prune.warn.incomplete_listing'))
    if not to_delete:
        info(i18n.t('prune.info.nothing'))
        return
    if dry_run:
        print(i18n.tag('info') + ' ' + describe_plan(to_delete, kept))
        for path in to_delete:
            print('  ' + path)
        print(i18n.tag('info') + ' '
              + i18n.t('prune.info.dry_run', count=len(to_delete)))
        return
    if not confirm(to_delete, kept, log_level):
        return

    failed = []
    kept_dirs = []
    files, directories = split_plan(to_delete, packed_dirs)
    # Files first (deepest-first order is preserved inside each group), then
    # the now-empty directories.  Directories use `rmdir`, never `rm -rf`, so a
    # directory that gained an entry after the listing survives.
    for batch in chunked(files):
        result = adbdevice.run_adb(
            adb, ('exec-out', 'sh', '-c', remove_command(batch)), env)
        if not result.returncode:
            continue
        # Narrow a batch failure down to the individual paths.
        for path in batch:
            single = adbdevice.run_adb(
                adb, ('exec-out', 'sh', '-c', remove_command([path])), env)
            if single.returncode:
                detail = single.stderr.decode('utf-8', 'replace').strip()
                failed.append((path, detail or f'exit {single.returncode}'))
    for batch in chunked(directories):
        result = adbdevice.run_adb(
            adb, ('exec-out', 'sh', '-c', rmdir_command(batch)), env)
        if not result.returncode:
            continue
        for path in batch:
            single = adbdevice.run_adb(
                adb, ('exec-out', 'sh', '-c', rmdir_command([path])), env)
            if single.returncode:
                detail = single.stderr.decode('utf-8', 'replace').strip()
                kept_dirs.append((path, detail or f'exit {single.returncode}'))
    for path, detail in failed:
        warn(i18n.t('prune.warn.delete_failed', path=path, err=detail))
    for path, detail in kept_dirs:
        if log_level in ('debug', 'trace'):
            warn(i18n.t('prune.warn.kept_dir', path=path))
    info(i18n.t('prune.info.deleted',
                count=len(to_delete) - len(failed) - len(kept_dirs),
                dirs=len(directories) - len(kept_dirs), kept=kept))
