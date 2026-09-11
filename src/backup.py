#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-platform AndBackup launcher.

The ``.bat`` and ``.sh`` files are intentionally tiny forwarding wrappers;
all configuration, ADB setup, streaming, verification and atomic replacement
live here so Windows and POSIX follow exactly the same code path.
"""
import ast
import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid

import paxck
import android_python


ENV_KEYS = ('ADB', 'DEVICE', 'ADB_SERIAL', 'SOURCE_DIR', 'OUT', 'COMPRESS',
            'SOURCE_MODE', 'DEVICE_PYTHON', 'DOWNLOAD_DEVICE_PYTHON',
            'DEVICE_PYTHON_URL', 'KEEP_ANDROID_ENV',
            'LOG_LEVEL', 'PROGRESS_INTERVAL', 'SHOW_RATE')
DEFAULTS = {'ADB': 'adb', 'SOURCE_DIR': '/sdcard/DCIM',
            'SOURCE_MODE': 'host-adb', 'LOG_LEVEL': 'info',
            'PROGRESS_INTERVAL': '5', 'SHOW_RATE': '0'}

# Fixed device-side cache location for the device-python interpreter tree.
ANDROID_ENV_DIR = '/data/local/tmp/andbackup-pyenv'


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def read_config(path):
    """Read the deliberately small top-level YAML subset used by this tool."""
    aliases = {
        'adb': 'ADB',
        'device': 'DEVICE', 'device_id': 'DEVICE', 'device-id': 'DEVICE',
        'adb_serial': 'DEVICE', 'serial': 'DEVICE',
        'source_dir': 'SOURCE_DIR', 'source': 'SOURCE_DIR',
        'out': 'OUT', 'compress': 'COMPRESS',
        'source_mode': 'SOURCE_MODE', 'source-mode': 'SOURCE_MODE',
        'device_python': 'DEVICE_PYTHON', 'device-python': 'DEVICE_PYTHON',
        'download_device_python': 'DOWNLOAD_DEVICE_PYTHON',
        'download-device-python': 'DOWNLOAD_DEVICE_PYTHON',
        'device_python_url': 'DEVICE_PYTHON_URL',
        'device-python-url': 'DEVICE_PYTHON_URL',
        'keep_android_env': 'KEEP_ANDROID_ENV',
        'keep-android-env': 'KEEP_ANDROID_ENV',
        'log_level': 'LOG_LEVEL', 'log-level': 'LOG_LEVEL',
        'progress_interval': 'PROGRESS_INTERVAL',
        'progress-interval': 'PROGRESS_INTERVAL',
        'show_rate': 'SHOW_RATE', 'show-rate': 'SHOW_RATE',
    }
    values = {}
    try:
        with open(path, 'r', encoding='utf-8-sig') as fh:
            lines = list(fh)
    except OSError as e:
        raise OSError(f'无法读取配置文件 {path!r}: {e.strerror or e}') from e
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or text.startswith('#'):
            continue
        if ':' not in text or text.startswith(('-', '{', '[')):
            raise ValueError(f'配置文件第 {number} 行不是顶层 key: value')
        key, raw = text.split(':', 1)
        env_key = aliases.get(key.strip().lower().replace('-', '_'))
        if env_key is None:
            continue
        raw = raw.strip()
        if raw.startswith('#'):
            raw = ''
        elif raw and raw[0] in "'\"":
            try:
                raw = ast.literal_eval(raw)
            except (SyntaxError, ValueError) as e:
                raise ValueError(f'配置文件第 {number} 行字符串无效') from e
            if not isinstance(raw, str):
                raise ValueError(f'配置文件第 {number} 行必须是字符串')
        else:
            raw = raw.split(' #', 1)[0].strip()
            if raw.startswith(('[', '{')):
                raise ValueError(f'配置文件第 {number} 行不支持列表/映射值')
            if raw.lower() in ('true', 'yes', 'on'):
                raw = '1'
            elif raw.lower() in ('false', 'no', 'off'):
                raw = '0'
        values[env_key] = raw
    return values


def _settings(config_path=None):
    """Merge YAML config, environment overrides and launcher defaults."""
    explicit_config = config_path is not None
    if config_path is None:
        config_path = os.environ.get(
            'BACKUP_CONFIG_FILE', os.path.join(_script_dir(), 'backup-android.yaml'))
    values = {}
    if config_path and os.path.isfile(config_path):
        try:
            values.update(read_config(config_path))
        except (OSError, ValueError) as e:
            raise RuntimeError(str(e)) from e
    elif explicit_config and config_path:
        raise RuntimeError(f'指定的配置文件不存在：{config_path!r}')
    for key in ENV_KEYS:
        if key in os.environ:
            values[key] = os.environ[key]
    # Legacy: ADB_SERIAL is the old name for the DEVICE selector.
    if not values.get('DEVICE') and values.get('ADB_SERIAL'):
        values['DEVICE'] = values['ADB_SERIAL']
    for key, value in DEFAULTS.items():
        values.setdefault(key, value)
    return values, config_path


def _run_adb(adb, args, env):
    try:
        return subprocess.run([adb] + list(args), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False)
    except OSError as e:
        raise RuntimeError(f'无法启动 adb {adb!r}: {e}') from e


def _display_error(prefix, result):
    detail = result.stderr.decode('utf-8', 'replace').strip()
    return f'{prefix}: {detail or "exit " + str(result.returncode)}'


def _base_adb_env():
    env = dict(os.environ)
    env.pop('ANDROID_SERIAL', None)
    return env


def _adb_devices(adb):
    """Parse `adb devices` into [(serial, state), ...]."""
    result = _run_adb(adb, ('devices',), _base_adb_env())
    if result.returncode:
        raise RuntimeError(_display_error('无法枚举 ADB 设备', result))
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


def _resolve_device(adb, settings, log_level):
    """Pick the ADB device to use; returns its serial or host:port.

    * Explicit ``DEVICE`` (config or env) wins; a ``host:port`` value is
      connected first. A missing/offline device surfaces as a later
      ``get-state`` failure.
    * Otherwise auto-select: exactly one online device is used; more than one
      is resolved interactively (or errors out when non-interactive); zero is
      an error.
    """
    device = (settings.get('DEVICE', '') or '').strip()
    if device:
        if ':' in device:
            result = _run_adb(adb, ('connect', device), _base_adb_env())
            if result.returncode:
                raise RuntimeError(_display_error(
                    f'无法连接 ADB 设备 {device}', result))
        return device

    online = [serial for serial, state in _adb_devices(adb)
              if state == 'device']
    if not online:
        raise RuntimeError(
            'adb 未发现已授权的设备。请确认已开启 USB 调试并在设备上授权，'
            '或在配置中设置 device（USB serial 或 host:port）。')
    if len(online) == 1:
        return online[0]
    if not sys.stdin.isatty() or log_level in ('quiet', 'error'):
        raise RuntimeError(
            '检测到多台 ADB 设备（' + '、'.join(online) +
            '），无法自动选择。请在配置中设置 device 字段，或在交互终端中选择。')
    print('检测到多台 ADB 设备：')
    for index, serial in enumerate(online, 1):
        print(f'  [{index}] {serial}')
    try:
        answer = input('请输入要备份的设备序号：')
    except EOFError:
        raise RuntimeError('未选择设备，已退出。')
    try:
        choice = int(answer.strip())
    except ValueError:
        raise RuntimeError(f'无效的序号：{answer!r}')
    if not 1 <= choice <= len(online):
        raise RuntimeError(f'序号超出范围：{choice}')
    return online[choice - 1]


def _stream_android_archive(source, adb, compress, output, env,
                            log_level='info', progress_interval='5', show_rate=False):
    """Compose the Android source adapter and generic compressor safely."""
    source_process = subprocess.Popen(
        [sys.executable, os.path.join(_script_dir(), 'adb_source.py'),
         '--adb', adb, '--log-level', str(log_level),
         '--progress-interval', str(progress_interval),
         *(('--show-rate',) if show_rate else ()), source],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    source_log = []

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
        try:
            compressor = subprocess.Popen(
                [sys.executable, os.path.join(_script_dir(), 'paxck.py'),
                 'compress', compress],
                env=env, stdin=source_process.stdout, stdout=output,
                stderr=subprocess.PIPE)
        except OSError:
            source_process.stdout.close()
            source_process.kill()
            source_process.wait()
            raise

        source_process.stdout.close()
        compressor_stderr = compressor.communicate()[1]
        source_rc = source_process.wait()
        log_thread.join()
        source_stderr = b''.join(source_log)
    finally:
        if source_process.poll() is None:
            source_process.kill()
            source_process.wait()
        log_thread.join()
    if source_rc or compressor.returncode:
        details = []
        if source_stderr:
            details.append(source_stderr.decode('utf-8', 'replace').strip())
        if compressor_stderr:
            details.append(compressor_stderr.decode('utf-8', 'replace').strip())
        detail = '\n'.join(part for part in details if part)
        raise RuntimeError(
            '传输失败：Android 源退出码 %d，压缩器退出码 %d%s' % (
                source_rc, compressor.returncode,
                f'：{detail}' if detail else ''))


def _adb_run_checked(adb, args, env, label):
    result = _run_adb(adb, args, env)
    if result.returncode:
        raise RuntimeError(_display_error(label, result))
    return result


_DEVICE_LOG_LEVELS = {'quiet': 0, 'error': 1, 'warn': 2, 'info': 3,
                      'debug': 4, 'trace': 5}


def _format_size(value):
    units = ('B', 'KiB', 'MiB', 'GiB', 'TiB')
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f'{int(amount)} B' if unit == 'B' else f'{amount:.1f} {unit}'
        amount /= 1024


class _DeviceProgress:
    """Host-side byte/rate progress for the device-python tar stream."""

    def __init__(self, log_level='info', interval=5.0, show_rate=False):
        name = str(log_level or 'info').lower()
        self.level = _DEVICE_LOG_LEVELS.get(name, _DEVICE_LOG_LEVELS['info'])
        self.interval = max(0.1, float(interval))
        self.show_rate = bool(show_rate)
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
            rate_text = f'，速率 {_format_size(rate)}/s' if self.show_rate else ''
            line = (f'[进度] 设备端打包中，已接收 {_format_size(self.received)}'
                    f'{rate_text}')
            if self._live.live:
                self._live.update(line)
            else:
                self.emit('info', line)

    def finish(self):
        if self._live.live:
            self._live.clear()
            return
        self.emit(
            'info',
            f'[进度] 设备端打包完成，共接收 {_format_size(self.received)}')


def _pump_source_to_compressor(source_stdout, compressor_stdin, progress):
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
        raise RuntimeError(
            f'DEVICE_PYTHON 目录不是有效的 Python prefix（缺少 bin/）：{prefix}')
    try:
        names = os.listdir(bin_dir)
    except OSError as e:
        raise RuntimeError(f'无法读取 DEVICE_PYTHON 的 bin/：{e}') from e
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
    raise RuntimeError(
        f'DEVICE_PYTHON 目录的 bin/ 下未找到 python 解释器：{prefix}')


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
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _local_paxck():
    return os.path.join(_script_dir(), 'paxck.py')


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
    """Identity token of the interpreter + the local paxck.py SHA-256."""
    default_prefix = os.path.abspath(android_python.default_prefix_dir())
    if plan['prefix_mode'] and os.path.abspath(local_python) == default_prefix:
        token = android_python.VARIANT        # auto-downloaded build (no hash)
    else:
        token = _sha256_file(plan['interp_file'])[:16]
    paxck_sha = _sha256_file(_local_paxck())
    return (token + '\n' + paxck_sha + '\n').encode('ascii')


def _remote_python_version_ok(adb, env, python_path):
    """True when the interpreter on the device actually runs."""
    command = f'{shlex.quote(python_path)} --version'
    result = _run_adb(adb, ('exec-out', 'sh', '-c', command), env)
    return (result.returncode == 0
            and result.stdout.startswith(b'Python '))


def _device_env_valid(adb, env, env_dir, interp_rel, expected_stamp):
    """True when a cached device env matches what we would deploy."""
    stamp_result = _run_adb(adb, ('exec-out', 'cat', env_dir + '/stamp'), env)
    if stamp_result.returncode != 0 or stamp_result.stdout != expected_stamp:
        return False
    return _remote_python_version_ok(
        adb, env, env_dir + '/' + interp_rel)


def _place_device_env(adb, env, local_python, plan, env_dir):
    """Upload interpreter + paxck.py into a fresh device cache directory."""
    interp_rel = plan['interp_rel']
    _adb_run_checked(adb, ('shell', 'mkdir', '-p', env_dir), env,
                     '无法创建设备缓存目录')
    if plan['prefix_mode']:
        local_tar = _tar_prefix(local_python)
        try:
            remote_tar = env_dir + '/python.tar'
            _adb_run_checked(adb, ('push', local_tar, remote_tar), env,
                             '上传设备 Python 环境失败')
            _adb_run_checked(
                adb, ('shell', 'tar', '-xf', remote_tar, '-C', env_dir),
                env, '解压设备 Python 环境失败')
            _run_adb(adb, ('shell', 'rm', '-f', remote_tar), env)
        finally:
            try:
                os.unlink(local_tar)
            except OSError:
                pass
    else:
        _adb_run_checked(adb, ('push', local_python, env_dir + '/python'), env,
                         '上传设备 Python 失败')
    _adb_run_checked(adb, ('push', _local_paxck(), env_dir + '/paxck.py'), env,
                     '上传设备 paxck.py 失败')
    _adb_run_checked(adb, ('shell', 'chmod', '700',
                           env_dir + '/' + interp_rel), env,
                     '设置设备 Python 执行权限失败')


def _write_env_stamp(adb, env, env_dir, stamp):
    fd, path = tempfile.mkstemp(prefix='andbackup-stamp-')
    os.close(fd)
    try:
        with open(path, 'wb') as fh:
            fh.write(stamp)
        _adb_run_checked(adb, ('push', path, env_dir + '/stamp'), env,
                         '写入设备缓存标识失败')
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _provision_device_python_env(adb, env, local_python, log_level):
    """Ensure the interpreter env exists and is valid on the device.

    Returns ``{'env_dir', 'interp', 'uploaded'}``.  Reuses a valid cached env;
    otherwise places it (retrying once when the interpreter itself fails to
    run, which is usually a corrupt/incompatible upload).
    """
    local_python = os.path.abspath(os.path.expanduser(local_python))
    if not os.path.exists(local_python):
        raise RuntimeError(f'设备 Python 不存在：{local_python}')
    plan = _device_python_plan(local_python)
    interp_rel = plan['interp_rel']
    stamp = _env_stamp_text(local_python, plan)
    env_dir = ANDROID_ENV_DIR

    if _device_env_valid(adb, env, env_dir, interp_rel, stamp):
        if log_level not in ('quiet', 'error'):
            print(f'[缓存] 复用 Android 端 Python 环境：{env_dir}')
        return {'env_dir': env_dir, 'interp': interp_rel, 'uploaded': False}

    python_path = env_dir + '/' + interp_rel
    for attempt in (1, 2):
        try:
            _run_adb(adb, ('shell', 'rm', '-rf', env_dir), env)
        except (RuntimeError, OSError):
            pass
        _place_device_env(adb, env, local_python, plan, env_dir)
        if _remote_python_version_ok(adb, env, python_path):
            _write_env_stamp(adb, env, env_dir, stamp)
            if log_level not in ('quiet', 'error'):
                print(f'[缓存] 已上传 Android 端 Python 环境：{env_dir}')
            return {'env_dir': env_dir, 'interp': interp_rel, 'uploaded': True}
        if log_level not in ('quiet', 'error'):
            print('[缓存] 设备端 Python 自检失败，重新上传一次...')
    try:
        _run_adb(adb, ('shell', 'rm', '-rf', env_dir), env)
    except (RuntimeError, OSError):
        pass
    raise RuntimeError(
        '设备端 Python 无法执行（上传或兼容性问题）。已删除设备缓存环境，'
        '请检查 DEVICE_PYTHON 与设备 ABI，或重新下载解释器后重试。')


def _clean_device_python_env(adb, env):
    try:
        return _run_adb(adb, ('shell', 'rm', '-rf', ANDROID_ENV_DIR), env)
    except (RuntimeError, OSError) as e:
        raise RuntimeError(f'清理设备端 Python 环境失败：{e}') from e


def _keep_env_explicit(value):
    text = (value or '').strip().lower()
    if text in ('1', 'true', 'yes', 'on'):
        return True
    if text in ('0', 'false', 'no', 'off'):
        return False
    return None


def _decide_keep_device_env(settings, log_level):
    """Resolve whether to keep a freshly uploaded device env.

    ``KEEP_ANDROID_ENV`` true/false wins.  Otherwise prompt when interactive
    (default keep); non-interactive or quiet/error defaults to removing the
    env so automation does not silently leave ~230 MiB on the device.
    """
    explicit = _keep_env_explicit(settings.get('KEEP_ANDROID_ENV', ''))
    if explicit is not None:
        return explicit
    if log_level in ('quiet', 'error') or not sys.stdin.isatty():
        return False
    try:
        answer = input('保留 Android 端 Python 环境以便下次直接复用？[Y/n] ')
    except EOFError:
        return False
    return (answer or 'y').strip().lower() not in ('n', 'no')


def _finish_device_env(adb, env, settings, device_env, log_level):
    """Keep or remove a freshly uploaded env after a run; print outcome."""
    if device_env is None or not device_env.get('uploaded'):
        return
    if _decide_keep_device_env(settings, log_level):
        if log_level not in ('quiet', 'error'):
            print(f'[缓存] 已保留 Android 端 Python 环境：{ANDROID_ENV_DIR}')
        return
    _clean_device_python_env(adb, env)
    if log_level not in ('quiet', 'error'):
        print('[缓存] 已删除 Android 端 Python 环境')


def _stream_device_python_archive(source, adb, compress, output, env,
                                  device_env, log_level='info',
                                  progress_interval='5', show_rate=False):
    """Stream one pack using an already-provisioned device-python environment.

    ``device_env`` is the dict produced by ``_provision_device_python_env``:
      env_dir : /data/local/tmp/andbackup-pyenv
      interp  : interpreter path relative to env_dir (e.g. bin/python3.14)
    The interpreter and paxck.py already exist on the device.  This function
    only runs the pack; its per-run status/error files live under
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
    progress = _DeviceProgress(log_level, progress_interval, show_rate)
    try:
        _adb_run_checked(adb, ('shell', 'mkdir', '-p', run_dir), env,
                         '无法创建设备运行目录')

        command = (
            f'{shlex.quote(remote_python)} {shlex.quote(remote_paxck)} create '
            f'{shlex.quote(source)} 2>{shlex.quote(remote_error)}; '
            f'__andbackup_rc=$?; printf "%s" "$__andbackup_rc" '
            f'>{shlex.quote(remote_status)}; exit "$__andbackup_rc"')
        progress.emit('info', '[进度] 设备端 Python 开始打包...')
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
            target=_pump_source_to_compressor,
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

        status_result = _run_adb(adb, ('exec-out', 'cat', remote_status), env)
        if status_result.returncode == 0:
            try:
                remote_rc = int(status_result.stdout.decode('ascii').strip())
            except (UnicodeDecodeError, ValueError):
                remote_rc = None
        error_result = _run_adb(adb, ('exec-out', 'cat', remote_error), env)
        if error_result.returncode == 0:
            remote_stderr = error_result.stdout
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
            _run_adb(adb, ('shell', 'rm', '-rf', run_dir), env)
        except (RuntimeError, OSError):
            pass

    compressor_rc = compressor.returncode if compressor is not None else 1
    if remote_rc != 0 or source_rc or compressor_rc:
        details = [part.decode('utf-8', 'replace').strip() for part in
                   (source_stderr, remote_stderr, compressor_stderr) if part]
        detail = '\n'.join(details)
        raise RuntimeError(
            '传输失败：设备 Python 源退出码 %s，压缩器退出码 %s%s' % (
                remote_rc if remote_rc is not None else source_rc,
                compressor_rc,
                f'：{detail}' if detail else ''))

    # Success: surface device-side warnings (e.g. skipped unreadable entries)
    # that paxck wrote to the remote stderr file, so they are not dropped.
    if remote_stderr:
        text = remote_stderr.decode('utf-8', 'replace').strip()
        if text:
            sys.stderr.write(text + '\n')
            sys.stderr.flush()
    progress.finish()


