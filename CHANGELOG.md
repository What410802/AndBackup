# Changelog

All notable changes to this project are recorded in this file.

## [Unreleased]

### Added

- `backup.py backup --prune-source` (command-line only): after the archive has
  been verified and atomically published, delete the source entries that really
  made it into the archive, to free space on the device. The packer records a
  manifest (`paxck.py`/`adb_source.py`/device-side `create` accept
  `--packed-manifest PATH`, passed automatically by the controller), so skipped
  or unreadable entries are never deleted; the source root is never deleted;
  directories are removed only when their listing was complete and nothing
  underneath them was skipped and use `rmdir`, never `rm -rf`. `--prune-dry-run`
  lists the plan without deleting, and an interactive terminal confirms once.
  Nothing is deleted when the run fails.
- Message localization (Chinese/English) for every command-line entry point:
  `--lang zh|en|auto` on `paxck.py`, `adb_source.py` and `backup.py`, plus the
  `ANDROBACKUP_LANG` environment variable. The order is CLI flag, environment,
  `LC_ALL`/`LC_MESSAGES`/`LANGUAGE`/`LANG`, the OS language (on Windows the user
  interface language), then English. `backup.py`
  exports its choice to the child tools it spawns, so one run prints one
  language. Help text, interactive prompts, progress and errors are all
  translated; `src/i18n.py` holds the catalog and `device-python` mode uploads
  it next to `paxck.py` (the device cache stamp covers both files).
- `backup.py tree [--tree-out PATH]`: lists the detailed tree of the
  source directory (type, mode, numeric owner/group UID/GID, size, mtime,
  symlink target) without writing an archive, so permissions can be checked
  before a backup. Requires one `adb` call per entry; large trees are slow.
- `backup.py -f`/`--force` (and the `force`/`FORCE` config key): overwrite an
  existing target without asking. Without it, an existing target file is never
  replaced silently — an interactive terminal asks "Overwrite it? [y/N]" (the
  default is no, and a "no" answer offers another path), a non-interactive run
  fails with a hint to add `--force`. Only the question is skipped: targets that
  cannot be written (directory, special file, unwritable parent, read-only or
  held open on Windows) still fail before any transfer.

### Fixed

- Messages no longer mix two languages. Two causes are gone: on Windows the
  automatic language now follows the **user interface language** instead of
  `locale.getlocale()`, which UTF-8 mode reports as English on a Chinese system
  (so a Chinese system now prints Chinese, as it does for its own error text);
  and OS error strings are translated by `i18n.os_error` from the `errno`
  (`error.errno.*`) instead of being embedded verbatim from `OSError.strerror`,
  so `--lang en` on a Chinese Windows no longer produces an English sentence
  with a Chinese error inside. Only an unrecognized `errno` keeps the raw OS
  text.
- Prompts are only shown when a console can answer them: `i18n.can_prompt`
  replaces the bare `sys.stdin.isatty()` checks (Windows reports a NUL/DEVNULL
  stdin as a TTY). An automated run therefore fails with one clear message
  instead of printing a question nobody can answer, and the prune plan is
  printed whether or not the prompt is shown.
- An unusable output target no longer wastes a whole transfer. `backup.py` now
  creates the `OUT.partial.*` placeholder, and hence validates the destination,
  *before* any ADB command: a target that already exists and is a directory, is
  not a regular file (pipe/device), lies under a path component that is an
  existing file, sits in an unwritable directory or (on Windows) is read-only
  or held open by another program now fails immediately with a clear message.
  An interactive terminal prints the reason and asks for a new `out` path
  instead of losing the run; non-interactive runs (or `log_level` `quiet`/
  `error`) fail as before.
- If publishing fails after the archive has already been verified, the verified
  archive is kept next to the target as `<name>.partial.*` and its path is
  printed, so a completed transfer plus verification is never thrown away.

### Changed

- **Breaking**: the controller and the Android data source now follow the
  `executable FUNCTION [options…]` grammar instead of encoding the function in
  options, so a command line says what it does:

  | Old | New |
  |---|---|
  | `backup.py --list-tree [--tree-out PATH]` | `backup.py tree [--tree-out PATH]` |
  | `backup.py --clean-env` | `backup.py clean env` |
  | `backup.py --clean-host-cache` | `backup.py clean host-cache` |
  | `backup.py --clean-env --clean-host-cache` | `backup.py clean all` |
  | `adb_source.py [--adb ADB] DIRECTORY` | `adb_source.py pack [--adb ADB] DIRECTORY` |

  `backup.py` without a function still backs up, so plain wrapper invocations
  (double-click, `backup-android.bat`) are unchanged, while `backup.py --help`
  now lists the functions. The old spellings are refused with exit code `2` and
  the new form instead of being reinterpreted — without that guard an old
  "clean only" script would have started writing an archive.
- Cleaning the caches can also be the last step of another function:
  `backup --clean-env`, `backup --clean-host-cache`, `tree --clean-env` and
  `tree --clean-host-cache` run the same cleanup, but only after that run
  succeeded (a failed backup keeps its caches for the retry). `backup.py clean`
  on its own still only cleans, and its target defaults to `all`.
