# v0.1.0 Release Notes Draft

This file is release-note source material. It does not create a Git tag or a
GitHub Release.

## Highlights

- Backs up ADB-shell-readable Android external-storage directories over USB or
  wireless TCP ADB, without placing executables or archive data on the device.
- Provides `paxck.py` as an independent local PAX tar, SHA-256 verification,
  compression, and extraction tool.
- Uses a single Python implementation behind both the Windows CMD and POSIX
  wrappers, with byte-safe ADB streaming on Windows.
- Adds default verified/atomic extraction and an explicit `--direct-tarfile`
  interoperability mode for trusted archives.

## Compatibility

- Python 3.12+; no third-party Python packages.
- Windows and Linux are covered by CI. macOS should work through the POSIX
  wrapper and standard-library implementation, but is not in the initial CI
  matrix.
- `xz`, `gzip`, and uncompressed tar are standard-library paths. `zstd`
  requires Python 3.14+ or a `zstd` executable on `PATH`.

## Release Checklist

- Run the full offline suite on the intended release commit.
- Review `git status`, ensuring `src/backup-android.yaml` is absent from the
  index and no endpoint or local backup path is staged.
- Review `CHANGELOG.md`, `VERSION`, the MIT `LICENSE`, and both README files.
- Create an annotated `v0.1.0` tag and a GitHub Release only after these checks.
