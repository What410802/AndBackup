# v0.2.0 Release Notes

This file is release-note source material. It does not create a Git tag or a
GitHub Release.

## Highlights

- New `device-python` mode: paxck runs on the device (an uploaded Android
  ARM64 Python), so directories with many small files no longer need one ADB
  round-trip per file. Both modes keep the same two-read PAX SHA-256 semantics
  and host-side verification; a failed mode never silently falls back to the
  other.
- Zero-setup interpreter for new users: `download_device_python: true` fetches
  a pinned python-build-standalone ARM64 build on first use and caches it on
  the host (`%LOCALAPPDATA%\andbackup` / `~/.cache/andbackup`) and on the
  device (`/data/local/tmp/andbackup-pyenv`), with stamp-based reuse.
- Explicit retention control: `keep_android_env`, plus the `--clean-env` and
  `--clean-host-cache` commands.
- Friendlier `out` handling: point it at a directory and the file is named
  `<source_dir-tail><compression suffix>`; a wrong file suffix is confirmed
  interactively (or written verbatim when non-interactive).
- Log levels (`quiet`…`trace`), progress interval, and ADB transfer-rate
  reporting.
- The shipped `backup-android.example.yaml` is minimal and runnable and now
  defaults to `device-python` + `download_device_python: true`.

## Compatibility

- Python 3.12+; no third-party Python packages.
- Windows and Linux are covered by CI. macOS should work through the POSIX
  wrapper and standard-library implementation, but is not in the CI matrix.
- `xz`, `gzip`, and uncompressed tar are standard-library paths. `zstd`
  requires Python 3.14+ or a `zstd` executable on `PATH`.
- `device-python` targets Android arm64 (aarch64) devices; `host-adb` has no
  device-side runtime requirement.

## Release Checklist

- Run the full offline suite on the intended release commit.
  Verified at 0.2.0: 153 tests, 0 failures/errors, 31 skipped (opt-in
  real-device tests).
- Real-device backup test completed against the target device.
- Review `git status`, ensuring `src/backup-android.yaml` is absent from the
  index and no endpoint or local backup path is staged.
- Review `CHANGELOG.md`, `VERSION`, the MIT `LICENSE`, and both README files.
- Create an annotated `v0.2.0` tag and a GitHub Release only after these checks.
