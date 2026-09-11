#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bootstrap the Android arm64 Python interpreter for ``source_mode: device-python``.

The Android interpreter is not shipped with the repository.  It is referenced
from astral-sh/python-build-standalone releases and, when requested, downloaded
into a persistent cache (or a user-chosen path) and unpacked there.  Subsequent
runs reuse the cache without touching the network.

No third-party Python package is required:

* Python 3.14+ decompresses ``.tar.zst`` with the standard-library
  ``compression.zstd`` and unpacks with ``tarfile``;
* otherwise an external ``zstd`` command is used when present;
* otherwise the OS ``tar`` is used (Windows ``bsdtar`` understands zstd);
* otherwise a clear error explains the missing tool.

Downloads use the standard-library ``urllib``; a plain local file path or a
``file://`` URL can be used instead of a network fetch (e.g. an archive the
user already downloaded).
"""
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

import i18n

# Pinned upstream build: python-build-standalone release 20260901, arm64
# (aarch64) Linux musl, fully static (includes the whole standard library and
# statically links extension modules, so only bin/ + lib/ are needed on device).
RELEASE = '20260901'
VARIANT = 'cpython-3.14.7+20260901-aarch64-unknown-linux-musl-lto+static-full'
DEFAULT_URL = ('https://github.com/astral-sh/python-build-standalone/releases/'
               'download/%s/%s.tar.zst' % (RELEASE, VARIANT))

_ENV_SUBDIR = 'andbackup'
_DOWNLOAD_SUBDIR = 'downloads'


def _log(message):
    sys.stderr.write(message + '\n')
    sys.stderr.flush()


def cache_root():
    """A persistent per-user cache directory appropriate for the platform."""
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    else:
        base = (os.environ.get('XDG_CACHE_HOME')
                or os.path.join(os.path.expanduser('~'), '.cache'))
    return os.path.join(base, _ENV_SUBDIR)


def default_prefix_dir():
    """Default extracted prefix location under the persistent cache."""
    return os.path.join(cache_root(), VARIANT)


def _is_prefix(path):
    """True if ``path`` looks like an extracted python prefix (bin/python*)."""
    if not os.path.isdir(path):
        return False
    bin_dir = os.path.join(path, 'bin')
    if not os.path.isdir(bin_dir):
        return False
    try:
        names = os.listdir(bin_dir)
    except OSError:
        return False
    return any(name == 'python' or name.startswith('python3') for name in names)


def _as_local_path(url):
    """Return a local file path when ``url`` refers to one, else None."""
    if os.path.isfile(url):
        return url
    if url.startswith('file://'):
        path = url[len('file://'):]
        if path.startswith('/') and len(path) > 2 and path[2] == ':':
            path = path[1:]          # file:///C:/... -> C:/...
        return path if os.path.isfile(path) else None
    return None


def _download(url, dest):
    """Fetch ``url`` into ``dest``; a local path/file:// URL is copied."""
    local = _as_local_path(url)
    if local is not None:
        shutil.copyfile(local, dest)
        return
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with open(dest, 'wb') as out:
                shutil.copyfileobj(response, out)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        raise RuntimeError(i18n.t('py.err.download', url=url, err=e)) from e
    except OSError as e:
        raise RuntimeError(i18n.t('py.err.write_download', path=dest,
                                  err=e)) from e


_PY_LAYOUT = None  # cached (root, interpreter, minor) of the current archive


def _which_zstd():
    """Return 'stdlib', 'external', or None for a usable zstd decompressor."""
    try:
        from compression import zstd          # Python 3.14+
        return 'stdlib'
    except ImportError:
        pass
    return 'external' if shutil.which('zstd') else None


def _decompress_zst_to_tar(zst_path, tar_path):
    """Write the decompressed tar of ``zst_path`` into ``tar_path``."""
    backend = _which_zstd()
    if backend is None:
        raise RuntimeError(i18n.t('py.err.no_zstd'))
    with open(zst_path, 'rb') as fin, open(tar_path, 'wb') as fout:
        if backend == 'stdlib':
            from compression import zstd
            zf = zstd.ZstdFile(fin, 'rb')
            try:
                shutil.copyfileobj(zf, fout)
            finally:
                zf.close()
        else:
            proc = subprocess.run([shutil.which('zstd'), '-d', '-c'],
                                  stdin=fin, stdout=fout,
                                  stderr=subprocess.PIPE)
            if proc.returncode:
                detail = proc.stderr.decode('utf-8', 'replace').strip()
                raise RuntimeError(
                    i18n.t('py.err.zstd_failed_detail', detail=detail)
                    if detail else i18n.t('py.err.zstd_failed'))
    return backend


