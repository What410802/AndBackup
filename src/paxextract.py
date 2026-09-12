#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extraction half of the PAXCK tool set.

Two modes live here, deliberately kept apart from the writer:

* ``cmd_extract`` — the backup-recovery path: every regular file is checked
  against its PAX ``PAXCK.checksum.sha256`` record while it is written into a
  private staging directory, and the staging directory is renamed onto the
  destination only when the whole archive validated.  The destination must not
  exist yet.
* ``cmd_extract_direct`` — the interoperability path for trusted third-party
  archives: tarfile's traditional direct-write semantics, no PAX validation and
  no atomicity.

The command line entry point lives in ``paxck.py`` (``paxck.py extract``), which
imports this module lazily so the archive writer stays independent of it.
"""
import hashlib
import lzma
import os
import shutil
import sys
import tarfile
import tempfile

import i18n
from paxck import (CHUNK, PAX_KEY, _bin_in, _drain_archive_stream,
                   open_archive_stream)


class _UnsafeArchive(ValueError):
    """The archive asks extraction to leave its destination directory."""


class _ArchiveInputError(OSError):
    """The archive path could not be opened before extraction began."""


def _member_parts(name):
    """Return a safe, portable relative path split into native components."""
    if not isinstance(name, str) or not name:
        raise _UnsafeArchive(i18n.t('paxck.extract.empty_path'))
    if '\0' in name:
        raise _UnsafeArchive(i18n.t('paxck.extract.nul_path', name=name))
    if name.startswith(('/', '\\')) or '\\' in name:
        raise _UnsafeArchive(i18n.t('paxck.extract.unsafe_path', name=name))
    parts = name.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise _UnsafeArchive(i18n.t('paxck.extract.dot_path', name=name))
    if os.name == 'nt' and any(':' in part for part in parts):
        raise _UnsafeArchive(i18n.t('paxck.extract.drive_path', name=name))
    return parts


def _member_path(stage, parts):
    path = os.path.join(stage, *parts)
    stage_norm = os.path.normcase(os.path.abspath(stage))
    path_norm = os.path.normcase(os.path.abspath(path))
    if os.path.commonpath((stage_norm, path_norm)) != stage_norm:
        raise _UnsafeArchive(i18n.t('paxck.extract.escape'))
    return path


def _require_directory_parents(stage, parts):
    current = stage
    for part in parts[:-1]:
        current = os.path.join(current, part)
        if os.path.islink(current) or not os.path.isdir(current):
            raise _UnsafeArchive(i18n.t('paxck.extract.bad_parent',
                                        path=repr('/'.join(parts))))


def _restore_metadata(path, member, follow_symlinks=True):
    """Restore portable mode/mtime fields without requiring elevated rights."""
    warn_tag = i18n.tag('warn')
    if follow_symlinks:
        try:
            os.chmod(path, member.mode)
        except OSError as e:
            sys.stderr.write(warn_tag + ' '
                             + i18n.t('paxck.warn.chmod_failed', name=member.name,
                                      err=i18n.os_error(e)) + '\n')
    try:
        os.utime(path, (member.mtime, member.mtime),
                 follow_symlinks=follow_symlinks)
    except (NotImplementedError, OSError) as e:
        sys.stderr.write(warn_tag + ' '
                         + i18n.t('paxck.warn.utime_failed', name=member.name,
                                  err=i18n.os_error(e)) + '\n')


def _copy_verified_member(tf, member, destination):
    headers = getattr(member, 'pax_headers', None) or {}
    expected = headers.get(PAX_KEY)
    if not expected:
        raise _UnsafeArchive(i18n.t('paxck.extract.missing_checksum',
                                    name=member.name, key=PAX_KEY))
    source = tf.extractfile(member)
    if source is None:
        raise OSError(i18n.t('paxck.extract.no_content', name=member.name))

    digest = hashlib.sha256()
    size = 0
    with open(destination, 'xb') as target:
        while True:
            chunk = source.read(CHUNK)
            if not chunk:
                break
            target.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size != member.size:
        raise OSError(i18n.t('paxck.extract.length_mismatch', name=member.name,
                             size=size, expected=member.size))
    actual = digest.hexdigest()
    if actual != expected:
        raise _UnsafeArchive(i18n.t('paxck.extract.mismatch', name=member.name,
                                    expected=expected[:16],
                                    actual=actual[:16]))


def _extract_to_stage(infile, stage):
    """Extract one verified archive into a private, empty staging directory."""
    src = None
    stream = None
    tf = None
    total = 0
    seen = set()
    deferred_hardlinks = []
    deferred_symlinks = []
    directories = []
    try:
        if infile:
            try:
                src = open(infile, 'rb')
            except OSError as e:
                raise _ArchiveInputError(
                    i18n.t('paxck.err.archive_unreadable', path=infile,
                           err=i18n.os_error(e))) from e
        else:
            src = _bin_in()
        stream = open_archive_stream(src)
        tf = tarfile.open(fileobj=stream, mode='r|')

        for member in tf:
            total += 1
            parts = _member_parts(member.name)
            canonical = '/'.join(parts)
            if canonical in seen:
                raise _UnsafeArchive(
                    i18n.t('paxck.extract.duplicate', name=member.name))
            seen.add(canonical)
            _require_directory_parents(stage, parts)
            destination = _member_path(stage, parts)

            if member.isdir():
                os.mkdir(destination)
                directories.append((destination, member))
                continue

            if member.isfile():
                _copy_verified_member(tf, member, destination)
                _restore_metadata(destination, member)
                continue

            if member.islnk():
                target_parts = _member_parts(member.linkname)
                deferred_hardlinks.append((destination, member, target_parts))
                continue

            if member.issym():
                if '\0' in member.linkname:
                    raise _UnsafeArchive(
                        i18n.t('paxck.extract.link_nul', name=member.name))
                deferred_symlinks.append((destination, member, parts))
                continue

            raise _UnsafeArchive(
                i18n.t('paxck.extract.unsupported_type', name=member.name,
                       type=member.type))

        if total == 0:
            raise _UnsafeArchive(i18n.t('paxck.extract.empty'))

        # All regular files and directories exist before links. This prevents a
        # symlink from becoming a parent used by a later extraction operation.
        for destination, member, target_parts in deferred_hardlinks:
            _require_directory_parents(stage, _member_parts(member.name))
            target = _member_path(stage, target_parts)
            if os.path.islink(target) or not os.path.isfile(target):
                raise _UnsafeArchive(
                    i18n.t('paxck.extract.bad_hardlink', name=member.name,
                           target=repr(member.linkname)))
            os.link(target, destination)
            _restore_metadata(destination, member)

        for destination, member, parts in deferred_symlinks:
            _require_directory_parents(stage, parts)
            target_is_directory = os.path.isdir(
                os.path.join(os.path.dirname(destination), member.linkname))
            os.symlink(member.linkname, destination,
                       target_is_directory=target_is_directory)
            _restore_metadata(destination, member, follow_symlinks=False)

        # Creating children changes directory timestamps, so restore them last.
        for destination, member in reversed(directories):
            _restore_metadata(destination, member)
        _drain_archive_stream(stream)
    finally:
        if tf is not None:
            try:
                tf.close()
            except Exception:
                pass
        if stream is not None and stream is not src:
            try:
                stream.close()
            except Exception:
                pass
        if src is not None and src is not _bin_in():
            try:
                src.close()
            except Exception:
                pass


def cmd_extract(infile, directory):
    """Safely extract a PAXCK archive into a new directory, atomically."""
    destination = os.path.abspath(directory)
    parent = os.path.dirname(destination) or os.curdir
    fail_tag = i18n.tag('fail')
    if os.path.lexists(destination):
        sys.stderr.write(fail_tag + ' '
                         + i18n.t('paxck.extract.exists', path=destination)
                         + '\n')
        return 1
    if not os.path.isdir(parent):
        sys.stderr.write(fail_tag + ' '
                         + i18n.t('paxck.extract.no_parent', path=parent) + '\n')
        return 1

    stage = None
    try:
        stage = tempfile.mkdtemp(
            prefix=os.path.basename(destination) + '.partial.', dir=parent)
        _extract_to_stage(infile, stage)
        os.replace(stage, destination)
        stage = None
        print(i18n.tag('done') + ' '
              + i18n.t('paxck.done.extracted', path=directory))
        return 0
    except _UnsafeArchive as e:
        sys.stderr.write(fail_tag + ' '
                         + i18n.t('paxck.extract.refused', err=e) + '\n')
        return 1
    except _ArchiveInputError as e:
        sys.stderr.write(fail_tag + ' '
                         + i18n.t('paxck.extract.input_failed', err=e) + '\n')
        return 1
    except (lzma.LZMAError, tarfile.TarError, EOFError) as e:
        sys.stderr.write(fail_tag + ' '
                         + i18n.t('paxck.extract.damaged', err=e) + '\n')
        return 1
    except OSError as e:
        sys.stderr.write(fail_tag + ' '
                         + i18n.t('paxck.extract.write_failed',
                                  err=i18n.os_error(e)) + '\n')
        return 3
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def cmd_extract_direct(infile, directory):
    """Delegate extraction to tarfile without PAXCK validation or staging.

    This intentionally has tarfile's direct-write semantics: ``directory`` may
    already exist and a failure may leave files behind.  It is for trusted
    archives and interoperability only; the default ``extract`` path above is
    the backup-recovery path.
    """
    destination = os.path.abspath(directory)
    fail_tag = i18n.tag('fail')
    if os.path.lexists(destination) and not os.path.isdir(destination):
        sys.stderr.write(fail_tag + ' '
                         + i18n.t('paxck.direct.not_a_dir', path=destination)
                         + '\n')
        return 1

    try:
        os.makedirs(destination, exist_ok=True)
    except OSError as e:
        sys.stderr.write(fail_tag + ' '
                         + i18n.t('paxck.direct.mkdir_failed',
                                  err=i18n.os_error(e)) + '\n')
        return 3

    src = None
    stream = None
    tf = None
    try:
        if infile:
            try:
                src = open(infile, 'rb')
            except OSError as e:
                sys.stderr.write(
                    fail_tag + ' '
                    + i18n.t('paxck.err.archive_unreadable', path=infile,
                             err=i18n.os_error(e)) + '\n')
                return 1
        else:
            src = _bin_in()

        try:
            stream = open_archive_stream(src)
            tf = tarfile.open(fileobj=stream, mode='r|')
            # Python 3.12+ changed extraction-filter defaults.  Direct mode is
            # explicitly requested for trusted archives, so preserve tarfile's
            # traditional unrestricted extraction semantics on every version.
            if hasattr(tarfile, 'fully_trusted_filter'):
                tf.extractall(destination, filter='fully_trusted')
            else:
                tf.extractall(destination)
            _drain_archive_stream(stream)
        except (lzma.LZMAError, tarfile.TarError, EOFError) as e:
            sys.stderr.write(fail_tag + ' '
                             + i18n.t('paxck.direct.damaged', err=e) + '\n')
            return 1
        except OSError as e:
            sys.stderr.write(fail_tag + ' '
                             + i18n.t('paxck.direct.failed',
                                      err=i18n.os_error(e)) + '\n')
            return 3
    finally:
        if tf is not None:
            try:
                tf.close()
            except Exception:
                pass
        if stream is not None and stream is not src:
            try:
                stream.close()
            except Exception:
                pass
        if src is not None and src is not _bin_in():
            try:
                src.close()
            except Exception:
                pass

    print(i18n.tag('done') + ' '
          + i18n.t('paxck.done.direct_extracted', path=directory))
    return 0