_OUT_SUFFIX = {'none': '.tar', 'gzip': '.tar.gz', 'xz': '.tar.xz',
               'zstd': '.tar.zst'}


def _plan_out(out, source, compress):
    """Classify the configured OUT into a concrete output file path.

    Returns ``(path, needs_confirm)`` where ``path`` is absolute and
    ``needs_confirm`` is True only when OUT is a *file* whose name does not end
    with the compressor's theoretical suffix (an interactive run may then ask
    to append it):

    * empty ``out`` -> ``backup<theoretical suffix>`` in the current directory;
    * a directory (an existing one, or any path ending in a separator) -> that
      directory, named ``<tail of source_dir><theoretical suffix>``;
    * a file already ending with the theoretical suffix -> unchanged;
    * any other file -> used verbatim (with ``needs_confirm=True``).
    """
    suffix = _OUT_SUFFIX[compress]
    if not out:
        return os.path.abspath('backup' + suffix), False
    if out.endswith(('/', '\\')) or os.path.isdir(os.path.abspath(out)):
        directory = out.rstrip('/\\') or os.curdir
        name = os.path.basename(source.rstrip('/\\')) or 'backup'
        return os.path.abspath(os.path.join(directory, name + suffix)), False
    if out.lower().endswith(suffix):
        return os.path.abspath(out), False
    return os.path.abspath(out), True


