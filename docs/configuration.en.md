# Configuration and CLI Reference

[Chinese version](configuration.md) | [English README](../README.en.md) | [Architecture and limits](flow.en.md)

User-facing usage lives in the README; this page keeps the full configuration,
command-line and cache-cleanup reference.

## Where Settings Come From (precedence)

1. `--config PATH` on the command line (highest)
2. `BACKUP_CONFIG_FILE` environment variable
3. the sibling `src/backup-android.yaml` (when present)
4. operational environment variables override the YAML values
   (`ADB`, `SOURCE_DIR`, `OUT`, `COMPRESS`, `SOURCE_MODE`, `DEVICE_PYTHON`,
   `DOWNLOAD_DEVICE_PYTHON`, `DEVICE_PYTHON_URL`, `KEEP_ANDROID_ENV`,
   `LOG_LEVEL`, `PROGRESS_INTERVAL`, `SHOW_RATE`)
5. built-in defaults

An explicitly missing `--config` is an error. Setting `BACKUP_CONFIG_FILE` empty
or to a missing path disables the default YAML (fully environment-driven). YAML
is a tiny top-level `key: value` subset (single/double-quoted strings,
`true/false/yes/no/on/off` booleans) parsed by `backup.py`; no PyYAML needed.

## YAML Keys

> The shipped template `backup-android.example.yaml` defaults (recommended) to
> `source_mode: device-python` with `download_device_python: true` (first run
> downloads and caches the interpreter). “Default” below is the conservative
> built-in value used when a key is absent (built-in `source_mode` is
> `host-adb`, built-in `download_device_python` is `false`, no auto-download);
> the template overrides only those two, and no mode ever auto-switches to the
> other.

| Key | Default | Meaning |
|---|---|---|
| `adb` | `adb` | ADB executable or absolute path |
| `adb_serial` | empty | USB serial or wireless `host:port`; empty lets ADB choose |
| `adb_connect` | `false` | `true` only to run `adb connect` first for a TCP serial |
| `source_dir` | `/sdcard/DCIM` | absolute device directory to back up |
| `out` | empty → `backup.tar.<suffix>` in cwd | output target: a directory (auto-named from the source_dir tail) or a file; see “OUT semantics” |
| `compress` | `xz` | `xz`, `gzip`, `zstd`, or `none` |
| `source_mode` | `host-adb` | `host-adb` (host reads) or `device-python` (device packs) |
| `device_python` | unset | `device-python`: local Android ARM64 Python, a single file or a prefix dir (`bin/`+`lib/`) |
| `download_device_python` | `false` | fetch the interpreter when no usable `device_python` exists |
| `device_python_url` | pinned upstream `.tar.zst` | override the download URL; may be a local `.tar.zst` |
| `keep_android_env` | unset | keep the device interpreter cache: `true`/`false`, else ask (remove when non-interactive) |
| `log_level` | `info` | `quiet`, `error`, `warn`, `info`, `debug`, `trace` |
| `progress_interval` | `5` | minimum progress interval in seconds |
| `show_rate` | `false` | include payload rate in periodic progress |