def _interpreter_minor(name):
    """Return a truthy suffix for a real ``python3.<digits>`` interpreter.

    ``'python3.14'`` -> ``'14'``; ``'python3'`` -> ``''`` (only a fallback);
    anything else (e.g. the ``python`` alias) -> ``None``.
    """
    if name == 'python3':
        return ''
    if name.startswith('python3.'):
        tail = name[len('python3.'):]
        if tail.isdigit():
            return tail
    return None


def _scan_tar_layout(tf):
    """Return (root, interpreter_name) of the archive.

    ``root`` is the tar directory that directly contains ``bin/`` ('' when the
    entries are at the archive root); ``interpreter_name`` is the real
    ``bin/python3.x`` file (symlink aliases ``python``/``python3`` ignored).
    """
    best = None
    for member in tf:
        name = member.name
        parts = name.split('/')
        if len(parts) < 2 or parts[-2] != 'bin':
            continue
        base = parts[-1]
        minor = _interpreter_minor(base)
        if minor is None:
            continue
        if member.issym():
            continue
        root = '/'.join(parts[:-2])
        # Prefer the most specific python3.<digits> interpreter.
        key = (bool(minor), len(base), name)
        if best is None or key > best[0]:
            best = (key, root, base)
    if best is None:
        return None, None
    return best[1], best[2]


def _tarfile_extract_needed(tar_path, stage):
    """Extract only the interpreter + its stdlib from a plain tar into stage."""
    with tarfile.open(tar_path, 'r') as tf:
        root, interp = _scan_tar_layout(tf)
    if root is None:
        raise RuntimeError(i18n.t('py.err.interpreter_missing'))
    minor = interp[len('python3'):]          # '.14' ('' for a bare python3)
    prefix_mark = root + '/' if root else ''
    lib_mark = prefix_mark + 'lib/python3' + minor + '/'
    interp_mark = prefix_mark + 'bin/' + interp

    def wanted(member):
        name = member.name
        if member.issym() or member.islnk() or not (member.isfile()
                                                    or member.isdir()):
            return False
        return name == interp_mark or name.startswith(lib_mark)

    with tarfile.open(tar_path, 'r') as tf:
        if hasattr(tarfile, 'fully_trusted_filter'):
            filt = tarfile.fully_trusted_filter
        else:
            filt = None
        for member in tf:
            if not wanted(member):
                continue
            if member.isdir():
                continue          # tarfile creates parents automatically
            target = os.path.join(stage, *member.name.split('/'))
            parent = os.path.dirname(target)
            os.makedirs(parent, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                raise RuntimeError(
                    i18n.t('py.err.entry_unreadable', name=member.name))
            with open(target, 'wb') as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, member.mode & 0o777)


def _bsdtar_extract_needed(zst_path, stage):
    """Windows fallback: bsdtar extracts only the interpreter + stdlib subtree.

    python-build-standalone archives contain ``share/terminfo`` trees and
    symlinks (``bin/python``, ``lib/libpython3.14.a``) that Windows bsdtar
    cannot create; restricting to the real interpreter and its ``lib/python3.x``
    stdlib avoids both.
    """
    tar = shutil.which('tar')
    if tar is None:
        raise RuntimeError(i18n.t('py.err.no_zstd_or_bsdtar'))
    listing = subprocess.run([tar, '-tf', zst_path],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if listing.returncode:
        raise RuntimeError(i18n.t(
            'py.err.tar_read_failed',
            detail=listing.stderr.decode('utf-8', 'replace').strip()))
    root, interp = None, None
    fallback_root = fallback_interp = None
    for line in listing.stdout.decode('utf-8', 'replace').splitlines():
        name = line.strip()
        parts = name.split('/')
        if len(parts) < 2 or parts[-2] != 'bin':
            continue
        minor = _interpreter_minor(parts[-1])
        if minor is None:
            continue
        candidate_root = '/'.join(parts[:-2])
        if minor:
            # python3.<digits> is the real interpreter; aliases python/python3
            # are symlinks that Windows cannot create.
            if interp is None:
                root, interp = candidate_root, parts[-1]
        elif fallback_interp is None:
            fallback_root, fallback_interp = candidate_root, parts[-1]
    if interp is None:
        root, interp = fallback_root, fallback_interp
    if root is None or interp is None:
        raise RuntimeError(i18n.t('py.err.interpreter_missing'))
    minor = interp[len('python3'):]
    root_arg = root + '/' if root else ''
    proc = subprocess.run(
        [tar, '-xf', zst_path, '-C', stage,
         '--include', root_arg + 'bin/' + interp,
         '--include', root_arg + 'lib/python3' + minor],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        detail = proc.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError(
            i18n.t('py.err.tar_extract_failed_detail', detail=detail)
            if detail else i18n.t('py.err.tar_extract_failed'))
    return root, interp


def _locate_prefix(stage):
    """Return the extracted directory that directly holds ``bin/python*``."""
    if _is_prefix(stage):
        return stage
    frontier = [stage]
    for _ in range(6):
        next_frontier = []
        for directory in frontier:
            try:
                entries = os.listdir(directory)
            except OSError:
                continue
            for name in entries:
                candidate = os.path.join(directory, name)
                if os.path.isdir(candidate):
                    if _is_prefix(candidate):
                        return candidate
                    next_frontier.append(candidate)
        frontier = next_frontier
    return None


def _extract_tar_zst(zst_path, prefix):
    """Unpack the needed interpreter subtree into a new prefix, atomically."""
    parent = os.path.dirname(prefix) or os.curdir
    os.makedirs(parent, exist_ok=True)
    stage = tempfile.mkdtemp(
        prefix=os.path.basename(prefix) + '.partial.', dir=parent)
    tmp_tar = None
    try:
        backend = _which_zstd()
        if os.name == 'nt' and backend is None:
            # Windows, Python <3.14, no external zstd: bsdtar handles zstd.
            _bsdtar_extract_needed(zst_path, stage)
            method = 'OS tar'
        else:
            if backend is None:
                raise RuntimeError(i18n.t('py.err.no_zstd_or_tar'))
            fd, tmp_tar = tempfile.mkstemp(
                prefix='andbackup-python-', suffix='.tar', dir=parent)
            os.close(fd)
            method = _decompress_zst_to_tar(zst_path, tmp_tar)
            _tarfile_extract_needed(tmp_tar, stage)
        located = _locate_prefix(stage)
        if located is None:
            raise RuntimeError(i18n.t('py.err.invalid_prefix'))
        os.replace(located, prefix)
        if os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)
        return method
    finally:
        if tmp_tar is not None:
            try:
                os.unlink(tmp_tar)
            except OSError:
                pass
        if os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)