def run(settings):
    adb = settings['ADB']
    source = settings['SOURCE_DIR']
    compress = (settings.get('COMPRESS', '') or 'none').strip().lower()
    source_mode = str(settings.get('SOURCE_MODE', 'host-adb')).lower()
    device_python = settings.get('DEVICE_PYTHON', '').strip()
    download_device_python = str(
        settings.get('DOWNLOAD_DEVICE_PYTHON', '')).lower() in (
            '1', 'true', 'yes', 'on')
    device_python_url = settings.get('DEVICE_PYTHON_URL', '').strip()
    out = settings.get('OUT', '').strip()
    log_level = str(settings.get('LOG_LEVEL', 'info')).lower()
    progress_interval = settings.get('PROGRESS_INTERVAL', '5')
    show_rate = str(settings.get('SHOW_RATE', '')).lower() in ('1', 'true', 'yes', 'on')

    if compress not in ('xz', 'gzip', 'zstd', 'none'):
        raise RuntimeError(
            f'未知压缩类型：{compress}（可选 xz / gzip / zstd / none）')
    if not source:
        raise RuntimeError('SOURCE_DIR 不能为空')
    if source_mode not in ('host-adb', 'device-python'):
        raise RuntimeError(
            f'未知 SOURCE_MODE：{source_mode}（可选 host-adb / device-python）')
    if log_level not in ('quiet', 'error', 'warn', 'info', 'debug', 'trace'):
        raise RuntimeError(
            f'无效日志级别：{log_level}（可选 quiet/error/warn/info/debug/trace）')
    try:
        if float(progress_interval) < 0.1:
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeError('PROGRESS_INTERVAL 必须是不小于 0.1 的秒数')
    if source_mode == 'device-python':
        # Resolve (or download+unpack) the Android interpreter up front so
        # configuration/network errors surface before ADB/archive work starts.
        device_python = android_python.resolve(
            device_python, download_device_python, device_python_url,
            quiet=log_level in ('quiet', 'error'))

    output_path, needs_confirm = _plan_out(out, source, compress)
    if needs_confirm:
        # A file whose extension differs from the compressor's theoretical
        # suffix. Non-interactive runs write it verbatim; interactive runs ask
        # once whether to append the suffix. An EOF (closed/no console stdin,
        # which Windows also reports for DEVNULL) defaults to verbatim, i.e.
        # the safe choice when nobody can answer.
        if sys.stdin.isatty() and log_level not in ('quiet', 'error'):
            suffix = _OUT_SUFFIX[compress]
            try:
                answer = input(
                    f'OUT “{out}” 与 compress={compress} 的理论后缀'
                    f'“{suffix}” 不符。自动追加后缀写为'
                    f'“{output_path}{suffix}”？[Y/n] ')
            except EOFError:
                answer = 'n'
            if (answer or 'y').strip().lower() not in ('n', 'no'):
                output_path += suffix

    env = dict(os.environ)
    device = _resolve_device(adb, settings, log_level)
    env['ANDROID_SERIAL'] = device

    result = _run_adb(adb, ('get-state',), env)
    if result.returncode:
        raise RuntimeError(_display_error(
            f'adb 不可用，请检查调试授权和 ADB 路径（设备 {device}）', result))

    parent = os.path.dirname(output_path) or os.curdir
    os.makedirs(parent, exist_ok=True)
    fd, partial = tempfile.mkstemp(
        prefix=os.path.basename(output_path) + '.partial.', dir=parent)
    os.close(fd)
    device_env = None
    published = False
    try:
        if log_level not in ('quiet', 'error'):
            print('[1/3] checking ADB and source directory...')
            print('[2/3] streaming Android source through PAX tar and compressor...')
        with open(partial, 'wb') as fh:
            if source_mode == 'host-adb':
                _stream_android_archive(source, adb, compress, fh, env,
                                        log_level, progress_interval, show_rate)
            else:
                device_env = _provision_device_python_env(
                    adb, env, device_python, log_level)
                _stream_device_python_archive(
                    source, adb, compress, fh, env, device_env,
                    log_level, progress_interval, show_rate)

        if log_level not in ('quiet', 'error'):
            print('[3/3] verifying archive...')
        verify = subprocess.run(
            [sys.executable, os.path.join(_script_dir(), 'paxck.py'),
             'verify', partial], env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False)
        if verify.returncode:
            detail = verify.stderr.decode('utf-8', 'replace').strip()
            raise RuntimeError('归档校验未通过' + (f'：{detail}' if detail else ''))
        os.replace(partial, output_path)
        partial = None
        published = True
        if log_level not in ('quiet', 'error'):
            print(f'[完成] {output_path}')
            print(f'       大小: {os.path.getsize(output_path)} 字节')
        # After a successful, verified run, ask whether to keep the env (or
        # honour keep_android_env / the non-interactive default).
        _finish_device_env(adb, env, settings, device_env, log_level)
        return 0
    finally:
        if partial:
            try:
                os.unlink(partial)
            except OSError:
                pass
        if (device_env is not None and device_env.get('uploaded')
                and not published):
            # Run failed or was interrupted: keep only if explicitly requested.
            if _keep_env_explicit(settings.get('KEEP_ANDROID_ENV', '')) is not True:
                try:
                    _clean_device_python_env(adb, env)
                except RuntimeError:
                    pass


