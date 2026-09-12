#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``source_mode: device-python``: provision an interpreter and pack on device.

The device-side packer is the same ``paxck.py`` writer, uploaded together with
its message catalog ``i18n.py`` into the fixed cache directory
``/data/local/tmp/andbackup-pyenv``.  This module owns that subsystem: the
interpreter bootstrap decision, the cache stamp, upload/verify/clean-up of the
device environment, the host-side progress display for the device tar stream,
and the remote-manifest pull used by ``--prune-source``.

The ``host-adb`` path in ``backup.py`` does not import anything from here, and
``paxck.py`` never imports this module: the two main features stay independent.
"""
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid

import adbdevice
import android_python
import i18n
import paxck

# Fixed device-side cache location for the device-python interpreter tree.
ANDROID_ENV_DIR = '/data/local/tmp/andbackup-pyenv'

_DEVICE_LOG_LEVELS = {'quiet': 0, 'error': 1, 'warn': 2, 'info': 3,
                      'debug': 4, 'trace': 5}


def _script_dir():
    """Directory holding the sibling scripts uploaded to the device."""
    return os.path.dirname(os.path.abspath(__file__))


def _format_size(value):
    return adbdevice.format_size(value)


class DeviceProgress:
    """Host-side byte/rate progress for a tar stream.

    Driven by ``on_bytes`` from whoever moves the bytes: the device-python
    transfer counts what it receives, and ``source_mode: host`` feeds it the
    growth of the archive being written (no relay needed there, so it polls the
    file instead).  ``sent_key``/``done_key`` carry the wording, because the
    same accounting describes a remote send or a local write.
    """

    def __init__(self, log_level='info', interval=5.0, show_rate=False,
                 sent_key='backup.progress.device_sending',
                 done_key='backup.progress.device_done'):
        name = str(log_level or 'info').lower()
        self.level = _DEVICE_LOG_LEVELS.get(name, _DEVICE_LOG_LEVELS['info'])
        self.interval = max(0.1, float(interval))
        self.show_rate = bool(show_rate)
        self.sent_key = sent_key
        self.done_key = done_key
        self.received = 0
        self._last = 0.0
        self._rate_at = time.monotonic()
        self._rate_bytes = 0
        self._live = paxck.LiveLine()

    def emit(self, level, message):
        if self.level >= _DEVICE_LOG_LEVELS[level]:
            sys.stderr.write(message + '\n')
            sys.stderr.flush()

    def on_bytes(self, count):
        self.received += count
        now = time.monotonic()
        if (self.level >= _DEVICE_LOG_LEVELS['info']
                and now - self._last >= self.interval):
            elapsed = now - self._rate_at
            delta = self.received - self._rate_bytes
            rate = delta / elapsed if elapsed else 0.0
            self._last = now
            self._rate_at = now
            self._rate_bytes = self.received
            rate_text = (i18n.t('backup.progress.rate',
                                rate=_format_size(rate))
                         if self.show_rate else '')
            line = i18n.tag('progress') + ' ' + i18n.t(
                self.sent_key, size=_format_size(self.received), rate=rate_text)
            if self._live.live:
                self._live.update(line)
            else:
                self.emit('info', line)

    def clear(self):
        """Drop the live line without claiming the work finished."""
        self._live.clear()

    def finish(self, size=None):
        if size is not None:
            self.received = size
        if self._live.live:
            self._live.clear()
            return
        self.emit('info', i18n.tag('progress') + ' ' + i18n.t(
            self.done_key, size=_format_size(self.received)))


def pump_source_to_compressor(source_stdout, compressor_stdin, progress):
    """Bridge the ADB tar stream to the compressor while counting bytes.

    If the compressor dies first, keep draining the ADB stream (discarding the
    bytes) so the remote source can finish instead of blocking on a full pipe.
    """
    sink = compressor_stdin
    try:
        while True:
            chunk = source_stdout.read(paxck.CHUNK)
            if not chunk:
                break
            if sink is not None:
                try:
                    sink.write(chunk)
                    progress.on_bytes(len(chunk))
                except (OSError, ValueError):
                    try:
                        sink.close()
                    except OSError:
                        pass
                    sink = None
    except (OSError, ValueError):
        pass
    finally:
        if sink is not None:
            try:
                sink.close()
            except OSError:
                pass


def _find_prefix_interpreter(prefix):
    """Locate a real interpreter file inside a python install prefix directory."""
    bin_dir = os.path.join(prefix, 'bin')
    if not os.path.isdir(bin_dir):
        raise RuntimeError(i18n.t('backup.err.prefix_no_bin', prefix=prefix))
    try:
        names = os.listdir(bin_dir)
    except OSError as e:
        raise RuntimeError(
            i18n.t('backup.err.prefix_bin_unreadable',
                   err=i18n.os_error(e))) from e
    candidates = []
    for name in names:
        full = os.path.join(bin_dir, name)
        if (os.path.isfile(full) and not os.path.islink(full)
                and (name.startswith('python3') or name == 'python')):
            candidates.append(name)
    for name in ('python3', 'python', 'python3.14', 'python3.13', 'python3.12'):
        if name in candidates:
            return 'bin/' + name
    if candidates:
        return 'bin/' + sorted(candidates)[-1]
    raise RuntimeError(i18n.t('backup.err.prefix_no_interpreter', prefix=prefix))


def _tar_prefix(prefix):
    """Create a plain tar of a python prefix.

    Entries live at the tar root (``bin/...``, ``lib/...``), so extracting into
    a device directory D yields ``D/bin/...`` and ``D/lib/...``; the interpreter
    then resolves its stdlib relative to D.
    """
    fd, path = tempfile.mkstemp(prefix='andbackup-pyenv-', suffix='.tar')
    os.close(fd)
    try:
        with tarfile.open(path, 'w', format=tarfile.PAX_FORMAT) as tf:
            for child in sorted(os.listdir(prefix)):
                tf.add(os.path.join(prefix, child), arcname=child,
                       recursive=True)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def local_payload():
    """The files uploaded next to the device interpreter.

    ``paxck.py`` is the packer, ``i18n.py`` its message catalog, and the
    ``tree`` function additionally uses ``tree_device.py``: the same upload
    serves both, and the cache stamp hashes every file listed here, so adding
    one invalidates older device caches automatically.
    """
    """Device-side Python files as ``(remote name, local path)`` pairs.

    ``device-python`` mode uploads ``paxck.py`` (the packer) plus ``i18n.py``
    (its message catalog) next to the interpreter.  The cache stamp covers all
    of them, so replacing either file invalidates an older cached environment.
    """
    return [('paxck.py', os.path.join(_script_dir(), 'paxck.py')),
            ('i18n.py', os.path.join(_script_dir(), 'i18n.py')),
            ('tree_device.py', os.path.join(_script_dir(), 'tree_device.py'))]


def _device_python_plan(local_python):
    """Describe how ``local_python`` maps onto the device cache directory."""
    local_python = os.path.abspath(os.path.expanduser(local_python))
    if os.path.isdir(local_python):
        interp_rel = _find_prefix_interpreter(local_python)
        return {'prefix_mode': True,
                'interp_rel': interp_rel,
                'interp_file': os.path.join(local_python, *interp_rel.split('/'))}
    return {'prefix_mode': False, 'interp_rel': 'python',
            'interp_file': local_python}


def _env_stamp_text(local_python, plan):
    """Identity token of the interpreter + the uploaded device scripts."""
    default_prefix = os.path.abspath(android_python.default_prefix_dir())
    if plan['prefix_mode'] and os.path.abspath(local_python) == default_prefix:
        token = android_python.VARIANT        # auto-downloaded build (no hash)
    else:
        token = _sha256_file(plan['interp_file'])[:16]
    lines = [token]
    for _name, path in local_payload():
        lines.append(_sha256_file(path))
    return ('\n'.join(lines) + '\n').encode('ascii')


def _remote_python_version_ok(adb, env, python_path):
    """True when the interpreter on the device actually runs."""
    command = f'{shlex.quote(python_path)} --version'
    result = adbdevice.run_adb(adb, ('exec-out', 'sh', '-c', command), env)
    return (result.returncode == 0
            and result.stdout.startswith(b'Python '))


def _device_env_valid(adb, env, env_dir, interp_rel, expected_stamp):
    """True when a cached device env matches what we would deploy."""
    stamp_result = adbdevice.run_adb(
        adb, ('exec-out', 'cat', env_dir + '/stamp'), env)
    if stamp_result.returncode != 0 or stamp_result.stdout != expected_stamp:
        return False
    return _remote_python_version_ok(
        adb, env, env_dir + '/' + interp_rel)


def _place_device_env(adb, env, local_python, plan, env_dir):
    """Upload interpreter + the device scripts into a fresh cache directory."""
    interp_rel = plan['interp_rel']
    adbdevice.run_adb_checked(adb, ('shell', 'mkdir', '-p', env_dir), env,
                              i18n.t('backup.err.mkdir_cache'))
    if plan['prefix_mode']:
        local_tar = _tar_prefix(local_python)
        try:
            remote_tar = env_dir + '/python.tar'
            adbdevice.run_adb_checked(adb, ('push', local_tar, remote_tar), env,
                                      i18n.t('backup.err.push_prefix'))
            adbdevice.run_adb_checked(
                adb, ('shell', 'tar', '-xf', remote_tar, '-C', env_dir),
                env, i18n.t('backup.err.unpack_prefix'))
            adbdevice.run_adb(adb, ('shell', 'rm', '-f', remote_tar), env)
        finally:
            try:
                os.unlink(local_tar)
            except OSError:
                pass
    else:
        adbdevice.run_adb_checked(adb, ('push', local_python, env_dir + '/python'),
                                  env, i18n.t('backup.err.push_python'))
    for name, path in local_payload():
        adbdevice.run_adb_checked(adb, ('push', path, env_dir + '/' + name), env,
                                  i18n.t('backup.err.push_script', name=name))
    adbdevice.run_adb_checked(adb, ('shell', 'chmod', '700',
                                    env_dir + '/' + interp_rel), env,
                              i18n.t('backup.err.chmod_python'))


def cached_env_state(adb, env, local_python):
    """The deployed device env when it already matches, else ``None``.

    ``tree`` uses this to prefer the device interpreter without ever paying for
    a download: it only reports an environment that is already there and
    valid.
    """
    local_python = os.path.abspath(os.path.expanduser(local_python))
    if not os.path.exists(local_python):
        return None
    plan = _device_python_plan(local_python)
    if not _device_env_valid(adb, env, ANDROID_ENV_DIR, plan['interp_rel'],
                            _env_stamp_text(local_python, plan)):
        return None
    return {'env_dir': ANDROID_ENV_DIR, 'interp': plan['interp_rel'],
            'uploaded': False}


def _write_env_stamp(adb, env, env_dir, stamp):
    fd, path = tempfile.mkstemp(prefix='andbackup-stamp-')
    os.close(fd)
    try:
        with open(path, 'wb') as fh:
            fh.write(stamp)
        adbdevice.run_adb_checked(adb, ('push', path, env_dir + '/stamp'), env,
                                  i18n.t('backup.err.write_stamp'))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def provision_env(adb, env, local_python, log_level):
    """Ensure the interpreter env exists and is valid on the device.

    Returns ``{'env_dir', 'interp', 'uploaded'}``.  Reuses a valid cached env;
    otherwise places it (retrying once when the interpreter itself fails to
    run, which is usually a corrupt/incompatible upload).
    """
    local_python = os.path.abspath(os.path.expanduser(local_python))
    if not os.path.exists(local_python):
        raise RuntimeError(
            i18n.t('backup.err.device_python_missing', path=local_python))
    plan = _device_python_plan(local_python)
    interp_rel = plan['interp_rel']
    stamp = _env_stamp_text(local_python, plan)
    env_dir = ANDROID_ENV_DIR

    if _device_env_valid(adb, env, env_dir, interp_rel, stamp):
        if log_level not in ('quiet', 'error'):
            print(i18n.tag('cache') + ' '
                  + i18n.t('backup.info.reuse_env', dir=env_dir))
        return {'env_dir': env_dir, 'interp': interp_rel, 'uploaded': False}

    python_path = env_dir + '/' + interp_rel
    for attempt in (1, 2):
        try:
            adbdevice.run_adb(adb, ('shell', 'rm', '-rf', env_dir), env)
        except (RuntimeError, OSError):
            pass
        _place_device_env(adb, env, local_python, plan, env_dir)
        if _remote_python_version_ok(adb, env, python_path):
            _write_env_stamp(adb, env, env_dir, stamp)
            if log_level not in ('quiet', 'error'):
                print(i18n.tag('cache') + ' '
                      + i18n.t('backup.info.uploaded_env', dir=env_dir))
            return {'env_dir': env_dir, 'interp': interp_rel, 'uploaded': True}
        if log_level not in ('quiet', 'error'):
            print(i18n.tag('cache') + ' '
                  + i18n.t('backup.warn.device_selfcheck'))
    try:
        adbdevice.run_adb(adb, ('shell', 'rm', '-rf', env_dir), env)
    except (RuntimeError, OSError):
        pass
    raise RuntimeError(i18n.t('backup.err.device_python_unusable'))


def clean_env(adb, env):
    """Remove the device-side interpreter cache."""
    try:
        return adbdevice.run_adb(adb, ('shell', 'rm', '-rf', ANDROID_ENV_DIR), env)
    except (RuntimeError, OSError) as e:
        raise RuntimeError(
            i18n.t('backup.err.clean_device_env', err=e)) from e


def explicit_bool(value):
    text = (value or '').strip().lower()
    if text in ('1', 'true', 'yes', 'on'):
        return True
    if text in ('0', 'false', 'no', 'off'):
        return False
    return None


def keep_env_explicit(value):
    return explicit_bool(value)


def _decide_keep_device_env(settings, log_level):
    """Resolve whether to keep a freshly uploaded device env.

    ``KEEP_ANDROID_ENV`` true/false wins.  Otherwise prompt when interactive
    (default keep); non-interactive or quiet/error defaults to removing the
    env so automation does not silently leave ~230 MiB on the device.
    """
    explicit = keep_env_explicit(settings.get('KEEP_ANDROID_ENV', ''))
    if explicit is not None:
        return explicit
    if not i18n.can_prompt(log_level):
        return False
    try:
        answer = input(i18n.t('backup.prompt.keep_env'))
    except EOFError:
        return False
    return (answer or 'y').strip().lower() not in ('n', 'no')


def finish_env(adb, env, settings, device_env, log_level):
    """Keep or remove a freshly uploaded env after a run; print outcome."""
    if device_env is None or not device_env.get('uploaded'):
        return
    if _decide_keep_device_env(settings, log_level):
        if log_level not in ('quiet', 'error'):
            print(i18n.tag('cache') + ' '
                  + i18n.t('backup.info.kept_env', dir=ANDROID_ENV_DIR))
        return
    clean_env(adb, env)
    if log_level not in ('quiet', 'error'):
        print(i18n.tag('cache') + ' ' + i18n.t('backup.info.removed_env'))


def _read_remote_file(adb, env, path):
    """Read one device file; None when it cannot be read."""
    result = adbdevice.run_adb(adb, ('exec-out', 'cat', path), env)
    return result.stdout if result.returncode == 0 else None


def _publish_raw_received(adb, env, remote_path, local_path):
    """Fetch the remote packed manifest into ``local_path``."""
    blob = _read_remote_file(adb, env, remote_path)
    if blob is None:
        return False
    with open(local_path, 'wb') as fh:
        fh.write(blob)
    return True


def stream_archive(source, adb, compress, output, env, device_env,
                   log_level='info', progress_interval='5', show_rate=False,
                   manifest=None):
    """Stream one pack using an already-provisioned device-python environment.

    ``device_env`` is the dict produced by ``provision_env``:
      env_dir : /data/local/tmp/andbackup-pyenv
      interp  : interpreter path relative to env_dir (e.g. bin/python3.14)
    The interpreter and the device scripts already exist on the device.  This
    function only runs the pack; its per-run status/error files live under
    ``<env_dir>/run/<uuid>`` and are always removed.  Keeping or deleting the
    cached env itself is the caller's responsibility.
    """
    env_dir = device_env['env_dir']
    interp_rel = device_env['interp']
    run_dir = env_dir + '/run/' + uuid.uuid4().hex
    remote_paxck = env_dir + '/paxck.py'
    remote_python = env_dir + '/' + interp_rel
    remote_status = run_dir + '/status'
    remote_error = run_dir + '/stderr'
    remote_manifest = run_dir + '/packed'
    source_process = None
    compressor = None
    log_thread = None
    pump_thread = None
    source_log = []
    source_stderr = b''
    compressor_stderr = b''
    remote_stderr = b''
    remote_rc = None
    source_rc = 1
    progress = DeviceProgress(log_level, progress_interval, show_rate)
    try:
        adbdevice.run_adb_checked(adb, ('shell', 'mkdir', '-p', run_dir), env,
                                  i18n.t('backup.err.mkdir_run'))

        command = (
            f'{shlex.quote(remote_python)} {shlex.quote(remote_paxck)} create '
            f'{shlex.quote(source)} '
            + (f'--packed-manifest {shlex.quote(remote_manifest)} '
               if manifest else '')
            + f'2>{shlex.quote(remote_error)}; '
            f'__andbackup_rc=$?; printf "%s" "$__andbackup_rc" '
            f'>{shlex.quote(remote_status)}; exit "$__andbackup_rc"')
        progress.emit('info', i18n.tag('progress') + ' '
                      + i18n.t('backup.progress.device_start'))
        source_process = subprocess.Popen(
            [adb, 'exec-out', 'sh', '-c', command], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def relay_source_log():
            if source_process.stderr is None:
                return
            for line in iter(source_process.stderr.readline, b''):
                source_log.append(line)
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()

        log_thread = threading.Thread(target=relay_source_log, daemon=True)
        log_thread.start()
        try:
            compressor = subprocess.Popen(
                [sys.executable, os.path.join(_script_dir(), 'paxck.py'),
                 'compress', compress],
                env=env, stdin=subprocess.PIPE, stdout=output,
                stderr=subprocess.PIPE)
        except OSError:
            source_process.stdout.close()
            raise
        pump_thread = threading.Thread(
            target=pump_source_to_compressor,
            args=(source_process.stdout, compressor.stdin, progress),
            daemon=True)
        pump_thread.start()

        compressor_stderr = compressor.stderr.read()
        compressor_rc = compressor.wait()
        source_rc = source_process.wait()
        pump_thread.join()
        log_thread.join()
        log_thread = None
        source_stderr = b''.join(source_log)

        status_result = adbdevice.run_adb(
            adb, ('exec-out', 'cat', remote_status), env)
        if status_result.returncode == 0:
            try:
                remote_rc = int(status_result.stdout.decode('ascii').strip())
            except (UnicodeDecodeError, ValueError):
                remote_rc = None
        error_result = adbdevice.run_adb(
            adb, ('exec-out', 'cat', remote_error), env)
        if error_result.returncode == 0:
            remote_stderr = error_result.stdout
        if manifest:
            _publish_raw_received(adb, env, remote_manifest, manifest)
    finally:
        if compressor is not None and compressor.poll() is None:
            compressor.kill()
            compressor.wait()
        if source_process is not None and source_process.poll() is None:
            source_process.kill()
            source_process.wait()
        if pump_thread is not None:
            pump_thread.join(timeout=2)
        if log_thread is not None:
            log_thread.join(timeout=2)
        try:
            adbdevice.run_adb(adb, ('shell', 'rm', '-rf', run_dir), env)
        except (RuntimeError, OSError):
            pass

    compressor_rc = compressor.returncode if compressor is not None else 1
    if remote_rc != 0 or source_rc or compressor_rc:
        details = [part.decode('utf-8', 'replace').strip() for part in
                   (source_stderr, remote_stderr, compressor_stderr) if part]
        detail = '\n'.join(details)
        raise RuntimeError(i18n.t(
            'backup.err.transfer_failed_device',
            source=remote_rc if remote_rc is not None else source_rc,
            compressor=compressor_rc,
            detail=i18n.t('i18n.detail_sep', detail=detail) if detail else ''))

    # Success: surface device-side warnings (e.g. skipped unreadable entries)
    # that paxck wrote to the remote stderr file, so they are not dropped.
    if remote_stderr:
        text = remote_stderr.decode('utf-8', 'replace').strip()
        if text:
            sys.stderr.write(text + '\n')
            sys.stderr.flush()
    progress.finish()
