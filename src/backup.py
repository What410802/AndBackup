#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-platform AndBackup launcher.

The ``.bat`` and ``.sh`` files are intentionally tiny forwarding wrappers;
all configuration, the host-adb pipeline, verification and atomic replacement
live here so Windows and POSIX follow exactly the same code path.

Sibling modules: ``adbdevice.py`` (ADB invocation and device selection),
``device_python.py`` (the ``source_mode: device-python`` subsystem),
``sourcetree.py`` (``--list-tree``) and ``prune.py`` (``--prune-source``).
"""
import ast
import argparse
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

import adbdevice
import i18n
import paxck
import android_python
import device_python
import prune
import sourcetree


ENV_KEYS = ('ADB', 'HOST', 'SERIAL', 'ANDROID_SERIAL',
            'DEVICE_ID', 'DEVICE', 'ADB_SERIAL',
            'SOURCE_DIR', 'OUT', 'COMPRESS',
            'SOURCE_MODE', 'DEVICE_PYTHON', 'DOWNLOAD_DEVICE_PYTHON',
            'DEVICE_PYTHON_URL', 'KEEP_ANDROID_ENV',
            'LOG_LEVEL', 'PROGRESS_INTERVAL', 'SHOW_RATE', 'FORCE')
DEFAULTS = {'ADB': 'adb', 'SOURCE_DIR': '/sdcard/DCIM',
            'SOURCE_MODE': 'host-adb', 'LOG_LEVEL': 'info',
            'PROGRESS_INTERVAL': '5', 'SHOW_RATE': '0', 'FORCE': '0'}


def _enabled(value):
    """Interpret a config/env boolean (`true`/`yes`/`on`/`1`)."""
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


# Fixed device-side cache location for the device-python interpreter tree.
ANDROID_ENV_DIR = device_python.ANDROID_ENV_DIR

# Kept importable here for callers/tests that use backup.py's helpers directly.
_match_host = adbdevice.match_host


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def read_config(path):
    """Read the deliberately small top-level YAML subset used by this tool."""
    aliases = {
        'adb': 'ADB',
        'host': 'HOST', 'address': 'HOST', 'ip': 'HOST',
        'serial': 'SERIAL', 'device_id': 'SERIAL', 'device': 'SERIAL',
        'adb_serial': 'SERIAL', 'android_serial': 'SERIAL',
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
        'force': 'FORCE', 'overwrite': 'FORCE',
    }
    values = {}
    try:
        with open(path, 'r', encoding='utf-8-sig') as fh:
            lines = list(fh)
    except OSError as e:
        raise OSError(i18n.t('backup.err.config_read', path=repr(path),
                             err=e.strerror or e)) from e
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or text.startswith('#'):
            continue
        if ':' not in text or text.startswith(('-', '{', '[')):
            raise ValueError(i18n.t('backup.err.config_line', number=number))
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
                raise ValueError(i18n.t('backup.err.config_string',
                                        number=number)) from e
            if not isinstance(raw, str):
                raise ValueError(i18n.t('backup.err.config_must_be_string',
                                        number=number))
        else:
            raw = raw.split(' #', 1)[0].strip()
            if raw.startswith(('[', '{')):
                raise ValueError(i18n.t('backup.err.config_no_lists',
                                        number=number))
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
        raise RuntimeError(i18n.t('backup.err.config_missing',
                                  path=repr(config_path)))
    for key in ENV_KEYS:
        if key in os.environ:
            values[key] = os.environ[key]
    # ADB serial selection: modern name plus legacy/ambient aliases.
    if not values.get('SERIAL'):
        for legacy in ('DEVICE_ID', 'DEVICE', 'ADB_SERIAL', 'ANDROID_SERIAL'):
            if values.get(legacy):
                values['SERIAL'] = values[legacy]
                break
    for key, value in DEFAULTS.items():
        values.setdefault(key, value)
    return values, config_path


def _download_device_python_setting(settings, source_mode):
    """`download_device_python` defaults to true for device-python mode."""
    flag = device_python.explicit_bool(
        settings.get('DOWNLOAD_DEVICE_PYTHON', ''))
    return (source_mode == 'device-python') if flag is None else flag


def _stream_android_archive(source, adb, compress, output, env,
                            log_level='info', progress_interval='5',
                            show_rate=False, manifest=None):
    """Compose the Android source adapter and generic compressor safely."""
    source_args = [sys.executable, os.path.join(_script_dir(), 'adb_source.py'),
                   '--adb', adb, '--log-level', str(log_level),
                   '--progress-interval', str(progress_interval),
                   *(('--show-rate',) if show_rate else ()),
                   *(('--packed-manifest', manifest) if manifest else ()),
                   source]
    source_process = subprocess.Popen(
        source_args,
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
        raise RuntimeError(i18n.t(
            'backup.err.transfer_failed', source=source_rc,
            compressor=compressor.returncode,
            detail=i18n.t('i18n.detail_sep', detail=detail) if detail else ''))


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


def output_problem(output_path):
    """Return a localized reason when ``output_path`` cannot be published.

    Only cheap checks, run *before* any ADB work: a target that cannot be
    written must not cost a full transfer.  The parent directory's
    writability is proven separately by creating the placeholder file.
    """
    if os.path.isdir(output_path) and not os.path.islink(output_path):
        return i18n.t('backup.err.out_is_dir', path=output_path)
    if os.path.lexists(output_path):
        if not os.path.isfile(output_path):
            return i18n.t('backup.err.out_not_regular', path=output_path)
        if os.name == 'nt':
            # Windows refuses to replace a read-only file, and a file held open
            # by another process (antivirus, viewer) blocks the replace too.
            try:
                with open(output_path, 'r+b'):
                    pass
            except OSError as e:
                return i18n.t('backup.err.out_locked', path=output_path,
                              err=e.strerror or e)
    return None


def _open_placeholder(output_path):
    """Create the ``OUT.partial.*`` placeholder next to the target.

    Creating it proves that the directory exists and is writable, and reserves
    the name, both before any byte is transferred.
    """
    parent = os.path.dirname(output_path) or os.curdir
    os.makedirs(parent, exist_ok=True)
    fd, path = tempfile.mkstemp(
        prefix=os.path.basename(output_path) + '.partial.', dir=parent)
    os.close(fd)
    return path


def publish_archive(partial, output_path):
    """Move the verified archive onto the target.

    Returns ``None`` on success, else the localized error text.  The verified
    archive stays at ``partial`` when publishing fails, so a finished transfer
    is never thrown away because the destination was unusable.
    """
    try:
        os.replace(partial, output_path)
    except OSError as e:
        return i18n.t('backup.err.publish_failed', path=output_path,
                      partial=partial, err=e.strerror or e)
    return None


def _read_line(text):
    """Read one line of input, or return ``None`` when nobody can answer.

    Windows reports a NUL/DEVNULL stdin as a TTY, so an EOF here is the only
    reliable sign that the run is not interactive after all.
    """
    try:
        return input(text)
    except EOFError:
        return None


def _target_problem(output_path, force):
    """Return the reason ``output_path`` must not be published, or ``None``.

    Pure (it never prompts) so the caller decides how to ask the user.  An
    existing target is only a *question*: without ``force`` it needs consent,
    which is why the reason mentions ``--force``.
    """
    problem = output_problem(output_path)
    if problem is not None or force or not os.path.lexists(output_path):
        return problem
    return i18n.t('backup.err.out_exists', path=output_path)


def _choose_output_path(output_path, source, compress, log_level, force=False):
    """Validate the destination, creating the staging placeholder file.

    Returns ``(output_path, partial)``.  A target that cannot be published at
    all, or an existing target the user (or ``--force``) has not accepted,
    stops the run here, before any transfer.  An interactive run prints the
    reason and asks for another path (with permission to retry after an
    existing file was kept); anything else raises ``RuntimeError``.
    """
    interactive = sys.stdin.isatty() and log_level not in ('quiet', 'error')
    while True:
        try:
            partial = _open_placeholder(output_path)
        except OSError as e:
            # The placeholder could not be created, so the parent directory is
            # unusable; an interactive run may still pick a better path.
            parent = os.path.dirname(output_path) or os.curdir
            problem = i18n.t('backup.err.out_parent', path=parent,
                             err=e.strerror or e)
        else:
            problem = output_problem(output_path)
            if problem is None and not force and os.path.lexists(output_path):
                answer = (_read_line(i18n.t('backup.out.overwrite_prompt',
                                            path=output_path))
                          if interactive else None)
                if answer is not None and answer.strip().lower() in ('y', 'yes'):
                    return output_path, partial
                if answer is not None:
                    # The user chose to keep the file: offer another path.
                    problem = i18n.t('backup.out.overwrite_declined',
                                     path=output_path)
                else:
                    # Nobody was asked (or nobody answered): this is the
                    # non-interactive failure, not a decision.
                    interactive = False
                    problem = i18n.t('backup.err.out_exists',
                                     path=output_path)
            if problem is None:
                return output_path, partial
            try:
                os.unlink(partial)
            except OSError:
                pass
        if not interactive:
            # The caller reports `problem` through the normal fatal-error
            # path, so do not print it twice.
            raise RuntimeError(problem)
        print(i18n.tag('warn') + ' ' + problem, file=sys.stderr)
        answer = _read_line(i18n.t('backup.out.retry_prompt'))
        if answer is None or not answer.strip():
            raise RuntimeError(problem)
        output_path, _needs_confirm = _plan_out(answer.strip(), source,
                                                compress)


def run(settings, prune_source=False, prune_dry_run=False):
    adb = settings['ADB']
    source = settings['SOURCE_DIR']
    compress = (settings.get('COMPRESS', '') or 'none').strip().lower()
    source_mode = str(settings.get('SOURCE_MODE', 'host-adb')).lower()
    # Not named `device_python`: that is the imported subsystem module.
    local_python = settings.get('DEVICE_PYTHON', '').strip()
    download_device_python = _download_device_python_setting(
        settings, source_mode)
    device_python_url = settings.get('DEVICE_PYTHON_URL', '').strip()
    out = settings.get('OUT', '').strip()
    log_level = str(settings.get('LOG_LEVEL', 'info')).lower()
    progress_interval = settings.get('PROGRESS_INTERVAL', '5')
    show_rate = str(settings.get('SHOW_RATE', '')).lower() in ('1', 'true', 'yes', 'on')
    force = _enabled(settings.get('FORCE'))

    if compress not in ('xz', 'gzip', 'zstd', 'none'):
        raise RuntimeError(
            i18n.t('backup.err.unknown_compressor', kind=compress))
    if prune_dry_run and not prune_source:
        raise RuntimeError(i18n.t('prune.err.dry_run_needs_source'))
    if not source:
        raise RuntimeError(i18n.t('backup.err.source_dir_empty'))
    if source_mode not in ('host-adb', 'device-python'):
        raise RuntimeError(
            i18n.t('backup.err.unknown_source_mode', mode=source_mode))
    if log_level not in ('quiet', 'error', 'warn', 'info', 'debug', 'trace'):
        raise RuntimeError(
            i18n.t('backup.err.invalid_loglevel', level=log_level))
    try:
        if float(progress_interval) < 0.1:
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeError(i18n.t('backup.err.invalid_progress_interval'))
    if source_mode == 'device-python':
        # Resolve (or download+unpack) the Android interpreter up front so
        # configuration/network errors surface before ADB/archive work starts.
        local_python = android_python.resolve(
            local_python, download_device_python, device_python_url,
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
                answer = input(i18n.t(
                    'backup.out.confirm', out=out, compress=compress,
                    suffix=suffix, path=output_path))
            except EOFError:
                answer = 'n'
            if (answer or 'y').strip().lower() not in ('n', 'no'):
                output_path += suffix

    env = dict(os.environ)
    i18n.export(env)

    # Validate the destination (and create the placeholder) before touching the
    # device: an unusable target must not waste a whole transfer.
    output_path, partial = _choose_output_path(
        output_path, source, compress, log_level, force)

    device_env = None
    published = False
    manifest_path = None
    try:
        device = adbdevice.resolve_device(adb, settings, log_level)
        env['ANDROID_SERIAL'] = device

        if prune_source:
            fd, manifest_path = tempfile.mkstemp(prefix='andbackup-packed-')
            os.close(fd)

        result = adbdevice.run_adb(adb, ('get-state',), env)
        if result.returncode:
            raise RuntimeError(adbdevice.display_error(
                i18n.t('backup.err.adb_unavailable', serial=device), result))

        if log_level not in ('quiet', 'error'):
            print('[1/3] ' + i18n.t('backup.step.check'))
            print('[2/3] ' + i18n.t('backup.step.stream'))
        with open(partial, 'wb') as fh:
            if source_mode == 'host-adb':
                _stream_android_archive(source, adb, compress, fh, env,
                                        log_level, progress_interval, show_rate,
                                        manifest_path)
            else:
                device_env = device_python.provision_env(
                    adb, env, local_python, log_level)
                device_python.stream_archive(
                    source, adb, compress, fh, env, device_env,
                    log_level, progress_interval, show_rate, manifest_path)

        if log_level not in ('quiet', 'error'):
            print('[3/3] ' + i18n.t('backup.step.verify'))
        verify = subprocess.run(
            [sys.executable, os.path.join(_script_dir(), 'paxck.py'),
             'verify', partial], env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False)
        if verify.returncode:
            detail = verify.stderr.decode('utf-8', 'replace').strip()
            raise RuntimeError(i18n.t('backup.err.verify_failed', detail=detail))
        problem = publish_archive(partial, output_path)
        if problem:
            # Publishing failed although the archive verified: keep the file so
            # the transfer and verification were not wasted.
            partial = None
            raise RuntimeError(problem)
        partial = None
        published = True
        if log_level not in ('quiet', 'error'):
            print(i18n.tag('done') + ' '
                  + i18n.t('backup.done.archive', path=output_path))
            print('       ' + i18n.t('backup.info.size',
                                    size=os.path.getsize(output_path)))
        # The archive is verified and published: only now may packed source
        # entries be deleted, and only those the packer actually wrote.
        if prune_source and manifest_path:
            prune.prune_source(adb, env, manifest_path, source, log_level,
                               prune_dry_run)
        # After a successful, verified run, ask whether to keep the env (or
        # honour keep_android_env / the non-interactive default).
        device_python.finish_env(adb, env, settings, device_env, log_level)
        return 0
    finally:
        if partial:
            try:
                os.unlink(partial)
            except OSError:
                pass
        if manifest_path:
            try:
                os.unlink(manifest_path)
            except OSError:
                pass
        if (device_env is not None and device_env.get('uploaded')
                and not published):
            # Run failed or was interrupted: keep only if explicitly requested.
            if device_python.keep_env_explicit(
                    settings.get('KEEP_ANDROID_ENV', '')) is not True:
                try:
                    device_python.clean_env(adb, env)
                except RuntimeError:
                    pass


def cmd_clean(settings, clean_device, clean_host):
    """Remove cached device-python environments (independent targets)."""
    log_level = str(settings.get('LOG_LEVEL', 'info')).lower()
    if clean_host:
        root = android_python.cache_root()
        shutil.rmtree(root, ignore_errors=True)
        if log_level not in ('quiet', 'error'):
            print(i18n.tag('clean') + ' '
                  + i18n.t('backup.info.cleaned_host_cache', dir=root))
    if clean_device:
        adb = settings['ADB']
        env = dict(os.environ)
        i18n.export(env)
        device = adbdevice.resolve_device(adb, settings, log_level)
        env['ANDROID_SERIAL'] = device
        result = adbdevice.run_adb(adb, ('get-state',), env)
        if result.returncode:
            raise RuntimeError(adbdevice.display_error(
                i18n.t('backup.err.adb_unavailable', serial=device), result))
        device_python.clean_env(adb, env)
        if log_level not in ('quiet', 'error'):
            print(i18n.tag('clean') + ' '
                  + i18n.t('backup.info.cleaned_device_env',
                           dir=ANDROID_ENV_DIR))
    return 0


def main(argv=None):
    paxck.configure_stdio_utf8()
    i18n.set_language(i18n.resolve(cli=i18n.prescan_lang(argv)))
    try:
        parser = argparse.ArgumentParser(
            description=i18n.t('backup.cli.description'))
        parser.add_argument(
            '--version', action='version', version=f'%(prog)s {paxck.VERSION}')
        parser.add_argument('--lang', choices=i18n.LANGUAGES + (i18n.AUTO,),
                            default=None, help=i18n.lang_help())
        parser.add_argument(
            '--config', metavar='PATH', help=i18n.t('backup.cli.config_help'))
        parser.add_argument('--log-level', choices=('quiet', 'error', 'warn', 'info', 'debug', 'trace'),
                            help=i18n.t('backup.cli.log_level_help'))
        parser.add_argument('--progress-interval', metavar='SECONDS',
                            help=i18n.t('backup.cli.progress_help'))
        parser.add_argument('--show-rate', action='store_true',
                            help=i18n.t('backup.cli.show_rate_help'))
        parser.add_argument('-f', '--force', action='store_true',
                            help=i18n.t('backup.cli.force_help'))
        parser.add_argument('--clean-env', action='store_true',
                            help=i18n.t('backup.cli.clean_env_help'))
        parser.add_argument('--clean-host-cache', action='store_true',
                            help=i18n.t('backup.cli.clean_host_cache_help'))
        parser.add_argument('--list-tree', action='store_true',
                            help=i18n.t('backup.cli.list_tree_help'))
        parser.add_argument('--tree-out', metavar='PATH',
                            help=i18n.t('backup.cli.tree_out_help'))
        parser.add_argument('--prune-source', action='store_true',
                            help=i18n.t('prune.cli.source_help'))
        parser.add_argument('--prune-dry-run', action='store_true',
                            help=i18n.t('prune.cli.dry_run_help'))
        args = parser.parse_args(argv)
        settings, _config = _settings(args.config)
        if args.log_level is not None:
            settings['LOG_LEVEL'] = args.log_level
        if args.progress_interval is not None:
            settings['PROGRESS_INTERVAL'] = args.progress_interval
        if args.show_rate:
            settings['SHOW_RATE'] = '1'
        if args.force:
            settings['FORCE'] = '1'
        if args.list_tree:
            return sourcetree.cmd_tree(settings, args.tree_out)
        if args.clean_env or args.clean_host_cache:
            return cmd_clean(settings, args.clean_env, args.clean_host_cache)
        return run(settings, args.prune_source, args.prune_dry_run)
    except (RuntimeError, OSError) as e:
        print(i18n.tag('error') + ' ' + i18n.t('backup.err.fatal', err=e),
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
