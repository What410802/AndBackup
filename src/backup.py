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

import paxck


ENV_KEYS = ('ADB', 'ADB_SERIAL', 'ADB_CONNECT', 'SOURCE_DIR', 'OUT', 'COMPRESS')
DEFAULTS = {'ADB': 'adb', 'SOURCE_DIR': '/sdcard/DCIM', 'COMPRESS': 'xz'}


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def read_config(path):
    """Read the deliberately small top-level YAML subset used by this tool."""
    aliases = {
        'adb': 'ADB', 'adb_serial': 'ADB_SERIAL', 'serial': 'ADB_SERIAL',
        'adb_connect': 'ADB_CONNECT', 'connect': 'ADB_CONNECT',
        'source_dir': 'SOURCE_DIR', 'source': 'SOURCE_DIR',
        'out': 'OUT', 'compress': 'COMPRESS',
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


def _stream_android_archive(source, adb, compress, output, env):
    """Compose the Android source adapter and generic compressor safely."""
    # stderr must not remain an unread PIPE: a large number of source warnings
    # could otherwise block the producer before the compressor reaches EOF.
    with tempfile.TemporaryFile() as source_log, tempfile.TemporaryFile() as compressor_log:
        source_process = subprocess.Popen(
            [sys.executable, os.path.join(_script_dir(), 'adb_source.py'),
             '--adb', adb, source],
            env=env, stdout=subprocess.PIPE, stderr=source_log)
        try:
            compressor = subprocess.Popen(
                [sys.executable, os.path.join(_script_dir(), 'paxck.py'),
                 'compress', compress],
                env=env, stdin=source_process.stdout, stdout=output,
                stderr=compressor_log)
        except OSError:
            source_process.stdout.close()
            source_process.kill()
            source_process.wait()
            raise

        source_process.stdout.close()
        compressor.wait()
        source_rc = source_process.wait()
        source_log.seek(0)
        compressor_log.seek(0)
        source_stderr = source_log.read()
        compressor_stderr = compressor_log.read()
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

    if compress not in ('xz', 'gzip', 'zstd', 'none'):
        raise RuntimeError(
            f'未知压缩类型：{compress}（可选 xz / gzip / zstd / none）')
    if not source:
        raise RuntimeError('SOURCE_DIR 不能为空')
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
        print('[1/3] checking ADB and source directory...')
        print('[2/3] streaming Android source through PAX tar and compressor...')
        with open(partial, 'wb') as fh:
            _stream_android_archive(source, adb, compress, fh, env)

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
        args = parser.parse_args(argv)
        settings, _config = _settings(args.config)
        return run(settings)
    except (RuntimeError, OSError) as e:
        print(f'[错误] {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
