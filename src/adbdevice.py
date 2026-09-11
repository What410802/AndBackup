#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADB primitives shared by the controller, the tree listing and device-python.

Everything here is about talking to ``adb`` and picking a device; archive,
configuration and command-line concerns live in ``backup.py``.  The Android
backup feature is the only consumer: ``paxck.py`` and its verify/extract
siblings never import this module.
"""
import os
import re
import shlex
import subprocess
import sys

import i18n


def run_adb(adb, args, env):
    try:
        return subprocess.run([adb] + list(args), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False)
    except OSError as e:
        raise RuntimeError(i18n.t('backup.err.adb_launch', adb=repr(adb),
                                  err=e)) from e


def run_adb_checked(adb, args, env, label):
    """Run one adb command, raising with ``label`` when it fails."""
    result = run_adb(adb, args, env)
    if result.returncode:
        raise RuntimeError(display_error(label, result))
    return result


def display_error(prefix, result):
    detail = result.stderr.decode('utf-8', 'replace').strip()
    return f'{prefix}: {detail or "exit " + str(result.returncode)}'


def base_adb_env():
    """Environment for device enumeration: no ambient serial pinning."""
    env = dict(os.environ)
    env.pop('ANDROID_SERIAL', None)
    return env


def adb_devices(adb):
    """Parse `adb devices` into [(serial, state), ...]."""
    result = run_adb(adb, ('devices',), base_adb_env())
    if result.returncode:
        raise RuntimeError(
            display_error(i18n.t('backup.err.list_devices'), result))
    devices = []
    for line in result.stdout.decode('utf-8', 'replace').splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        if '\t' in line:
            serial, state = line.split('\t', 1)
        else:
            serial, state = line.split(None, 1)
        devices.append((serial.strip(), state.strip()))
    return devices


def choose_device(devices, log_level, hint=None):
    """Pick one device from several, interactively when possible."""
    if len(devices) == 1:
        return devices[0]
    if not sys.stdin.isatty() or log_level in ('quiet', 'error'):
        message = i18n.t('backup.err.multi_device',
                         devices='、'.join(devices))
        raise RuntimeError(i18n.t('backup.err.with_hint', hint=hint,
                                  message=message) if hint else message)
    print(hint or i18n.t('backup.info.multi_device'))
    for index, serial in enumerate(devices, 1):
        print(f'  [{index}] {serial}')
    try:
        answer = input(i18n.t('backup.prompt.device_index'))
    except EOFError:
        raise RuntimeError(i18n.t('backup.err.no_device_chosen'))
    try:
        choice = int(answer.strip())
    except ValueError:
        raise RuntimeError(i18n.t('backup.err.bad_index', answer=answer))
    if not 1 <= choice <= len(devices):
        raise RuntimeError(i18n.t('backup.err.index_range', choice=choice))
    return devices[choice - 1]


def match_host(devices, host):
    """Online serials that correspond to a wireless ``host`` target.

    ``adb connect 192.0.2.1`` yields the serial ``192.0.2.1:5555``, so an
    IP-only ``host`` also matches by prefix; an mDNS id matches exactly.
    """
    host = host.lower()
    return [serial for serial, state in devices
            if state == 'device'
            and (serial.lower() == host
                 or serial.lower().startswith(host + ':'))]


def resolve_device(adb, settings, log_level):
    """Pick the ADB device to use; returns its serial as shown by adb.

    * ``host`` selects the wireless endpoint (``IP`` or ``IP:port``); the
      controller runs ``adb connect`` first, then pins the matching device.
    * ``serial`` pins a specific adb device (the first column of
      ``adb devices``: a USB serial or an mDNS id).
    * With neither set, exactly one online device is used; several are
      resolved interactively (or error when non-interactive); zero is an error.
    """
    host = (settings.get('HOST', '') or '').strip()
    serial = (settings.get('SERIAL', '') or '').strip()
    if host:
        result = run_adb(adb, ('connect', host), base_adb_env())
        if result.returncode:
            raise RuntimeError(display_error(
                i18n.t('backup.err.connect_failed', host=host), result))
        if serial:
            return serial
        matched = match_host(adb_devices(adb), host)
        if len(matched) == 1:
            return matched[0]
        if not matched:
            raise RuntimeError(i18n.t('backup.err.host_no_match', host=host))
        return choose_device(matched, log_level,
                             hint=i18n.t('backup.hint.host_multi', host=host))

    if serial:
        return serial

    online = [serial for serial, state in adb_devices(adb)
              if state == 'device']
    if not online:
        raise RuntimeError(i18n.t('backup.err.no_devices'))
    return choose_device(online, log_level)


_ADB_STATUS_RE = re.compile(rb'\0__ANDBACKUP_RC__(\d+)\0$')


def adb_shell_command(command):
    """Wrap one device shell command with the shared exec-out protocol.

    Windows ``adb.exe`` may merge remote shell stderr into ``exec-out``
    stdout, which would corrupt a path listing or a ``stat`` line.  Remote
    stderr is discarded on the device and a NUL-delimited status trailer is
    appended, then stripped again on the host (same protocol as
    ``adb_source.py``).
    """
    return (f'{command} 2>/dev/null; '
            f'__andbackup_rc=$?; '
            f"printf '\\0__ANDBACKUP_RC__%s\\0' \"$__andbackup_rc\"")


def adb_exec_shell(adb, env, command):
    """Return ``(payload, remote_rc)`` for one device shell command."""
    result = run_adb(
        adb, ('exec-out', 'sh', '-c', adb_shell_command(command)), env)
    if result.returncode:
        raise RuntimeError(
            display_error(i18n.t('backup.err.adb_command_failed'), result))
    match = _ADB_STATUS_RE.search(result.stdout)
    if not match:
        raise RuntimeError(
            i18n.t('backup.err.no_remote_rc', command=repr(command)))
    return result.stdout[:match.start()], int(match.group(1))


def format_size(value):
    """Human-readable byte count used by progress output and the tree header."""
    units = ('B', 'KiB', 'MiB', 'GiB', 'TiB')
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f'{int(amount)} B' if unit == 'B' else f'{amount:.1f} {unit}'
        amount /= 1024


def quote(path):
    """POSIX-quote one device path for a shell command."""
    return shlex.quote(path)