def _bootstrap(url, prefix, quiet):
    """Download (once) and unpack the interpreter into ``prefix``."""
    downloads = os.path.join(cache_root(), _DOWNLOAD_SUBDIR)
    os.makedirs(downloads, exist_ok=True)
    local = _as_local_path(url)
    if local is not None:
        filename = os.path.basename(local)
    else:
        filename = url.rstrip('/').rsplit('/', 1)[-1]
    if not filename:
        filename = VARIANT + '.tar.zst'
    zst_path = os.path.join(downloads, filename)
    tag = i18n.tag('download')
    if not os.path.isfile(zst_path):
        if not quiet:
            _log(tag + ' ' + i18n.t('py.log.fetching', url=url))
        _download(url, zst_path)
    else:
        if not quiet:
            _log(tag + ' ' + i18n.t('py.log.cached_archive', path=zst_path))
    if not quiet:
        _log(tag + ' ' + i18n.t('py.log.unpacking_to', path=prefix))
    method = _extract_tar_zst(zst_path, prefix)
    if not quiet:
        _log(tag + ' ' + i18n.t('py.log.done', method=method, path=prefix))


def resolve(device_python='', allow_download=False, url='', quiet=False):
    """Return an existing Android interpreter path for ``device-python``.

    * ``device_python`` pointing at an existing file or prefix dir is returned
      unchanged (never downloaded).
    * Otherwise, when ``allow_download`` is true, the pinned upstream build is
      fetched into ``device_python`` (a target directory) or, when empty, into
      the persistent cache, unpacked, and returned.
    * Otherwise a clear error explains what to configure.
    """
    url = (url or DEFAULT_URL).strip()
    target = (device_python or '').strip()
    if target:
        path = os.path.abspath(os.path.expanduser(target))
        if os.path.exists(path):
            return path
        if not allow_download:
            raise RuntimeError(
                i18n.t('py.err.device_python_missing', path=path) + '\n'
                + i18n.t('py.hint.point_or_download'))
        prefix = path
    else:
        # The persistent cache is used only when the user asked for the
        # on-demand download (download_device_python: true). Otherwise an
        # explicit DEVICE_PYTHON is required so a missing interpreter is a
        # deterministic error instead of depending on the machine cache.
        if not allow_download:
            raise RuntimeError(
                i18n.t('py.err.mode_needs_python') + '\n'
                + i18n.t('py.hint.set_device_python') + '\n'
                + i18n.t('py.hint.enable_download'))
        prefix = default_prefix_dir()
        if _is_prefix(prefix):
            return prefix
    if _is_prefix(prefix):
        return prefix
    _bootstrap(url, prefix, quiet)
    if not _is_prefix(prefix):
        raise RuntimeError(i18n.t('py.err.no_prefix', prefix=prefix))
    return prefix
