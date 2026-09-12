#!/usr/bin/env python3
"""CMD integration-test substitute for the limited adb exec-out protocol."""
import os
import posixpath
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import re



def _unwrap_protocol(command):
    """Run the command part used by adb_source's binary status protocol."""
    marker = ' 2>/dev/null; __andbackup_rc=$?; printf '
    if marker not in command:
        return command, False
    return command.split(marker, 1)[0], True


def _write_status(rc):
    sys.stdout.buffer.write(b'\0__ANDBACKUP_RC__' + str(rc).encode('ascii') + b'\0')


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


def _remote_path(device_path):
    root = os.environ.get('FAKE_ADB_REMOTE_ROOT')
    if not root:
        raise ValueError('FAKE_ADB_REMOTE_ROOT is not configured')
    path = device_path.replace('/', os.sep).lstrip(os.sep)
    return os.path.join(root, *path.split(os.sep))


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


def _walk(source):
    """Yield the device paths `find` would visit, root first."""
    root = _local_path(source)
    if not os.path.isdir(root):
        raise FileNotFoundError(source)
    yield source
    for directory, directories, filenames in os.walk(root, followlinks=False):
        directories.sort()
        filenames.sort()
        for name in directories + filenames:
            yield _device_path(source, root, os.path.join(directory, name))


def _stat_text(device_path, fmt):
    """The `stat -c` text for one entry, without the trailing path field."""
    item = os.lstat(_local_path(device_path))
    seconds, nanos = divmod(item.st_mtime_ns, 1000000000)
    human = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(seconds))
    text = '%x|%d|%d|%s.%09d +0000|%o' % (
        item.st_mode, item.st_size, seconds, human, nanos,
        stat.S_IMODE(item.st_mode))
    if '%u' in fmt:
        # Mirrors toybox `%u|%g`; Windows reports 0 for both.
        text += '|%d|%d' % (item.st_uid, item.st_gid)
    return text


def _find_exec(source, words):
    """Emulate `find ROOT -exec ... {} +` (the one-shot tree listing)."""
    if os.environ.get('FAKE_ADB_NO_ONESHOT'):
        raise ValueError('this fake ROM cannot run find -exec')
    if '-type' in words and words[words.index('-type') + 1] == 'l':
        # find ROOT -type l -exec sh -c SCRIPT sh {} +
        for path in _walk(source):
            local = _local_path(path)
            if os.path.islink(local):
                target = os.readlink(local)
                sys.stdout.buffer.write(
                    path.encode('utf-8', 'surrogateescape') + b'\0'
                    + target.encode('utf-8', 'surrogateescape') + b'\0')
        return 0
    fmt = words[words.index('-c') + 1]
    for path in _walk(source):
        try:
            text = _stat_text(path, fmt)
        except OSError:
            continue
        sys.stdout.buffer.write(
            (text + '|' + path).encode('utf-8', 'surrogateescape') + b'\n')
    return 0


def _stat(device_path, fmt):
    sys.stdout.buffer.write((_stat_text(device_path, fmt) + '\n').encode('ascii'))


def _cat(device_path):
    limit = os.environ.get('FAKE_ADB_TRUNCATE')
    remaining = int(limit) if limit else None
    path = _local_path(device_path) if device_path.startswith(os.environ['FAKE_ADB_SOURCE']) else _remote_path(device_path)
    with open(path, 'rb') as fh:
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


def _rm(device_paths):
    """Fake `rm -f --`: delete files and symlinks (never directories)."""
    missing = []
    for device_path in device_paths:
        path = _local_path(device_path)
        if os.path.isdir(path) and not os.path.islink(path):
            missing.append(device_path + ': Is a directory')
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            missing.append(device_path + ': No such file or directory')
    if missing:
        for item in missing:
            sys.stderr.write('rm: %s\n' % item)
        return 1
    return 0


def _rmdir(device_paths):
    """Fake `rmdir --`: remove empty directories only, like the real tool."""
    failed = []
    for device_path in device_paths:
        path = _local_path(device_path)
        try:
            os.rmdir(path)
        except OSError as exc:
            failed.append('%s: %s' % (device_path, exc.strerror or exc))
    if failed:
        for item in failed:
            sys.stderr.write('rmdir: %s\n' % item)
        return 1
    return 0


