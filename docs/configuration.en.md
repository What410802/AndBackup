# Configuration and CLI Reference

[Chinese version](configuration.md) | [English README](../README.en.md) | [Architecture and limits](flow.en.md)

User-facing usage lives in the README; this page keeps the full configuration,
command-line and cache-cleanup reference.

## Where Settings Come From (precedence)

1. `--config PATH` on the command line (highest)
2. `BACKUP_CONFIG_FILE` environment variable
3. the sibling `src/backup-android.yaml` (when present)
4. operational environment variables override the YAML values
   (`ADB`, `HOST`, `SERIAL`, `ANDROID_SERIAL`, `SOURCE_DIR`, `OUT`, `COMPRESS`, `SOURCE_MODE`, `DEVICE_PYTHON`,
   `DOWNLOAD_DEVICE_PYTHON`, `DEVICE_PYTHON_URL`, `KEEP_ANDROID_ENV`,
   `LOG_LEVEL`, `PROGRESS_INTERVAL`, `SHOW_RATE`, `FORCE`, `TREE_MODE`)
5. built-in defaults

An explicitly missing `--config` is an error. Setting `BACKUP_CONFIG_FILE` empty
or to a missing path disables the default YAML (fully environment-driven). YAML
is a tiny top-level `key: value` subset (single/double-quoted strings,
`true/false/yes/no/on/off` booleans) parsed by `backup.py`; no PyYAML needed.

## YAML Keys

> The shipped template `backup-android.example.yaml` recommends
> `source_mode: device-python`; `download_device_python` then defaults to `true`
> (the first run downloads and caches the interpreter). “Default” below is the
> built-in value used when a key is absent (built-in `source_mode` is
> `host-adb`; `download_device_python` defaults to `true` only for
> `device-python`, otherwise `false`).

