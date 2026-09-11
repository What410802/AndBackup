# AndBackup Testing

[Chinese version](testing.md) | [English README](../README.en.md)

## Run Offline Tests

The suite needs only the Python standard library. It covers local packing,
verification/extraction, configuration, POSIX wrappers, and real CMD batch
execution with a fake ADB.

```sh
python3 -m unittest discover -s tests -t . -v
```

```bat
py -m unittest discover -s tests -t . -v
```

Optional pytest invocation, when pytest is already installed, is:

```sh
python3 -m pytest tests -q
```

CI runs the offline suite on Ubuntu and Windows with Python 3.12, 3.13, and
3.14. Device tests skip when ADB, authorization, or an explicit target path is
absent, so they do not make CI depend on hardware.

Local release preparation has run the complete offline suite on Windows
CPython 3.13.15 and selected cross-platform tests on WSL Ubuntu CPython 3.14.4.
Python 3.12 is the minimum supported version and is not installed locally for
this preparation. Ubuntu 24.04 ships 3.12; Debian 12 ships 3.11 and needs a
separate 3.12+ interpreter.

## Run Device Tests

Choose a read-only test directory with files for the current device. The suite
does not create or upload test data. Set `ANDROBACKUP_DEVICE_DIR` explicitly:

```sh
ANDROBACKUP_ADB=/path/to/platform-tools/adb \
ANDROBACKUP_DEVICE_DIR=/storage/emulated/0/DCIM \
python3 -m unittest -v tests.test_device_integration
```

For a USB device, leave `ANDROBACKUP_ADB_SERIAL` and
`ANDROBACKUP_ADB_CONNECT` unset for a single device, or set the USB serial when
multiple devices are online. For a wireless device, use the current connection
endpoint and request a connection only when needed:

```bat
set "ANDROBACKUP_ADB_SERIAL=192.0.2.1:5555"
set "ANDROBACKUP_ADB_CONNECT=1"
set "ANDROBACKUP_DEVICE_DIR=/storage/emulated/0/DCIM"
py -m unittest -v tests.test_device_integration
```

The device suite checks authorization, directory readability, repeated
`exec-out cat` bytes versus `adb pull`, the host backup pipeline, archive
verification, and bounded device free-space change. It also checks an LF byte
path to guard against a Windows ADB/CMD line-ending regression. A selected
serial is cached for the test run so an mDNS and TCP/USB ADB listing cannot
cause `more than one device/emulator` failures.

## Coverage

| Area | Tests |
|---|---|
| `paxck.py` | Magic sniffing, PAX checksums, local file changes, links, compression, verified extraction, direct-tarfile extraction, malformed archives, and CLI status. |
| Interpreter bootstrap | Cache reuse without network, missing-interpreter errors, and local `.tar.zst` download+unpack using an offline fixture. |
| Local integration | `create | compress | verify`, system tar interoperability, restoration fidelity, and non-UTF-8 names where supported. |
| POSIX/CMD controllers | Fake ADB plus real `.sh` or `cmd.exe`/`.bat`, USB and TCP selection, binary bytes, temporary-output cleanup, configuration selection, `--list-tree` (modes, owner/group, indentation, symlink targets, `--tree-out`, no archive written), truncated-content skip-and-publish, `--prune-source` (only packed entries go, skipped entries and the source root stay, dry run deletes nothing, the remote manifest path in `device-python` mode), and `device-python` upload/round-trip/cleanup without fallback. |
| Device integration | Opt-in real ADB transport and source-byte checks. |
| Messages/language (`tests/test_i18n_unit.py`) | Catalog parity (keys, placeholders, non-empty), every template formats, unknown-key fallback, language-neutral tags, every key used by the source exists, no CJK literals left in the command modules, `normalize`/`resolve` precedence and fallback, `--lang` parsing, localized `--help`, and rejection of an invalid `--lang`. |
| Packed manifest / prune (`tests/test_prune_unit.py`) | Manifest parsing (`P`/`D`/`S`/`L`, non-UTF-8 paths, empty/unknown records) and the deletion plan: deepest-first, skipped entries and their parents kept, every directory kept with an incomplete listing, the source root never deleted, out-of-root paths refused, sibling prefixes not treated as children; commands use `rm -f`/`rmdir` only and quote paths correctly. |

The `--list-tree` fake ADB answers the requested `stat -c` format, including
`%u`/`%g` (the Windows substitute reports `st_uid`/`st_gid`, normally 0), so the
tree tests cover the same code path on both launchers.

The verified-extraction tests assert that checksum failure or path traversal
does not publish a destination. Direct mode tests assert that the completion
text labels it as unverified/non-atomic and that ordinary tar archives can be
extracted for trusted interoperability.
