# AndBackup

[Chinese README](README.md) | [English architecture notes](docs/flow.en.md) | [English testing notes](docs/testing.en.md) | [Configuration and CLI reference](docs/configuration.en.md)

AndBackup has two independent pieces:

- `paxck.py` creates a streaming POSIX PAX tar archive for a local directory,
  with a SHA-256 PAX header on every regular file. It can also compress, verify,
  and extract those archives.
- `adb_source.py` makes an ADB-shell-readable Android directory a source for
  the same archive writer. `backup.py` combines that source with compression,
  verification, and atomic replacement of the final host archive.

Android directories are read in one of two explicitly selected modes; a failed
mode is never auto-switched to the other:

- `host-adb` (default): the host reads entries one by one over `adb exec-out`;
  the device only runs `find`/`stat`/`readlink`/`cat` and never receives an
  archive, compression binary, Python runtime, or temporary file.
- `device-python`: the host uploads a user-provided Android ARM64 Python and
  `paxck.py` to the fixed device directory `/data/local/tmp/andbackup-pyenv`,
  where the device streams a raw PAX tar to stdout for host-side compression.
  No archive or compressed file is created on the device; the interpreter is
  validated and reused from the cache, or removed per the caching rules (see
  [docs/configuration.en.md](docs/configuration.en.md)). A python install
  prefix directory (`bin/` + `lib/`) is packed into one tar, uploaded, and
  extracted on the device so it can resolve its standard library.

Files travel as binary data over `adb exec-out`; the host builds, compresses,
and verifies the archive in both modes.

## Requirements

- Python 3.12 or later. There are no third-party Python dependencies.
- Android Platform-Tools `adb` for Android backups. The generic local packer
  does not require ADB.
- An ADB-authorized device and an ADB-shell-readable directory under
  `/storage/emulated/0` for Android backups.
- Use `cmd.exe` for the Windows `.bat` wrapper and binary pipelines. Do not
  put the archive stream through a PowerShell text pipeline.

`xz`, `gzip`, and uncompressed tar use the Python standard library. `zstd`
uses Python 3.14+'s `compression.zstd` when available, otherwise a `zstd`
executable on `PATH`.

At v0.1.0 release preparation, the full offline suite was run with Windows
CPython 3.13.15 and selected cross-platform tests were run with WSL Ubuntu
CPython 3.14.4. Python 3.12 is the declared minimum and is covered by the
post-push CI matrix, but was not installed locally solely for this release.
Ubuntu 24.04 ships Python 3.12; Debian 12 ships Python 3.11, so Debian 12
users need to provide a separate Python 3.12+ interpreter.

## Android Quick Start

Create the ignored local configuration from the tracked, endpoint-free
template. Do not commit the resulting `src/backup-android.yaml`.

```sh
cp src/backup-android.example.yaml src/backup-android.yaml
```

```bat
copy src\backup-android.example.yaml src\backup-android.yaml
```

Edit the copied file. Its supported top-level YAML keys are:

| Key | Meaning |
|---|---|
| `adb` | ADB executable, default `adb`. |
| `adb_serial` | USB serial or wireless `host:port`; empty lets ADB select one device. |
| `adb_connect` | `true` only when a TCP `adb connect` should be issued first. |
| `source_dir` | Android absolute directory to back up. |
| `out` | Host output archive path. |
| `compress` | `xz`, `gzip`, `zstd`, or `none`. |
| `source_mode` | `host-adb` (host reads entries) or `device-python` (device packs via an uploaded Python). |
| `device_python` | Used by `device-python`: a local path to an Android ARM64 Python — a standalone interpreter file or a python install prefix directory (`bin/` + `lib/`). |
| `download_device_python` | `true` lets `device-python` fetch the pinned upstream interpreter when `device_python` is empty or points to a missing path. |
| `device_python_url` | Overrides the pinned `device_python_url` download URL; may be a local `.tar.zst` path for offline reuse. |

USB configuration normally has an empty `adb_serial` for one connected device,
or its USB serial for a multi-device host:

```yaml
adb: adb
adb_serial: ""
adb_connect: false
source_dir: "/storage/emulated/0/DCIM"
out: android-backup.tar.xz
compress: xz
log_level: info
progress_interval: 5
```

For wireless debugging, first pair the host in Android's Wireless debugging
screen, then set the separately displayed *connection* endpoint, not the
pairing endpoint:

```yaml
adb_serial: "192.0.2.1:5555"
adb_connect: true
```

When Android changes its wireless connection port, update only `adb_serial`.
Set `adb_connect: false` when that endpoint is already connected. Do not set
`adb_connect: true` for a USB serial.

Run the matching thin wrapper:

```sh
src/backup-android.sh
```

```bat
src\backup-android.bat
```

Both forward their arguments to `backup.py`, so a configuration elsewhere can
be selected explicitly:

```sh
src/backup-android.sh --config /path/to/site-backup.yaml
```

```bat
src\backup-android.bat --config D:\backup-config\site-backup.yaml
```

Configuration precedence is `--config PATH`, `BACKUP_CONFIG_FILE`, then an
existing sibling `src/backup-android.yaml`. The operational environment
variables `ADB`, `ADB_SERIAL`, `ADB_CONNECT`, `SOURCE_DIR`, `OUT`, `COMPRESS`,
`SOURCE_MODE`, `DEVICE_PYTHON`, `DOWNLOAD_DEVICE_PYTHON`, `DEVICE_PYTHON_URL`,
`LOG_LEVEL`, `PROGRESS_INTERVAL`, and `SHOW_RATE` override YAML values.
`PYTHON` selects the interpreter for the wrappers.

## Local Archive Workflow

`paxck.py` can be used without Android or ADB:

```sh
python3 src/paxck.py create /path/to/local-directory \
  | python3 src/paxck.py compress xz > local.tar.xz
python3 src/paxck.py verify -i local.tar.xz
python3 src/paxck.py extract -i local.tar.xz -C local-restored
```

The Android source adapter can be composed manually, although `backup.py` is
recommended for actual backups because it checks both pipe processes and
publishes output atomically:

```sh
python3 src/adb_source.py --adb adb /storage/emulated/0/DCIM \
  | python3 src/paxck.py compress xz > android.tar.xz
python3 src/paxck.py verify -i android.tar.xz
```

For `device-python`, set `source_mode: device-python` and `device_python` in
YAML (or the `SOURCE_MODE`/`DEVICE_PYTHON` environment variables), then run the
same wrapper. `DEVICE_PYTHON` may be a single self-contained interpreter file or
a python install prefix directory (`bin/` + `lib/`); a directory is packed into
one tar, uploaded, and extracted on the device. The Python must be compatible with the device ABI/linker (static musl aarch64
builds work well). The interpreter is cached at `/data/local/tmp/andbackup-pyenv`;
each run checks the stamp and `--version`, reusing a valid cache and otherwise
re-provisioning it. After a run that created the env, an interactive terminal
is asked whether to keep it (Enter = keep); non-interactive runs remove it
unless `keep_android_env: true`. Delete the device env with `--clean-env` and
the host download cache with `--clean-host-cache` (see
[docs/configuration.en.md](docs/configuration.en.md)). If `device-python`
cannot run, the controller fails instead of silently falling back to
`host-adb`.

The interpreter is not bundled with the repository. To have the controller
fetch it on demand, set `download_device_python: true`; when `device_python` is
empty or points to a path that does not exist yet, `backup.py` downloads the
pinned
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
release (override the URL with `device_python_url`, or point it at a local
`.tar.zst` for offline reuse) and unpacks it into `device_python` or the
per-user cache (`%LOCALAPPDATA%\andbackup` on Windows, `~/.cache/andbackup`
elsewhere). Later runs reuse the cache without network access. Decompression
needs no third-party Python package: Python 3.14+'s standard-library
`compression.zstd`, an external `zstd`, or the OS `tar` (Windows `bsdtar`
handles zstd).