**OUT semantics**: empty → `backup.tar.<suffix>` in the current directory
(suffix `.tar.xz`/`.tar.gz`/`.tar.zst`/`.tar` per `compress`). Pointing `out` at
an **existing directory**, or at a path ending in `/` (or `\`), treats it as a
directory and writes `<that directory>/<tail of source_dir><suffix>` (e.g.
`source_dir` `/storage/emulated/0/DCIM` with `out: ./backups/` →
`./backups/DCIM.tar.xz`). Otherwise it is a **file** path: used as-is when it
already ends with the theoretical suffix, else treated as a suffix mismatch that
non-interactive runs (no TTY, or `log_level` `quiet`/`error`) write verbatim,
while an interactive terminal is asked once whether to append the suffix
(Enter = append, `n`/`no` = keep the name).

## Environment Variables

The operational variables map 1:1 to the YAML keys above (e.g.
`DEVICE_PYTHON_URL` ↔ `device_python_url`). Two selector variables:

| Variable | Meaning |
|---|---|
| `PYTHON` | full path of the Python interpreter used by the wrappers (Windows) |
| `BACKUP_CONFIG_FILE` | UTF-8 config path; empty/missing disables YAML |

## Command Line

Archive bytes use binary stdin/stdout; on Windows run pipelines from `cmd.exe`.
All three Python entry points support `--version`.

| Entry | Usage | Contract |
|---|---|---|
| Local writer | `paxck.py create DIRECTORY` | raw PAX tar to stdout; `PAXCK.checksum.sha256` per regular file |
| Compressor | `paxck.py compress {xz,gzip,zstd,none}` | stdin→stdout; `xz`/`gzip`/`none` need only the stdlib |
| Verifier | `paxck.py verify [ARCHIVE]` or `-i ARCHIVE`, optional `-q` | auto-detect tar/xz/gzip/zstd; verify each PAX SHA-256 |
| Extractor | `paxck.py extract [ARCHIVE] -C DEST` | default verified/staged/atomic; `--direct-tarfile` for trusted archives |
| Android source | `adb_source.py [--adb ADB] [--log-level LEVEL] [--progress-interval SECONDS] DIRECTORY` | `host-adb`: raw PAX tar to stdout |
| Android controller | `backup.py [--config PATH] [--log-level …] [--progress-interval …] [--show-rate] [--clean-env] [--clean-host-cache]` | run + verify + atomic replace; `--clean-*` clean caches and exit |
| Wrappers | `backup-android.sh [ARGS…]` / `backup-android.bat [ARGS…]` | forward all args to `backup.py` |

### stdout/stderr responsibilities

Commands that carry **data** (archive bytes) keep stdout strictly binary — no
text is ever mixed in; human-readable status and diagnostics go to stderr and
results are expressed via the exit code. Pure management commands instead use
stdout for status text.

| Command | stdout | stderr |
|---|---|---|
| `paxck.py create` | binary raw tar only (via `sys.stdout.buffer`) | `[WARN]`, `[error]` |
| `paxck.py compress` | binary compressed stream only | `[error]` (e.g. missing zstd support) |
| `paxck.py verify` | empty (deliberately no text) | all diagnostics and the summary — “N entries: …”, even on success |
| `paxck.py extract` (default) | on success: `[done] verified and extracted to …` | `[FAIL] …`, `[WARN] …` |
| `paxck.py extract --direct-tarfile` | on success: `[done] … (not SHA-256-verified, non-atomic)` | `[FAIL] …` |
| `adb_source.py` | binary raw tar only | progress, `[WARN]`, `[error]` |
| `backup.py` | text status (`[1/3]`…`[done]`, `[cache]`, `[clean]`) | source progress, device diagnostics/warnings, `[error]` |
| `backup-android.sh/.bat` | forwards `backup.py` stdout | forwards `backup.py` stderr |

Rules:

- Trust the exit code, not stdout parsing. `paxck.py verify` exits non-zero on
  failure and rejects third-party tars whose regular files have no SHA-256
  record (with an explanation).
- When composing `create`/`compress`/`verify` in a pipeline, stdout carries data
  only (`verify` none); read human information from stderr so it never pollutes
  the stream.
- `extract` has no binary output, so its success text goes to stdout and can be
  read like an ordinary command; failure diagnostics still go to stderr only.
  The default extract prints “verified and extracted”; direct mode explicitly
  says “not SHA-256-verified, non-atomic”.
- `backup.py` stdout is human-facing progress text; backup bytes go to the `out`
  file, never to stdout.
- On Windows use binary pipelines/redirection from `cmd.exe`; avoid PowerShell
  text pipelines that could rewrite bytes.

## Caching and Cleanup

### Host download cache (automatic)
With `download_device_python: true` and no usable interpreter, `backup.py`
downloads the pinned
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
`.tar.zst` into the per-user cache (Windows `%LOCALAPPDATA%\andbackup`, POSIX
`~/.cache/andbackup`) and unpacks only `bin/python3.x` + `lib/python3.x`
(skipping symlinks and `share/`, which Windows cannot materialise). Later runs
re-check the layout and reuse without the network. Extraction uses stdlib
`compression.zstd` (3.14+), an external `zstd`, or the OS `tar` (Windows
`bsdtar`); no third-party Python package.

### Android device cache (`device-python`)
The interpreter is placed at the fixed `/data/local/tmp/andbackup-pyenv`
(`bin/`, `lib/`, `paxck.py`, `stamp`). Before each pack it is validated:
layout + `stamp` (interpreter identity + local `paxck.py` SHA-256) + the
interpreter running `--version`. A valid cache is reused (no upload, no
prompt); a mismatch triggers a fresh placement with a `--version` self-test,
retried once when the interpreter itself fails to run (corrupt/incompatible
upload).

After a run that created the env, an interactive terminal is asked whether to
keep it (Enter = keep). `keep_android_env: true`/`false` forces the choice;
non-interactive (no TTY) with no setting removes it so scripts do not silently
leave ~230 MiB on the device. Reusing an existing env never prompts or removes.
Per-run status files live under `<env>/run/<uuid>` and are always removed.

Cleanup commands are independent:
```sh
src/backup-android.sh  --clean-env          # device /data/local/tmp/andbackup-pyenv
src/backup-android.sh  --clean-host-cache   # host download cache
```
```bat
src\backup-android.bat --clean-env
src\backup-android.bat --clean-host-cache
```

## Compression Notes
- `xz`, `gzip`, and plain tar need only the Python standard library.
- `zstd`: stdlib `compression.zstd` on 3.14+; on 3.12/3.13 a `zstd` executable
  on `PATH` is required (exit code 2 otherwise).
- xz defaults to `preset=6` (CPU-bound); use `none`/`gzip` for speed.
