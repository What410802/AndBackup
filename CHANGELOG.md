# Changelog

All notable changes to this project are recorded in this file.

## [0.3.0] - 2026-09-12

### Added

- **A trailing member inventory (`PAXCK.manifest`) ends every archive**, which
  closes the set-membership hole: per-file checksums prove that the bytes inside
  are the bytes that were read, but they cannot notice a whole member that was
  deleted (with its record) or planted after packing. The packer now appends one
  ordinary regular member listing every other member's type/mode/size/mtime/
  uid/gid, path, and link target, NUL-framed so a tab or newline in a path is
  safe. Being a regular file, it carries its own `PAXCK.checksum.sha256`, so
  editing it is caught by its own hash. `verify` and `extract` compare it with
  the archive **in both directions**: missing, extra, changed metadata/type/link
  target, and duplicate paths all fail.

  The inventory is **required by default**, so `verify`/`extract` refuse an
  archive made by an older version with a hint to pass
  `--allow-missing-inventory`, which keeps content checking only. Both of our
  extraction modes treat it as bookkeeping and do not write it into the
  destination; other tar tools (7-Zip, WinRAR, Explorer, GNU tar) will list it
  and extract it as a file of that name, which is unavoidable for a regular
  member -- and it has to be a regular member to carry its own checksum.
  Honest limits,
  documented in `docs/flow.md`: there is no key and no signature, so a
  determined attacker who rebuilds the whole archive stays self-consistent, and
  the inventory writes every member path into the archive (~`path length + 60`
  bytes per member, less once compressed).
- `backup.py extract ARCHIVE -C DIR [--direct-tarfile]`: recovery as a first
  class function of the launcher, running the same `paxck.py extract` the
  tool set already had (per-file verification plus the inventory comparison,
  published atomically into a destination that must not exist). `--direct`
  keeps the trusted-archive tarfile semantics.
- `backup.py verify` and `backup.py extract` gained
  `--allow-missing-inventory` for archives made before the inventory existed.
- `backup.py` now reports a first word that is not a function (`frobnicate`) as
  an unknown function, listing the real ones. It used to be passed to the
  default `backup` function, so `backup-android.bat extract --help` silently
  printed the *backup* help.
- `source_mode: host` (or `SOURCE_MODE=host`): back up a directory on the host
  itself, with no ADB involved at all -- no `adb` binary, no `serial`
  resolution, no `get-state` (those keys are simply ignored in this mode).
  `source_dir` is then a host path (a relative one resolves against the launch
  directory) and must be an existing directory, checked before anything is
  created. The pipeline is otherwise the same one: `paxck.py create` writes the
  PAX tar with per-file `PAXCK.checksum.sha256`, the compressor runs, the
  archive is verified and published atomically, and the destination pre-flight
  and overwrite guard (`-f`/`--force`) apply as usual -- so a host backup is
  indistinguishable from a device backup and `backup.py verify` re-checks it
  directly. `--prune-source` is refused (it deletes through the device shell),
  and `--progress-interval`/`--show-rate` report the bytes written into the
  archive (after compression) instead of transferred device payload.
- `backup.py verify [ARCHIVE]` (or `-i ARCHIVE`): re-check an existing archive
  with the same command the backup pipeline runs on the archive it just
  produced, so a manual check and the pipeline's own post-transfer check are
  literally the same code and print the same diagnostics. It is purely local
  -- no device, no configuration (it does not even accept `--config`, because
  there is nothing to read) -- it never writes or modifies anything, it
  accepts stdin when no path is given, `--log-level quiet|error` keeps only the
  fatal lines, and it exits `1` when any record fails to verify.
- `backup.py tree --tree-mode device-python` (and, when the environment is
  already deployed, the default `auto`): the device lists its own tree with the
  uploaded `tree_device.py`, which walks it with `os.scandir` + `os.lstat`,
  **keeps the whole listing in memory**, reports its own progress while walking
  and hands everything over in **one write**. Measured on a real device: a
  70394-entry `Android/data/com.tencent.mobileqq` directory lists in ~6 seconds,
  where the per-entry fallback needs hours, and it gets metadata for all 70394
  entries where the shell one-shot can only `stat` 6803 of them (scoped storage
  denies the rest). Records are NUL-framed with the metadata and the path in two
  separate records, so a newline, `|` or tab inside a name cannot confuse them.
  `auto` uses the deployed environment when there is one (two cheap probes; it
  never downloads an interpreter just to list) and otherwise the shell one-shot.
  The device interpreter's timestamps are aligned with `stat` by probing the
  device UTC offset once (`stat -c %y /`) and passing it as `TZ=UTC-8`, since
  `adb shell` exports no `TZ` and Android has no `zoneinfo` for musl to read.
- `backup.py tree` now enumerates the device directory in **one device-side
  pass** by default: the device runs `find ROOT -exec stat -c '…|%n' -- {} +`
  (plus one `find -type l -exec sh -c …` pass for symlink targets) and the host
  streams the records back, so a tree of N entries costs a handful of adb round
  trips instead of N. Measured on a real device: 5093 entries went from ~17
  minutes to ~1.6 seconds. `--tree-mode {auto,device-python,oneshot,per-entry}`
  (or the `tree_mode`/`TREE_MODE` setting) picks the strategy. The listing
  header now records it as `# listing: device-python|oneshot|per-entry`.
- `backup.py tree` reports progress on stderr at `progress_interval`: the
  enumeration phase shows the bytes and entries received, the one-shot phase
  shows `received/total`, the device-Python phase shows the entries the script
  itself has done, and the per-entry fallback shows `collected/total`.
  The listing itself (stdout or `--tree-out`) stays free of progress text.
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

### Changed

- Archives grew by one member (the inventory) and are no longer byte-identical
  to those made by previous versions: `verify` on an archive without the
  inventory now fails unless `--allow-missing-inventory` is given, and
  `extract` refuses it for the same reason (an incomplete recovery must not look
  successful). Regenerating an archive with this version adds the inventory.
- `verify`'s closing report now splits its "without a record" column, because
  that number is derived while reading and always mixed two different things:
  `no record 3 (2 directories/links, 1 regular file)`. The figure counts
  non-regular members (directories, symlinks) together with regular files that
  carry no `PAXCK.checksum.sha256`, and nothing in the archive stores it --
  only the per-file records exist. `docs/flow.md`/`docs/flow.en.md` now spell
  out how the number is computed and what both layers really protect: content
  (per-file hash, broken/truncated stream) plus, through the trailing inventory
  member, set membership. The remaining limit is stated as well: there is no key
  and no signature, so an attacker who rebuilds the whole archive stays
  self-consistent, and `--allow-missing-inventory` gives the membership layer up
  for archives made before it existed.
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

- `backup.py tree` no longer drops to the very slow per-entry fallback (one adb
  round trip per entry) because of a **normal** non-zero device exit code: on
  Android, `find` reports the failures of the batched `stat` calls, and toybox
  returns 127 when a subdirectory is unreadable (routine under
  `Android/data/*`), even though every readable entry was listed. A non-zero
  exit now only matters when it produced no record at all; partial results are
  kept, and the affected entries simply show as `[?]` (the header still reports
  the exit code). Likewise, unparsable records are counted and warned about
  instead of aborting the pass.
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