def cmd_clean(settings, clean_device, clean_host):
    """Remove cached device-python environments (independent targets)."""
    log_level = str(settings.get('LOG_LEVEL', 'info')).lower()
    if clean_host:
        root = android_python.cache_root()
        shutil.rmtree(root, ignore_errors=True)
        if log_level not in ('quiet', 'error'):
            print(f'[清理] 已删除主机下载缓存：{root}')
    if clean_device:
        adb = settings['ADB']
        env = dict(os.environ)
        device = _resolve_device(adb, settings, log_level)
        env['ANDROID_SERIAL'] = device
        result = _run_adb(adb, ('get-state',), env)
        if result.returncode:
            raise RuntimeError(_display_error(
                f'adb 不可用，请检查调试授权和 ADB 路径（设备 {device}）', result))
        _clean_device_python_env(adb, env)
        if log_level not in ('quiet', 'error'):
            print(f'[清理] 已删除设备端 Python 环境：{ANDROID_ENV_DIR}')
    return 0


def main(argv=None):
    paxck.configure_stdio_utf8()
    try:
        parser = argparse.ArgumentParser(
            description='AndBackup 主控：读取 YAML 并执行 ADB 流式归档')
        parser.add_argument(
            '--version', action='version', version=f'%(prog)s {paxck.VERSION}')
        parser.add_argument(
            '--config', metavar='PATH',
            help='配置文件路径（默认脚本目录中的 backup-android.yaml；覆盖 BACKUP_CONFIG_FILE）')
        parser.add_argument('--log-level', choices=('quiet', 'error', 'warn', 'info', 'debug', 'trace'),
                            help='日志级别，覆盖配置中的 log_level')
        parser.add_argument('--progress-interval', metavar='SECONDS',
                            help='进度输出最小间隔秒数，覆盖配置中的 progress_interval')
        parser.add_argument('--show-rate', action='store_true',
                            help='显示 ADB 有效载荷速率，覆盖配置中的 show_rate')
        parser.add_argument('--clean-env', action='store_true',
                            help='删除设备端缓存的 Android Python 环境后退出（不执行备份）')
        parser.add_argument('--clean-host-cache', action='store_true',
                            help='删除主机端 Android Python 下载/解压缓存后退出（不执行备份）')
        args = parser.parse_args(argv)
        settings, _config = _settings(args.config)
        if args.log_level is not None:
            settings['LOG_LEVEL'] = args.log_level
        if args.progress_interval is not None:
            settings['PROGRESS_INTERVAL'] = args.progress_interval
        if args.show_rate:
            settings['SHOW_RATE'] = '1'
        if args.clean_env or args.clean_host_cache:
            return cmd_clean(settings, args.clean_env, args.clean_host_cache)
        return run(settings)
    except (RuntimeError, OSError) as e:
        print(f'[错误] {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