| Key | Default | Meaning |
|---|---|---|
| `adb` | `adb` | ADB executable or absolute path |
| `host` | empty → no wireless | wireless endpoint: `IP` or `IP:port`; non-empty runs `adb connect` automatically (aliases `address`/`ip`) |
| `serial` | empty → auto | ADB serial (`adb devices` first column, equivalent to `adb -s SERIAL`; e.g. `AERF6R4517018096`, `adb-…._adb-tls-connect._tcp`); empty auto-selects (one device directly; multiple listed for an interactive choice, error when non-interactive). The `-t` transport id is only needed when serials repeat. Legacy `device_id`/`device`/`adb_serial` still map here. |
| `source_dir` | `/sdcard/DCIM` | absolute device directory to back up |
| `out` | empty → `backup.tar.<suffix>` in cwd | output target: a directory (auto-named from the source_dir tail) or a file; see “OUT semantics” |
| `compress` | empty/absent → `none` | `xz`, `gzip`, `zstd`, or `none`; empty/absent means `none` (uncompressed) |
| `source_mode` | `host-adb` | `host-adb` (host reads) or `device-python` (device packs) |
| `device_python` | unset | `device-python`: local Android ARM64 Python, a single file or a prefix dir (`bin/`+`lib/`) |
| `download_device_python` | `true` for `device-python` (else `false`) | fetch the interpreter when no usable `device_python` exists |
| `device_python_url` | pinned upstream `.tar.zst` | override the download URL; may be a local `.tar.zst` |
| `keep_android_env` | unset | keep the device interpreter cache: `true`/`false`, else ask (remove when non-interactive) |
| `log_level` | `info` | `quiet`, `error`, `warn`, `info`, `debug`, `trace` |
| `progress_interval` | `5` | minimum progress interval in seconds |
| `show_rate` | `false` | include payload rate in periodic progress |
| `force` | `false` | overwrite an existing target without asking (alias `overwrite`; same as `-f`/`--force`) |
| `tree_mode` | `auto` | how `tree` enumerates: `auto` (default: prefer the one-shot device pass) / `oneshot` / `per-entry` |

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
**Output target pre-flight**: before any ADB/device work, a placeholder file
(`<name>.partial.<random>`, in the target's own directory) proves the parent
chain can be created and written and screens the target itself. These therefore
fail **immediately** instead of costing a whole transfer: the target already
exists and is a directory; the target is not a regular file (pipe/device);
some path component is an existing file; the parent directory is not writable;
and on Windows the target is read-only or held open by another program. An
interactive terminal is asked for a new `out` (Enter gives up and fails), while
non-interactive runs and `log_level` `quiet`/`error` just error out. If the
transfer and verification both succeed but the final rename fails (a race), the
verified archive is **kept** as that `.partial.*` file and its path is printed,
so it can be renamed or moved manually instead of being lost.
**Existing target file**: an existing target (the name including its suffix) is
never replaced silently. An interactive terminal asks "Overwrite it? [y/N]"
(the default is no; answering `n` or Enter offers another path), while a
non-interactive run (no console, or `log_level` `quiet`/`error`) fails with a
hint to add `--force`, so an unattended run cannot clobber the previous backup.
`-f`/`--force` on the command line (or `force: true` in the config) skips the
question and overwrites; it only decides *whether to ask*, it does not bypass
the cases above where the bytes really cannot be written.
**Relative path base**: every relative path resolves against the **working
directory of the launched process** (wherever you run the wrapper or
`backup.py`), not the config file's directory and not the `src/` script
directory: `out`, `device_python`, a local `.tar.zst` given as
`device_python_url`, `BACKUP_CONFIG_FILE`, and `--config` all behave that way.
A missing `out` parent directory is created (`backup.py` calls `os.makedirs`)
first. The one exception is the default config file: without
`--config`/`BACKUP_CONFIG_FILE`, `backup-android.yaml` is looked up next to
`backup.py`.
**Device selection**: `host` is the wireless endpoint (`IP` or `IP:port`);
non-empty runs `adb connect` first. Afterwards `serial` pins the device when
given; otherwise the controller matches `adb devices` against `host` (exact, or
an `IP:` prefix so an IP-only value works) and errors out when nothing matches
(never silently switching to another device). A non-empty `serial` with an
empty `host` pins that ADB serial directly (USB serial or mDNS id) without
`adb connect`. With both empty, `adb devices` is enumerated: exactly one online
device is used, several are listed for an interactive choice (an error when
non-interactive or `log_level` `quiet`/`error`), and zero is an error.

## Environment Variables

**Language**: command-line output picks its language automatically and can be
forced with `--lang zh|en|auto` (accepted by `paxck.py`, `adb_source.py` and
`backup.py`; in `paxck.py` the flag works before or after the subcommand) or
`ANDROBACKUP_LANG=zh|en`. The order is `--lang` → `ANDROBACKUP_LANG` →
`LC_ALL`/`LC_MESSAGES`/`LANGUAGE`/`LANG` → the OS locale → `en`. The controller
exports its choice to the child tools, so one run prints one language. Help
text and error messages switch with it; argparse's own `usage:`/`error:`
prefixes stay English.

On Windows the "OS locale" is the **user interface language** (the Win32 UI
language), not `locale.getlocale()`: the latter describes the process C locale,
which UTF-8 mode reports as English on a Chinese system — that is what used to
put Chinese OS error text inside an English template. OS error wording is now
translated by this tool from the `errno` (`i18n.os_error`: file already exists,
permission denied or in use, a path component is not a directory, read-only file
system, ...), and only an unrecognized `errno` keeps the raw OS text, so a
message never mixes two languages.

The operational variables map 1:1 to the YAML keys above (e.g.
`DEVICE_PYTHON_URL` ↔ `device_python_url`). Two selector variables:

| Variable | Meaning |
|---|---|
| `PYTHON` | full path of the Python interpreter used by the wrappers (Windows) |
| `BACKUP_CONFIG_FILE` | UTF-8 config path; empty/missing disables YAML |

The legacy `DEVICE_ID`/`DEVICE`/`ADB_SERIAL` environment variables still map to
`SERIAL` (`ANDROID_SERIAL` is accepted too, matching adb itself); `ADB_CONNECT`
is deprecated and ignored.

## Command Line

Archive bytes use binary stdin/stdout; on Windows run pipelines from `cmd.exe`.
All three Python entry points support `--version`.

**Grammar: `executable FUNCTION [options…]`** — the first non-option word picks
the **function** (subcommand) and every later argument belongs to it, so the
command line says whether this run lists, backs up or cleans:

| Entry | Function | Usage | Contract |
|---|---|---|---|
| `paxck.py` | local writer | `paxck.py create DIRECTORY` | raw PAX tar to stdout; `PAXCK.checksum.sha256` per regular file |
| `paxck.py` | compressor | `paxck.py compress {xz,gzip,zstd,none}` | stdin→stdout; `xz`/`gzip`/`none` need only the stdlib |
| `paxck.py` | verifier | `paxck.py verify [ARCHIVE]` or `-i ARCHIVE`, optional `-q` | auto-detect tar/xz/gzip/zstd; verify each PAX SHA-256 |
| `paxck.py` | extractor | `paxck.py extract [ARCHIVE] -C DEST` | default verified/staged/atomic; `--direct-tarfile` for trusted archives |
| `adb_source.py` | data source | `adb_source.py pack [--adb ADB] [--log-level LEVEL] [--progress-interval SECONDS] [--show-rate] DIRECTORY` | `host-adb`: raw PAX tar to stdout (the pipeline stage) |
| `backup.py` | backup | `backup.py backup [--config PATH] [--log-level …] [--progress-interval …] [--show-rate] [-f|--force] [--prune-source] [--prune-dry-run] [--clean-env] [--clean-host-cache]` | run + verify + atomic replace; the last two options mean "also clean these caches after a successful run" |
| `backup.py` | tree | `backup.py tree [--config PATH] [--log-level …] [--tree-out PATH] [--tree-mode {auto,oneshot,per-entry}] [--clean-env] [--clean-host-cache]` | list only the detailed tree of the source directory (mode, numeric owner/group UID/GID, size, time, symlink targets) to stdout, or to `--tree-out PATH`. Writes no archive; enumeration modes and progress are described below |
| `backup.py` | clean | `backup.py clean [{env,host-cache,all}] [--config PATH] [--log-level …]` | clean caches and exit: `env` = device Python environment, `host-cache` = host download/unpack cache, `all` = both (default) |
| Wrappers | — | `backup-android.sh [FUNCTION options…]` / `backup-android.bat [FUNCTION options…]` | forward all args to `backup.py` |

Without a function, `backup.py` behaves like `backup` (so double-clicking a
wrapper or running it bare still backs up); bare `--help`/`--version` show the
**top-level** help (the function list), while `backup.py backup --help` shows the
backup options. `--lang` is accepted before or after the function.

### Migrating from the old spelling

The old releases expressed the function as an option (`--list-tree`,
`--clean-env`, `--clean-host-cache`). Those are no longer silently reinterpreted
as options of the default function: they fail with the new spelling instead
(exit code `2`), so an old "clean only" script cannot suddenly start writing an
archive:

| Old | New | Meaning |
|---|---|---|
| `backup.py --list-tree [--tree-out PATH]` | `backup.py tree [--tree-out PATH]` | list only |
| `backup.py --clean-env` | `backup.py clean env` | clean the device environment only |
| `backup.py --clean-host-cache` | `backup.py clean host-cache` | clean the host cache only |
| `backup.py --clean-env --clean-host-cache` | `backup.py clean all` | clean both |
| (no equivalent) | `backup.py backup --clean-env` | back up, then clean the device environment |
| `adb_source.py [--adb ADB] DIRECTORY` | `adb_source.py pack [--adb ADB] DIRECTORY` | pipeline data source |

The remaining `backup.py` options (`--config`, `--log-level`,
`--progress-interval`, `--show-rate`, `-f`/`--force`, `--prune-source`,
`--prune-dry-run`) were always options of the backup function and keep their
spelling.

### Tree enumeration modes and progress (`tree --tree-mode`)

`tree` needs one `stat` per entry (plus `readlink` for symlinks), which used to
mean one adb round trip per entry from the host -- a 5093-entry directory took
about 17 minutes. The default is now:

- **`oneshot`** (the default strategy): one device-side pass. The device runs
  `find ROOT -exec stat -c '…|%n' -- {} +`, so `stat` is batched there and the
  host only streams the records back; a second `find -type l -exec sh -c …`
  pass collects symlink targets. A whole tree costs a handful of adb round
  trips: the same 5093-entry directory now lists in ~1.6 s (~600x faster).
- **`per-entry`**: the original host-driven loop (one adb round trip per
  entry). Kept as the fallback because it works on every ROM.
- **`auto`** (the default): try `oneshot` first, and fall back to `per-entry`
  with a `[WARN]` when the device refuses it (e.g. a `find` without
  `-exec … +`) or when its output disagrees with the directory listing (a path
  containing a newline cannot ride in a line-based record).
  `--tree-mode oneshot` instead fails hard, so a script can insist on the fast
  path.

Both strategies print `[PROGRESS]` lines on **stderr** at `progress_interval`:
the enumeration phase shows "X received, N entries", the one-shot phase shows
"N/total entries received", and the per-entry mode shows "N/total entries
collected". The listing itself (stdout or `--tree-out`) stays pure, so it can
still be piped; its header gained one line, `# listing: oneshot|per-entry`,
recording which strategy ran.

Set the default with the YAML key `tree_mode` or the environment variable
`TREE_MODE`.

### Deleting the packed source entries (`backup --prune-source`)

`backup.py backup --prune-source` removes the entries that really made it into the
archive from the device *after the archive was verified and atomically
published*, to free space. It is irreversible:

- **Command-line only**: there is no YAML key and no environment variable (a
  config-file entry is ignored), so a one-time setting cannot silently delete
  the source on every later run.
- **Only packed entries**: the packer writes a manifest of every entry
  (`P:` packed, `D:` packed directory, `S:` skipped, `L:` incomplete listing).
  Anything the adapter skipped — permissions, scoped storage, non-regular files,
  or a file modified while packing — is **never** deleted.
- **The source root itself is never deleted** (e.g.
  `/storage/emulated/0/Android/data/com.tencent.mobileqq` stays even when all of
  its entries are removed).
- **Directories have stricter rules**: they are deleted only when their listing
  was complete and nothing underneath them was skipped. With an incomplete
  listing (`find` returned non-zero) only files and symlinks are deleted and
  every directory is kept.
- **Directories use `rmdir`, never `rm -rf`**: a directory that gained an entry
  after the listing simply fails to be removed and is kept.
- Files use `rm -f` and directories `rmdir`, in deepest-first batches; a failing
  batch is retried per path so the exact entries are reported with `[WARN]`.
- Order: files first, then the emptied directories, followed by an `[INFO]`
  summary: “deleted N packed entries (M of them directories); kept K …”.
- `--prune-dry-run` prints the plan and deletes nothing (it requires
  `--prune-source`).
- At an interactive terminal (and when `log_level` is not `quiet`/`error`) the
  plan is printed and confirmed once; Enter or `y` proceeds, `n` cancels. A
  non-interactive run treats `--prune-source` itself as the authorization.
- `backup.py`, `adb_source.py` and (in `device-python` mode) the device-side
  `paxck.py create` all accept `--packed-manifest PATH`; the controller passes
  it automatically when pruning is requested, so it needs no manual use.
- Nothing is deleted when the backup, the verification or the publish failed:
  the failure path only removes host-side temporary files.

Examples:
```sh
src/backup-android.sh backup --prune-source --prune-dry-run   # see the plan first
src/backup-android.sh backup --prune-source                   # really delete after publishing
src/backup-android.sh --prune-source                          # the function name is optional
```

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

The clean function only cleans (no backup; `all` is the default):
```sh
src/backup-android.sh  clean env          # device /data/local/tmp/andbackup-pyenv
src/backup-android.sh  clean host-cache   # host download cache
src/backup-android.sh  clean all          # both
```
```bat
src\backup-android.bat clean env
src\backup-android.bat clean host-cache
```

Cleaning can also be a **last step of a backup or tree run** (`--clean-env` /
`--clean-host-cache`), executed only when that run succeeded; a failed run keeps
its caches for the retry:
```sh
src/backup-android.sh  backup --clean-host-cache    # back up, then drop the host cache
src/backup-android.sh  tree   --clean-env           # list, then drop the device env
```

## Compression Notes
- `xz`, `gzip`, and plain tar need only the Python standard library.
- `zstd`: stdlib `compression.zstd` on 3.14+; on 3.12/3.13 a `zstd` executable
  on `PATH` is required (exit code 2 otherwise).
- xz defaults to `preset=6` (CPU-bound); use `none`/`gzip` for speed.
