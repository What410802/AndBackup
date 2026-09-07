#!/usr/bin/env python3
"""CMD integration-test substitute for the limited adb exec-out protocol."""
import os
import posixpath
import shlex
import stat
import sys


def _log(args):
    path = os.environ.get('FAKE_ADB_LOG')
    if not path:
        return
    with open(path, 'a', encoding='utf-8', newline='') as fh:
        fh.write('serial=' + os.environ.get('ANDROID_SERIAL', '') + '\n')
        fh.write(' '.join(shlex.quote(arg) for arg in args) + '\n')


def _local_path(device_path):
    source = os.environ['FAKE_ADB_SOURCE'].rstrip('/') or '/'
    relative = posixpath.relpath(device_path, source)
    if relative == '.':
        return os.environ['FAKE_ADB_ROOT']
    if relative == '..' or relative.startswith('../'):
        raise ValueError('path is outside the fake Android source')
    return os.path.join(os.environ['FAKE_ADB_ROOT'], *relative.split('/'))


def _device_path(source, local_root, local_path):
    relative = os.path.relpath(local_path, local_root)
    if relative == '.':
        return source
    return source.rstrip('/') + '/' + relative.replace(os.sep, '/')


def _find(source):
    root = _local_path(source)
    if not os.path.isdir(root):
        raise FileNotFoundError(source)
    paths = [source]
    for directory, directories, filenames in os.walk(root, followlinks=False):
        directories.sort()
        filenames.sort()
        for name in directories + filenames:
            paths.append(_device_path(source, root, os.path.join(directory, name)))
    sys.stdout.buffer.write(
        b''.join(path.encode('utf-8', 'surrogateescape') + b'\0' for path in paths))


def _stat(device_path):
    item = os.lstat(_local_path(device_path))
    text = '%x|%d|%d|%o\n' % (
        item.st_mode, item.st_size, int(item.st_mtime), stat.S_IMODE(item.st_mode))
    sys.stdout.buffer.write(text.encode('ascii'))


def _cat(device_path):
    limit = os.environ.get('FAKE_ADB_TRUNCATE')
    remaining = int(limit) if limit else None
    with open(_local_path(device_path), 'rb') as fh:
        while True:
            size = 65536 if remaining is None else min(65536, remaining)
            if size == 0:
                return
            chunk = fh.read(size)
            if not chunk:
                return
            sys.stdout.buffer.write(chunk)
            if remaining is not None:
                remaining -= len(chunk)


def main(args):
    _log(args)
    if os.environ.get('FAKE_ADB_FAIL'):
        return 1
    if args == ['get-state']:
        sys.stdout.write('device\n')
        return 0
    if len(args) == 2 and args[0] == 'connect':
        sys.stdout.write('connected to ' + args[1] + '\n')
        return 0
    if len(args) != 4 or args[:3] != ['exec-out', 'sh', '-c']:
        sys.stderr.write('fake adb: unsupported arguments: %r\n' % (args,))
        return 1
    try:
        words = shlex.split(args[3], posix=True)
        if words[:1] == ['find'] and words[-1:] == ['-print0']:
            _find(words[1])
        elif words[:2] == ['stat', '-c'] and len(words) == 5 and words[3] == '--':
            _stat(words[4])
        elif words[:2] == ['readlink', '-n'] and len(words) == 4 and words[2] == '--':
            sys.stdout.buffer.write(os.readlink(_local_path(words[3])).encode(
                'utf-8', 'surrogateescape'))
        elif words[:1] == ['cat'] and len(words) == 3 and words[1] == '--':
            _cat(words[2])
        else:
            raise ValueError('unsupported shell command: %r' % (args[3],))
    except (OSError, ValueError, IndexError) as exc:
        sys.stderr.write('fake adb: %s\n' % (exc,))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
