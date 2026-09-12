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
import lzma
import sys
import tarfile

import i18n
from paxck import (PAX_KEY, _bin_in, _drain_archive_stream, open_archive_stream,
                   sha256_stream)


def cmd_verify(quiet=False, infile=None):
    """Verify one archive (or stdin): structure plus every PAX SHA-256."""
    src = None
    tf = None
    stream = None
    # regular / 通过 / 失败 / 无记录的条目计数；nosum：普通文件但缺 SHA-256 记录
    total = ok = bad = skip = nosum = regular = 0
    failures = []
    truncated = False
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
                                bad=bad, skip=skip) + '\n')

    return 1 if bad else 0
