# Changelog

All notable changes to this project are recorded in this file.

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
