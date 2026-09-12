# v0.3.0 Release Notes

This file is release-note source material. It does not create a Git tag or a
GitHub Release.

## Highlights

- **Command lines now name the function**: `backup.py backup|tree|verify|extract|clean`,
  `paxck.py create|compress|verify|extract` and `adb_source.py pack`. A command
  line says what this run does, and an old option-style spelling
  (`--list-tree`, `--clean-env`, `--clean-host-cache`) is refused with exit code
  `2` and the new form instead of being reinterpreted. Running the launcher with
  no function still backs up, so the double-click wrappers are unchanged.
- **`tree` became fast enough for real device trees.** The default now lists on
  the device: either with the uploaded Python (`device-python`, the interpreter
  is reused when it is already deployed) or with one batched shell pass
  (`find … -exec stat … {} +`). A directory that took 30 minutes per 20 000
  entries now finishes in seconds: measured 70 394 entries in ~6 s
  (`Android/data/com.tencent.mobileqq`) and 5 093 entries in ~1.6 s (DCIM,
  byte-identical to the per-entry output). A non-zero `find` exit code -- normal
  on Android, where an unreadable subdirectory makes toybox return 127 -- no
  longer throws the partial result away.
- **Archives gained a member inventory**, so verification now also proves that
  nothing was lost: every archive ends with a `PAXCK.manifest` member listing
  each member's type/mode/size/mtime/uid/gid, path and link target, protected by
  its own PAX SHA-256. `verify` and `extract` compare it with the archive in both
  directions, so a deleted member (even together with its record), a planted
  member, changed metadata and duplicate paths all fail. **This is a format
  change**: archives made with 0.2.0 need `--allow-missing-inventory` to be
  verified or extracted. The remaining limit is documented: there is no key and
  no signature, so a whole-archive rebuild is internally consistent.
- **`verify` and `extract` are now functions of the user-facing launcher**
  (`backup.py verify ARCHIVE`, `backup.py extract ARCHIVE -C DIR`), running
  exactly the commands the pipeline uses on its own output. Verification is
  purely local; extraction verifies, compares the member set and then publishes
  atomically into a destination that must not exist. `--direct-tarfile` keeps the
  trusted-archive `tarfile` semantics.
- **Host-directory backups** (`source_mode: host`): the same pipeline
  (PAX tar with per-file SHA-256, verify, atomic publish, overwrite guard) now
  works on a local directory with no ADB involved at all.
- **Calmer defaults**: the output target is validated *before* any transfer, an
  existing archive is never silently overwritten (interactive confirm,
  `-f`/`--force`, or a `force: true` key), and one run prints one language
  (`--lang`, `ANDROBACKUP_LANG`, or the OS language).

## Compatibility

- Python 3.12+; no third-party Python packages.
- Windows and Linux are covered by CI (3.12, 3.13, 3.14). macOS should work
  through the POSIX wrapper and the standard-library implementation, but is not
  in the CI matrix.
- `xz`, `gzip`, and uncompressed tar are standard-library paths. `zstd`
  requires Python 3.14+ or a `zstd` executable on `PATH`.
- `device-python` targets Android arm64 (aarch64) devices; `host-adb` and
  `source_mode: host` have no device-side runtime requirement.
- **Breaking, command line**: functions are no longer options (see the table in
  `docs/configuration.md`); `backup.py --list-tree`, `backup.py --clean-env` and
  `backup.py --clean-host-cache` now fail with exit code `2`.
- **Breaking, archive format**: 0.2.0 archives have no member inventory, so
  `verify`/`extract` refuse them unless `--allow-missing-inventory` is passed.
  Re-running a backup with this version produces an archive with the inventory.
- **Breaking, defaults**: an existing `out` file is no longer overwritten
  without `-f`/`--force` (or an interactive confirmation).

## Release Checklist

- Run the full offline suite on the intended release commit.
  Verified at 0.3.0: 322 tests, 0 failures/errors, 48 skipped on Windows
  CPython 3.13.15 and 66 skipped on WSL Ubuntu CPython 3.14.4 (the extra skips
  are the Windows-only CMD/`fake-adb` suites).
- Real-device checks completed against the target device (Huawei ALI-AN00,
  Android 15): `tree` on DCIM and on `Android/data/com.tencent.mobileqq`,
  `backup` in `device-python` mode, `verify` (member inventory matched 4/4) and
  `extract` round-trip; the temporary device directory was removed afterwards.
- CI green on the release commit (GitHub Actions matrix: ubuntu/windows ×
  Python 3.12/3.13/3.14).
- Review `git status`, ensuring `src/backup-android.yaml` is absent from the
  index and no endpoint or local backup path is staged.
- Review `CHANGELOG.md`, `VERSION`, the MIT `LICENSE`, and both README files.
- Create an annotated `v0.3.0` tag and a GitHub Release only after these checks.
