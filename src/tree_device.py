#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Device-side tree lister for ``backup.py tree --tree-mode device-python``.

Uploaded next to ``paxck.py``/``i18n.py`` by ``device-python`` mode.  It walks
the source directory with ``os.scandir`` + ``os.lstat`` (no shell, no per-entry
process), keeps the whole listing in memory, and then writes it out in **one
go** -- a single flush instead of one adb round trip per entry.

Record framing (all NUL-terminated, so any byte in a name is safe):

* ``\\x01P<count>``   progress: ``count`` entries listed so far (the script
  supervises its own walk, so the host can show progress even though nothing
  reaches it until the end);
* ``\\x02U<path>``    the entry could not be ``lstat``-ed (permission, I/O);
* ``\\x03L<target>``  symlink target of the entry whose two records came just
  before;
* ``<fields>``        metadata of one entry, followed by its own ``<path>``
  record; unlike the shell one-shot this cannot be confused by a newline or a
  ``|`` inside a name.

``<fields>`` is ``mode_hex|size|mtime_epoch|mtime_human|perm_octal|uid|gid`` --
the same layout ``stat -c '%f|%s|%Y|%y|%a|%u|%g'`` produces, so the host
parses both strategies with one parser.
"""
import os
import stat as stat_module
import sys
import time

PROGRESS_EVERY = 200
PROGRESS_MARK = b'\x01P'
UNREADABLE_MARK = b'\x02U'
LINK_MARK = b'\x03L'


def _encode(text):
    return text.encode('utf-8', 'surrogateescape')


def _fields(item, path):
    """The ``%f|%s|%Y|%y|%a|%u|%g`` equivalent of one ``os.lstat`` result."""
    seconds = int(item.st_mtime)
    human = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(item.st_mtime))
    fraction = item.st_mtime_ns - seconds * 1000000000
    return '%x|%d|%d|%s.%09d %s|%o|%d|%d' % (
        item.st_mode, item.st_size, seconds, human, fraction,
        time.strftime('%z', time.localtime(item.st_mtime)),
        stat_module.S_IMODE(item.st_mode), item.st_uid, item.st_gid)


def _walk(root):
    """Yield every path under ``root`` (root first), depth first, sorted.

    ``os.scandir`` gives the entries and their ``DirEntry`` metadata in one
    syscall, which is what makes this far cheaper than a shell loop.
    """
    stack = [root]
    while stack:
        path = stack.pop()
        yield path
        try:
            with os.scandir(path) as scan:
                children = sorted(entry.path for entry in scan)
        except OSError:
            # An unreadable directory: it stays in the listing as an entry,
            # its children simply cannot be enumerated.
            continue
        stack.extend(reversed(children))


def main(argv):
    if len(argv) < 2:
        sys.stderr.write('usage: tree_device.py DIRECTORY\n')
        return 2
    root = argv[1].rstrip('/') or '/'
    out = sys.stdout.buffer
    records = []
    count = 0
    for path in _walk(root):
        try:
            item = os.lstat(path)
        except OSError:
            records.append(UNREADABLE_MARK + _encode(path) + b'\0')
            count += 1
        else:
            records.append(_encode(_fields(item, path)) + b'\0'
                           + _encode(path) + b'\0')
            count += 1
            if stat_module.S_ISLNK(item.st_mode):
                try:
                    target = os.readlink(path)
                except OSError:
                    pass
                else:
                    records.append(LINK_MARK + _encode(target) + b'\0')
        if count % PROGRESS_EVERY == 0:
            # Self-reported progress; the host only shows it at its own pace.
            out.write(PROGRESS_MARK + str(count).encode('ascii') + b'\0')
            out.flush()
    # Everything is in memory by now: hand it over in one write.
    out.write(b''.join(records))
    out.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
