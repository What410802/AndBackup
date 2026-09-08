# Changelog

All notable changes to this project are recorded in this file.

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