On Windows, run equivalent binary pipelines from `cmd.exe`:

```bat
py src\paxck.py create "C:\source" ^
  | py src\paxck.py compress xz > local.tar.xz
py src\paxck.py verify -i local.tar.xz
py src\paxck.py extract -i local.tar.xz -C local-restored
```

## Public Interfaces and Differences

The complete command-line tables for `paxck.py`, `adb_source.py`, and
`backup.py`, the supported YAML keys/environment variables, the cache and
cleanup rules (`keep_android_env`, `--clean-env`, `--clean-host-cache`), and
compression notes live in [docs/configuration.en.md](docs/configuration.en.md).
Archive metadata fields, time precision, Android permission boundaries, and the
comparison against uploading an independent tar binary to the device live in
[docs/flow.en.md](docs/flow.en.md). Archive semantics, security rules, and the
exit-code table follow below.

## Archive Semantics and Security

Archives are Python `tarfile.PAX_FORMAT`. The writer stores path, type,
portable mode bits, modification time, link target, and regular-file size.
Local regular files also retain source UID/GID in their tar headers. Android
entries use default UID/GID because the ADB adapter does not collect them.
The tool does not write xattrs, ACLs, SELinux labels, atime, ctime, birth time,
user/group names, device IDs, sockets, FIFOs, or device nodes.

Android modification time is read from `stat` `%Y` plus the observable
fraction in `%y`. The source filesystem, Android FUSE/storage implementation,
and Python/PAX representation determine actual precision; a displayed nine
digit fraction is not a universal promise of nanosecond storage precision.
Android shell permissions and scoped-storage boundaries can also prevent
access to some metadata and directories. Those are platform boundaries, not a
claim that a different compression format could recover unavailable metadata.

Some Windows `adb.exe` transports merge remote shell stderr into `exec-out`
stdout. `adb_source.py` discards that remote stderr and removes an internal
NUL-delimited status trailer before writing the tar stream, so diagnostics
cannot become path or file bytes. When `find` reports inaccessible descendants,
the source emits `[WARN]`, skips unreadable entries, writes a valid partial
archive, and returns exit code `3`; the controller consequently keeps the
partial file unpublished. Missing roots, protocol corruption, compression
errors, and files changing between the two reads remain hard failures.

Default extraction accepts only directory, regular-file, symlink, and hardlink
members. It rejects empty, duplicate, absolute, backslash, dot, parent, NUL,
and Windows-drive-style member names, and never follows an archive symlink
while creating later members. Symlink targets themselves are preserved as tar
semantics and may be absolute or dangling. Do not use `--direct-tarfile` with
untrusted input.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Successful command. |
| `1` | Invalid argument, invalid/unverifiable archive, unsafe verified extraction input, or ordinary ADB/root-path failure. |
| `2` | zstd input/output was requested but neither standard-library nor external zstd support is available. |
| `3` | Irrecoverable source, compression, or extraction destination I/O failure. The output must not be considered a successful backup. |

## Testing

Run the complete offline suite:

```sh
python3 -m unittest discover -s tests -t . -v
```

```bat
py -m unittest discover -s tests -t . -v
```

Real-device tests are opt-in: provide `ANDROBACKUP_DEVICE_DIR` explicitly so
the repository does not assume a private device path. USB tests can leave the
serial empty; wireless tests use the current connection `host:port` and may set
`ANDROBACKUP_ADB_CONNECT=1` to connect first.

```sh
ANDROBACKUP_ADB=/path/to/adb \
ANDROBACKUP_DEVICE_DIR=/storage/emulated/0/DCIM \
python3 -m unittest -v tests.test_device_integration
```

See [testing notes](docs/testing.en.md) for coverage and Windows/CMD details,
and [architecture notes](docs/flow.en.md) for pipeline and metadata details.

## License

Released under the [MIT License](LICENSE). See [CHANGELOG.md](CHANGELOG.md)
for version history and [release-0.1.0.md](docs/release-0.1.0.md) for the
initial release draft/checklist.