def main(args):
    _log(args)
    if os.environ.get('FAKE_ADB_FAIL'):
        return 1
    if args == ['devices']:
        sys.stdout.write('List of devices attached\n')
        listing = os.environ.get('FAKE_ADB_DEVICES', 'FAKE-1 device')
        for line in listing.splitlines():
            if line.strip():
                sys.stdout.write(line.rstrip() + '\n')
        return 0
    if args == ['get-state']:
        missing = os.environ.get('FAKE_ADB_DEVICE_NOT_FOUND')
        if missing and os.environ.get('ANDROID_SERIAL', '') == missing:
            sys.stderr.write('error: device %r not found\n' % missing)
            return 1
        sys.stdout.write('device\n')
        return 0
    if len(args) == 2 and args[0] == 'connect':
        sys.stdout.write('connected to ' + args[1] + '\n')
        return 0
    if args[:1] == ['push'] and len(args) == 3:
        src, dst = args[1:]
        os.makedirs(os.path.dirname(_remote_path(dst)), exist_ok=True)
        shutil.copyfile(src, _remote_path(dst))
        return 0
    if args[:1] == ['shell'] and len(args) >= 2:
        command = args[1:]
        if command[:2] == ['mkdir', '-p'] and len(command) == 3:
            os.makedirs(_remote_path(command[2]), exist_ok=True)
            return 0
        if command[:2] == ['chmod', '700'] and len(command) == 3:
            os.chmod(_remote_path(command[2]), 0o700)
            return 0
        if command[:2] == ['rm', '-rf'] and len(command) == 3:
            shutil.rmtree(_remote_path(command[2]), ignore_errors=True)
            return 0
        # device-python prefix mode: tar -xf <remote.tar> -C <remote_dir>
        if command[0] == 'tar' and command[1] == '-xf' and len(command) == 5 \
                and command[3] == '-C':
            src = _remote_path(command[2])
            dst = _remote_path(command[4])
            os.makedirs(dst, exist_ok=True)
            with open(src, 'rb') as fh:
                import tarfile
                with tarfile.open(fileobj=fh, mode='r:') as tf:
                    if hasattr(tarfile, 'fully_trusted_filter'):
                        tf.extractall(dst, filter='fully_trusted')
                    else:
                        tf.extractall(dst)
            return 0
        sys.stderr.write('fake adb: unsupported shell command: %r\n' % (command,))
        return 1
    if args[:2] == ['exec-out', 'cat'] and len(args) == 3:
        try:
            with open(_remote_path(args[2]), 'rb') as fh:
                shutil.copyfileobj(fh, sys.stdout.buffer)
            return 0
        except OSError as exc:
            sys.stderr.write('fake adb: %s\n' % exc)
            return 1
    if len(args) != 4 or args[:3] != ['exec-out', 'sh', '-c']:
        sys.stderr.write('fake adb: unsupported arguments: %r\n' % (args,))
        return 1
    try:
        raw_command = args[3]
        # Device-Python mode runs the uploaded paxck script. Simulate it with
        # the host interpreter against the fake device tree while preserving
        # tar bytes on stdout and redirecting diagnostics to the remote file.
        if ' create ' in raw_command and '/paxck.py' in raw_command:
            tokens = shlex.split(raw_command, posix=True)
            source_index = tokens.index('create') + 1
            source = tokens[source_index]
            remote_error = re.search(r'2>([^; ]+)', raw_command).group(1)
            remote_status = re.search(r'>/data/local/tmp/[^; ]+/status', raw_command).group(0)[1:]
            local_source = _local_path(source)
            command = [sys.executable,
                       os.path.join(os.path.dirname(__file__), '..', 'src',
                                    'paxck.py'), 'create', local_source]
            manifest_local = None
            if '--packed-manifest' in tokens:
                remote_manifest = tokens[tokens.index('--packed-manifest') + 1]
                fd, manifest_local = tempfile.mkstemp(suffix='.manifest')
                os.close(fd)
                command += ['--packed-manifest', manifest_local]
            if os.environ.get('FAKE_ADB_DEVICE_PYTHON_FAIL'):
                result = subprocess.CompletedProcess([], 7, b'device python failed\n')
            else:
                result = subprocess.run(
                    command, stdout=sys.stdout.buffer, stderr=subprocess.PIPE,
                    check=False)
            if manifest_local:
                # The device-side packer sees device paths only: translate the
                # host-side records back before publishing the remote manifest.
                with open(manifest_local, 'rb') as fh:
                    blob = fh.read()
                os.unlink(manifest_local)
                out = b''
                for record in blob.split(b'\0'):
                    if len(record) < 2 or record[1:2] != b':':
                        continue
                    status, value = record[:1], record[2:].decode(
                        'utf-8', 'surrogateescape')
                    if status in (b'P', b'D', b'S'):
                        value = _device_path(source, local_source, value)
                    out += (status + b':' +
                            value.encode('utf-8', 'surrogateescape') + b'\0')
                os.makedirs(os.path.dirname(_remote_path(remote_manifest)),
                            exist_ok=True)
                with open(_remote_path(remote_manifest), 'wb') as fh:
                    fh.write(out)
            os.makedirs(os.path.dirname(_remote_path(remote_error)), exist_ok=True)
            with open(_remote_path(remote_error), 'wb') as fh:
                fh.write(result.stderr)
            with open(_remote_path(remote_status), 'w', encoding='ascii') as fh:
                fh.write(str(result.returncode))
            return result.returncode
        command, wrapped = _unwrap_protocol(raw_command)
        words = shlex.split(command, posix=True)
        if words[:1] == ['find'] and words[-1:] == ['-print0']:
            _find(words[1])
        elif words[:1] == ['find'] and '-exec' in words:
            _find_exec(words[1], words)
        elif words[:2] == ['stat', '-c'] and len(words) == 5 and words[3] == '--':
            _stat(words[4], words[2])
        elif words[:2] == ['readlink', '-n'] and len(words) == 4 and words[2] == '--':
            sys.stdout.buffer.write(os.readlink(_local_path(words[3])).encode(
                'utf-8', 'surrogateescape'))
        elif words[:1] == ['cat'] and len(words) == 3 and words[1] == '--':
            _cat(words[2])
        elif words[:1] == ['rm'] and len(words) >= 3 and words[1:3] == ['-f', '--']:
            if _rm(words[3:]):
                raise ValueError('rm failed')
        elif words[:1] == ['rmdir'] and len(words) >= 2 and words[1] == '--':
            if _rmdir(words[2:]):
                raise ValueError('rmdir failed')
        elif words[1:] == ['--version'] and words[0].startswith(
                '/data/local/tmp/andbackup-pyenv'):
            # Device-python self-test: the cached interpreter reports a version.
            sys.stdout.write('Python 3.14.7\n')
        else:
            raise ValueError('unsupported shell command: %r' % (command,))
    except (OSError, ValueError, IndexError) as exc:
        if wrapped:
            _write_status(1)
            return 0
        sys.stderr.write('fake adb: %s\n' % (exc,))
        return 1
    if wrapped:
        _write_status(0)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
