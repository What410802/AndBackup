# AndBackup

[Chinese README](README.md) | [English architecture notes](docs/flow.en.md) | [English testing notes](docs/testing.en.md)

AndBackup has two independent pieces:

- `paxck.py` creates a streaming POSIX PAX tar archive for a local directory,
  with a SHA-256 PAX header on every regular file. It can also compress, verify,
  and extract those archives.
- `adb_source.py` makes an ADB-shell-readable Android directory a source for
  the same archive writer. `backup.py` combines that source with compression,
  verification, and atomic replacement of the final host archive.

The Android device never receives an archive, compression binary, Python
runtime, or temporary file. Files travel as binary data over `adb exec-out`;
the host builds and verifies the archive.

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

USB configuration normally has an empty `adb_serial` for one connected device,
or its USB serial for a multi-device host:

```yaml
adb: adb
adb_serial: ""
adb_connect: false
source_dir: "/storage/emulated/0/DCIM"
out: android-backup.tar.xz
compress: xz
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
variables `ADB`, `ADB_SERIAL`, `ADB_CONNECT`, `SOURCE_DIR`, `OUT`, and
`COMPRESS` override YAML values. `PYTHON` selects the interpreter for the
wrappers.

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

On Windows, run equivalent binary pipelines from `cmd.exe`:

```bat
py src\paxck.py create "C:\source" ^
  | py src\paxck.py compress xz > local.tar.xz
py src\paxck.py verify -i local.tar.xz
py src\paxck.py extract -i local.tar.xz -C local-restored
```

## Public Command-Line Interface

The following commands are the public interface for v0.1.0. Archive data uses
binary stdin/stdout; human-facing status and diagnostics are separate.

| Entry point | Syntax | Contract |
|---|---|---|
| Local writer | `paxck.py create DIRECTORY` | Writes a raw PAX tar to stdout. The directory itself is the archive root. Each regular file has `PAXCK.checksum.sha256`. |
| Compressor | `paxck.py compress {xz,gzip,zstd,none}` | Reads bytes from stdin and writes compressed or unchanged bytes to stdout. |
| Verifier | `paxck.py verify [ARCHIVE]` or `-i ARCHIVE`, optional `-q` | Detects raw tar, xz, gzip, and zstd; verifies every regular-file PAX SHA-256. |
| Verified extractor | `paxck.py extract [ARCHIVE] -C DEST`, or `-i ARCHIVE` | Default recovery mode. `DEST` must not exist. It checks each file while writing a sibling staging directory, rejects unsafe paths and unchecked regular files, and atomically publishes only after the entire archive succeeds. |
| Direct extractor | `paxck.py extract --direct-tarfile [ARCHIVE] -C DEST` | `--direct` is an alias. Calls Python `tarfile` directly, permits an existing destination, skips PAX SHA-256 validation, and is not atomic. It is only for trusted archives or interoperability; a failure can leave partial output. |
| Android source | `adb_source.py [--adb ADB] DIRECTORY` | Streams an Android absolute directory to stdout as raw PAX tar through `adb exec-out`; no compression or destination file is created. |
| Android controller | `backup.py [--config PATH]` | Reads configuration/environment, starts the Android source and compressor, verifies a unique `.partial` archive, then atomically replaces `OUT`. |
| POSIX wrapper | `backup-android.sh [ARGS...]` | Forwards all arguments to its sibling `backup.py`. |
| CMD wrapper | `backup-android.bat [ARGS...]` | Forwards all arguments to its sibling `backup.py`; run from CMD. |

`paxck.py --version`, `adb_source.py --version`, and `backup.py --version`
print the release version. Successful extraction status goes to stdout. Failure
diagnostics go to stderr. A direct-extraction success explicitly says that it
was not SHA-256-verified and was non-atomic.

No undocumented Python module functions, classes, or constants are a stable
public API in v0.1.0; the table above is the supported interface.

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
