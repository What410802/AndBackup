#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verification half of the PAXCK tool set.

``paxck.py`` writes archives whose regular files carry a
``PAXCK.checksum.sha256`` PAX record; this module re-reads such an archive and
checks every record.  It is a separate module so the writer stays independent:
``device-python`` mode uploads ``paxck.py`` (plus ``i18n.py``) to the device and
never needs the verifier there.

The command line entry point lives in ``paxck.py`` (``paxck.py verify``), which
imports this module lazily.
"""
import hashlib
import lzma
import sys
import tarfile

import i18n
from paxck import (INVENTORY_NAME, PAX_KEY, InventoryError, _bin_in,
                   _drain_archive_stream, compare_inventory,
                   member_identity, open_archive_stream, parse_inventory,
                   sha256_stream)


def _identity_text(identity):
    """Human-readable identity, for reporting a changed member."""
    kind, mode, size, mtime, uid, gid, linkname = identity
    return 'type %s, mode %o, size %d, mtime %.9f, uid %d, gid %d%s' % (
        kind, mode, size, mtime, uid, gid,
        (', link ' + linkname) if linkname else '')


def _identity_diff(listed, found):
    """Which identity fields differ, as ``name: old -> new`` fragments."""
    names = ('type', 'mode', 'size', 'mtime', 'uid', 'gid', 'link')
    parts = []
    for label, old, new in zip(names, listed, found):
        if old != new:
            parts.append('%s %s -> %s' % (label, old, new))
    return ', '.join(parts)


def _report_inventory(listed, found, failures, quiet):
    """Compare the archive against its inventory; append failures."""
    missing, extra, changed = compare_inventory(listed, found)
    for name in missing[:50]:
        failures.append(i18n.t('paxck.inventory.missing', name=name))
    for name in extra[:50]:
        failures.append(i18n.t('paxck.inventory.extra', name=name))
    for name, was, now in changed[:50]:
        failures.append(i18n.t('paxck.inventory.changed', name=name,
                               detail=_identity_diff(was, now)))
    if not quiet:
        sys.stderr.write(i18n.t(
            'paxck.inventory.summary', listed=len(listed),
            found=len(found), missing=len(missing), extra=len(extra),
            changed=len(changed)) + '\n')
    return len(missing) + len(extra) + len(changed)


def cmd_verify(quiet=False, infile=None, allow_missing_inventory=False):
    """Verify one archive (or stdin): structure plus every PAX SHA-256.

    Nothing in the archive stores how many entries carry no checksum: the
    numbers printed at the end are derived while walking the members.

    ``total`` counts every tar member the reader yields (directories and
    symlinks included; PAX extended headers are folded into
    ``member.pax_headers`` and never appear as members of their own), ``ok`` /
    ``bad`` are the regular files whose record matched / did not, and the
    "without a record" column is everything else -- non-regular members plus
    regular files that carry no record.  ``nosum`` keeps the latter apart so
    the report can say which is which, and so the "a paxck archive with no
    record at all" gate below cannot be fooled by a third-party tar.
    """
    src = None
    tf = None
    stream = None
    # regular / 通过 / 失败 / 无记录的条目计数；nosum：普通文件但缺 SHA-256 记录
    total = ok = bad = skip = nosum = regular = 0
    failures = []
    truncated = False
    inventory_blob = None
    inventory_failures = []
    found = {}
    duplicates = []
    fail_tag = i18n.tag('fail')

    try:
        if infile:
            try:
                src = open(infile, 'rb')
            except OSError as e:
                sys.stderr.write(
                    '  ' + fail_tag + ' '
                    + i18n.t('paxck.verify.unreadable', path=infile,
                             err=i18n.os_error(e)) + '\n')
                return 1
        else:
            src = _bin_in()

        try:
            stream = open_archive_stream(src)
            tf = tarfile.open(fileobj=stream, mode='r|')
        except (lzma.LZMAError, OSError, tarfile.TarError) as e:
            sys.stderr.write('  ' + fail_tag + ' '
                             + i18n.t('paxck.verify.unparsable',
                                      err=i18n.os_error(e)) + '\n')
            sys.stderr.write('        ' + i18n.t('paxck.verify.truncated_hint')
                             + '\n')
            return 1

        try:
            for m in tf:
                total += 1
                ph = getattr(m, 'pax_headers', None) or {}
                digest = ph.get(PAX_KEY)

                if m.name == INVENTORY_NAME:
                    # The inventory describes the member set, so it is not part
                    # of it: it cannot list itself and is not counted.  Its own
                    # PAX SHA-256 is still checked, like any regular file.
                    total -= 1
                    fobj = tf.extractfile(m)
                    if fobj is None:
                        inventory_failures.append(i18n.t(
                            'paxck.verify.entry_unreadable', name=m.name))
                        continue
                    inventory_blob = fobj.read()
                    if digest is not None:
                        actual = hashlib.sha256(inventory_blob).hexdigest()
                        if actual != digest:
                            inventory_failures.append(i18n.t(
                                'paxck.inventory.self_mismatch',
                                expected=digest[:16], actual=actual[:16]))
                    continue
                if m.name in found:
                    duplicates.append(m.name)
                found[m.name] = member_identity(m)

                if not m.isfile():
                    skip += 1
                    continue
                regular += 1
                if digest is None:
                    skip += 1
                    nosum += 1
                    continue

                fobj = tf.extractfile(m)
                if fobj is None:
                    bad += 1
                    failures.append(
                        i18n.t('paxck.verify.entry_unreadable', name=m.name))
                    continue

                actual, _ = sha256_stream(fobj)
                if actual == digest:
                    ok += 1
                else:
                    bad += 1
                    failures.append(i18n.t(
                        'paxck.verify.mismatch', name=m.name,
                        expected=digest[:16], actual=actual[:16]))
            # tar 结束标志之前不一定已经读完压缩流。必须排空，才能触发 gzip/xz
            # 的尾部校验，并取得外部 zstd 的最终退出码。
            _drain_archive_stream(stream)
        except (lzma.LZMAError, tarfile.TarError, EOFError, OSError) as e:
            # 流在中途损坏/截断：已校验的部分仍有效，但整体必须判失败
            truncated = True
            failures.append(
                i18n.t('paxck.verify.stream_broken', count=total,
                       err=i18n.os_error(e)))
            bad += 1
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
        # 只有自己打开的文件才负责关闭；stdin 不能关
        if src is not None and src is not _bin_in():
            try:
                src.close()
            except Exception:
                pass

    # 空归档是静默失败的典型：tar 执行失败时流仍合法，但一个条目都没有。
    # 这种情况必须判为失败，否则会把空归档当成有效备份。
    if total == 0:
        sys.stderr.write('  ' + fail_tag + ' '
                         + i18n.t('paxck.verify.empty') + '\n')
        return 1

    # 普通文件一条 SHA-256 记录都没有：说明这不是 paxck 生成的归档，
    # 本次校验实际上什么都没验证。若判成功，等于给第三方 tar 发了免检通行证。
    if bad == 0 and ok == 0 and nosum > 0:
        sys.stderr.write(
            '  ' + fail_tag + ' '
            + i18n.t('paxck.verify.no_checksum_records', count=nosum,
                     key=PAX_KEY) + '\n'
            + '        ' + i18n.t('paxck.verify.no_checksum_origin') + '\n')
        return 1

    # 一个普通文件都没有：不算失败（备份空目录是合法的），但要说清楚
    if regular == 0:
        sys.stderr.write('  ' + i18n.t('paxck.verify.no_regular_files') + '\n')

    # 成员集合：清单成员（如果有）与归档实际成员双向比对。逐文件哈希只能证明
    # “里面的文件内容没变”，发现不了“整个成员被删掉/被插进来”。
    problems = len(inventory_failures)
    failures.extend(inventory_failures)
    for name in duplicates[:50]:
        failures.append(i18n.t('paxck.inventory.duplicate', name=name))
        problems += 1
    if inventory_blob is None:
        if not allow_missing_inventory:
            failures.append(i18n.t('paxck.inventory.absent',
                                   member=INVENTORY_NAME))
            problems += 1
    else:
        try:
            listed = parse_inventory(inventory_blob)
        except InventoryError as e:
            failures.append(i18n.t(e.message_key, **e.kwargs))
            problems += 1
        else:
            problems += _report_inventory(listed, found, failures, quiet)

    if not quiet:
        for line in failures[:50]:
            sys.stderr.write('  ' + fail_tag + ' ' + line + '\n')
        if len(failures) > 50:
            sys.stderr.write('  ' + i18n.t('paxck.verify.more_failures',
                                           count=len(failures) - 50) + '\n')
        if truncated:
            sys.stderr.write('  ' + i18n.t('paxck.verify.incomplete_hint')
                             + '\n')
        sys.stderr.write(i18n.t('paxck.verify.summary', total=total, ok=ok,
                                bad=bad, skip=skip, nosum=nosum,
                                other=skip - nosum) + '\n')

    return 1 if (bad or problems) else 0
