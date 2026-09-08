#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-platform AndBackup launcher.

The ``.bat`` and ``.sh`` files are intentionally tiny forwarding wrappers;
all configuration, ADB setup, streaming, verification and atomic replacement
live here so Windows and POSIX follow exactly the same code path.
"""
import ast
import argparse
import os
import subprocess
import sys
import tempfile
import threading

import paxck


ENV_KEYS = ('ADB', 'ADB_SERIAL', 'ADB_CONNECT', 'SOURCE_DIR', 'OUT', 'COMPRESS',
            'LOG_LEVEL', 'PROGRESS_INTERVAL', 'SHOW_RATE')
DEFAULTS = {'ADB': 'adb', 'SOURCE_DIR': '/sdcard/DCIM', 'COMPRESS': 'xz',
            'LOG_LEVEL': 'info', 'PROGRESS_INTERVAL': '5', 'SHOW_RATE': '0'}


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def read_config(path):
    """Read the deliberately small top-level YAML subset used by this tool."""
    aliases = {
        'adb': 'ADB', 'adb_serial': 'ADB_SERIAL', 'serial': 'ADB_SERIAL',
        'adb_connect': 'ADB_CONNECT', 'connect': 'ADB_CONNECT',
        'source_dir': 'SOURCE_DIR', 'source': 'SOURCE_DIR',
        'out': 'OUT', 'compress': 'COMPRESS',
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


def run(settings):
    adb = settings['ADB']
    source = settings['SOURCE_DIR']
    compress = settings['COMPRESS'].lower()
    serial = settings.get('ADB_SERIAL', '').strip()
    connect = str(settings.get('ADB_CONNECT', '')).lower() in ('1', 'true', 'yes', 'on')
    out = settings.get('OUT', '').strip()
    log_level = str(settings.get('LOG_LEVEL', 'info')).lower()
    progress_interval = settings.get('PROGRESS_INTERVAL', '5')
    show_rate = str(settings.get('SHOW_RATE', '')).lower() in ('1', 'true', 'yes', 'on')

    if compress not in ('xz', 'gzip', 'zstd', 'none'):
        raise RuntimeError(
            f'未知压缩类型：{compress}（可选 xz / gzip / zstd / none）')
    if not source:
        raise RuntimeError('SOURCE_DIR 不能为空')
    if log_level not in ('quiet', 'error', 'warn', 'info', 'debug', 'trace'):
        raise RuntimeError(
            f'无效日志级别：{log_level}（可选 quiet/error/warn/info/debug/trace）')
    try:
        if float(progress_interval) < 0.1:
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeError('PROGRESS_INTERVAL 必须是不小于 0.1 的秒数')
    if not out:
        extension = {'gzip': 'gz', 'zstd': 'zst', 'xz': 'xz'}.get(compress)
        out = 'backup.tar' if compress == 'none' else f'backup.tar.{extension}'

    env = dict(os.environ)
    if serial:
        env['ANDROID_SERIAL'] = serial
    if connect:
        if not serial:
            raise RuntimeError('ADB_CONNECT 需要同时设置 ADB_SERIAL=host:port')
        result = _run_adb(adb, ('connect', serial), env)
        if result.returncode:
            raise RuntimeError(_display_error(f'无法连接无线 ADB {serial}', result))

    result = _run_adb(adb, ('get-state',), env)
    if result.returncode:
        raise RuntimeError(_display_error(
            'adb 不可用，请检查调试授权和 ADB 路径', result))

    output_path = os.path.abspath(out)
    parent = os.path.dirname(output_path) or os.curdir
    os.makedirs(parent, exist_ok=True)
    fd, partial = tempfile.mkstemp(
        prefix=os.path.basename(output_path) + '.partial.', dir=parent)
    os.close(fd)
    try:
        if log_level not in ('quiet', 'error'):
            print('[1/3] checking ADB and source directory...')
            print('[2/3] streaming Android source through PAX tar and compressor...')
        with open(partial, 'wb') as fh:
            _stream_android_archive(source, adb, compress, fh, env,
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
        if log_level not in ('quiet', 'error'):
            print(f'[完成] {out}')
            print(f'       大小: {os.path.getsize(output_path)} 字节')
        return 0
    finally:
        if partial:
            try:
                os.unlink(partial)
            except OSError:
                pass


def main(argv=None):
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
        args = parser.parse_args(argv)
        settings, _config = _settings(args.config)
        if args.log_level is not None:
            settings['LOG_LEVEL'] = args.log_level
        if args.progress_interval is not None:
            settings['PROGRESS_INTERVAL'] = args.progress_interval
        if args.show_rate:
            settings['SHOW_RATE'] = '1'
        return run(settings)
    except (RuntimeError, OSError) as e:
        print(f'[错误] {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