- `--prune-source`/`--prune-dry-run` stay options of the backup function
  (`backup.py backup --prune-source`): they consume the manifest of that very
  run, so they cannot become a function of their own.
- `backup.py` no longer overwrites an existing target on its own. Scripts that
  intentionally reuse one output path (for example a nightly job writing
  `out: ./backups/DCIM.tar.xz`) must now pass `-f`/`--force` or set
  `force: true`; interactive runs are asked once instead. This trade keeps an
  unattended typo or a re-run from destroying the previous verified archive.
- Split the two large scripts into focused modules without changing any
  behaviour or CLI. `paxck.py` (was 1186 lines) now holds only the writer,
  the compressor and the CLI; verification and extraction moved to
  `paxverify.py` and `paxextract.py` and are imported lazily, so the module
  uploaded to the device stays just `paxck.py` + `i18n.py` and depends on
  neither. `backup.py` (was 1331 lines) now owns configuration, the `host-adb`
  pipeline, `OUT` planning, publishing and the CLI; the ADB primitives moved to
  `adbdevice.py`, the `device-python` subsystem to `device_python.py`, the
  listing tree to `sourcetree.py` and the prune execution to `prune.py`. The
  "PAX packing with embedded SHA-256" and "Android/local backup" features stay
  independent: the packing side imports none of the controller modules.
- Status tags are now language-neutral (`[ERROR]`, `[WARN]`, `[DONE]`,
  `[INFO]`, `[PROGRESS]`, `[DEBUG]`, `[CACHE]`, `[CLEAN]`, `[DOWNLOAD]` and the
  existing `[FAIL]`) instead of the localized `[错误]`/`[完成]`/`[进度]`/`[缓存]`/
  `[清理]`/`[调试]`, so logs, tests and scripts match them in any language.

### Changed

- ADB device selectors are now named after adb itself: the YAML key and
  environment variable are `serial`/`SERIAL` (equivalent to `adb -s SERIAL`,
  plus the ambient `ANDROID_SERIAL`), while `device_id`, `DEVICE_ID`, `device`,
  `DEVICE` and `ADB_SERIAL` remain accepted as legacy aliases. `-t` transport
  ids are only needed when serials repeat, so they are no longer mentioned as
  the primary selector.
- Entries that cannot be read (permissions, scoped storage) or that changed
  size before their first read are skipped with `[WARN]` and the archive is
  still verified and published; only a run that can enumerate no archivable
  entry at all produces no file. A two-pass mismatch while a file is being
  written still fails hard with exit code `3`.

### Fixed

- `--list-tree` and `adb_source.py` share one exec-out protocol: remote stderr
  is discarded on the device and a NUL status trailer is stripped on the host,
  so `adb.exe` builds that merge stderr into `exec-out` stdout cannot inject
  text into the listing, the `stat` output, or the archive.

## [0.2.0] - 2026-09-09

### Added

- `device-python` mode: the Android directory is packed on the device by an
  uploaded Python running the same `paxck.py`, keeping the two-read PAX
  SHA-256 semantics of `host-adb`.
- On-demand interpreter bootstrap: `download_device_python: true` fetches a
  pinned python-build-standalone ARM64 build into a per-user cache and unpacks
  only the needed `bin/`+`lib/` subtree; local install-prefix directories are
  supported too.
- Device-side interpreter cache at `/data/local/tmp/andbackup-pyenv` with a
  stamp check and reuse; `keep_android_env`, `--clean-env` and
  `--clean-host-cache` control retention and cleanup.
- Configurable `out`: a directory target derives `<source_dir-tail><suffix>`;
  a mismatched file suffix is appended after a prompt (written verbatim in
  non-interactive runs).
- Log levels, progress interval, and ADB transfer-rate reporting.

### Changed

- The shipped `backup-android.example.yaml` now defaults to
  `device-python` + `download_device_python: true` (recommended). When unset
  the built-in remains `host-adb`; a failed mode never auto-switches to the
  other.
- README slimmed: the full configuration/CLI/cache reference moved to
  `docs/configuration.md` (+ English), and an architecture diagram was added.
- Documented the stdout/stderr responsibilities of each CLI entry point.

### Fixed

- Non-interactive (EOF/`DEVNULL`) runs now write a mismatched output name
  verbatim instead of prompting, including on Windows.

### Security and reliability

- Device-python placement self-tests with `--version`, retries once, and fails
  loudly (removing the cache) rather than silently falling back to `host-adb`.

## [0.1.0] - 2026-09-08

### Added

- Cross-platform Android `/storage/emulated/0` backup via USB or TCP ADB.
- Generic local PAX tar writer with per-regular-file SHA-256, streaming
  compression, verification, and extraction.
- Verified, staged extraction with atomic publication, plus an explicit
  trusted-archive `tarfile` direct-extraction mode.
- Windows CMD and POSIX shell wrappers sharing one Python control path.
- Offline Windows/POSIX integration tests and opt-in real-device tests.

### Security and reliability

- Final Android archives are verified before replacing their destination.
- Default extraction rejects unsafe paths and unverified regular files, and
  never modifies an existing destination directory.
- Site-specific ADB settings are now kept in an ignored local configuration.
