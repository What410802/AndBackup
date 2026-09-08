# AndBackup Architecture

[Chinese version](flow.md) | [English README](../README.en.md)

## Responsibilities

The project separates source acquisition from archive handling:

- `paxck.py` is the generic host-side PAX writer, compressor, verifier, and
  extractor. It has no ADB dependency.
- `adb_source.py` adapts an Android directory to that writer. It lists paths,
  reads metadata, reads symlink targets, and streams regular-file bytes through
  `adb exec-out`.
- `backup.py` is the Android production controller. It merges configuration,
  performs an optional TCP `adb connect`, runs the source and compressor,
  verifies a unique host-side partial archive, then atomically replaces the
  requested output.
- `backup-android.sh` and `backup-android.bat` are deliberately thin POSIX and
  CMD forwarding wrappers.

```text
Android files -- adb exec-out --> adb_source.py -- raw PAX --> paxck.py compress
                                                             |
                                                        host .partial archive
                                                             |
                                                        paxck.py verify
                                                             |
                                                        atomic final output
```

The Android device only runs `find -print0`, `stat`, `readlink`, and `cat` as
the ADB shell user. It does not run tar, a compressor, Python, or receive a
temporary archive.

## ADB Transports

USB and wireless debugging use the same data protocol. For USB, set
`adb_serial` empty for one device or to the USB serial when several devices are
attached; leave `adb_connect` false. For wireless debugging, pair first, then
set `adb_serial` to the currently displayed `host:connection-port`; set
`adb_connect` true only when the controller should run `adb connect` first.

The controller exports the selected serial as `ANDROID_SERIAL` for every ADB
subprocess. `exec-out` is kept as a binary subprocess pipe. In particular,
Windows CMD redirects Python's binary stdout; it must not be replaced by a
PowerShell text pipeline, which can alter arbitrary archive bytes.

## Consistency Model

For each Android regular file, the adapter:

1. Gets type, mode, size, and modification time using `stat`.
2. Reads the file once to calculate SHA-256 and actual length.
3. Refuses the backup when that length differs from `stat`.
4. Reads it again directly into the tar stream, checking that the second read
   has exactly the expected length and that ADB exits successfully.

This bounds memory by the stream chunk size and turns concurrent source changes
into a failure rather than a silently successful partial backup. Local
`paxck.py create` uses the same two-pass rule for regular files.

## PAX Contents

The writer uses `tarfile.PAX_FORMAT`. It writes `name`, tar type, mode, mtime,
size, link target, and `PAXCK.checksum.sha256` on regular files. Local regular
files receive source UID/GID headers; the Android adapter leaves UID/GID at
their tar defaults because it does not collect them.

The project does not currently record uname/gname, atime, ctime, creation time,
inode, device number, xattrs, ACLs, file flags, SELinux labels, or special
files. Android `%y` supplies observable fractional mtime and `%Y` its integral
epoch value. The filesystem/ROM/FUSE implementation controls the actual stored
precision; test a particular path with:

```sh
adb shell "stat -c '%y|%Y' -- /storage/emulated/0/path/to/file"
```

Android scoped storage, Unix permissions, and the ADB shell UID can deny or
synthesize metadata. The absence of unsupported fields is an explicit archive
scope decision; changing compression cannot recover data that was not readable
or collected.

## Extraction Modes

`paxck.py extract` defaults to verified, staged recovery. The destination must
not exist. Each regular file is SHA-256-checked while written to a temporary
sibling directory. Empty, duplicate, unsafe, or unsupported members fail the
operation. The staging directory is renamed to the destination only after
every member and compression footer is valid, so an existing destination is
never touched.

`paxck.py extract --direct-tarfile` (or `--direct`) deliberately delegates to
Python `tarfile` with its traditional trusted-archive semantics. It permits an
existing destination, skips PAX checksum validation, is non-atomic, and may
leave partial files on an error. It is useful for known/trusted conventional
tar archives, not for backup recovery from untrusted data.

## Device-Side Tar Alternative

Uploading a tar executable to `/data/local/tmp` and streaming its output is a
different implementation, not an equivalent archive promise. It depends on
device architecture, linker/SELinux/execution policy, tar version and options;
may preserve different metadata or special files; and normally lacks this
project's two-pass PAX SHA-256 semantics. It may yield the same logical files,
but the tar bytes, PAX headers, compression bytes, hardlink treatment, and
metadata can differ.
