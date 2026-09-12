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
CPython 3.13.15 (322 tests, 0 failures, 48 skipped) and the same suite on WSL
Ubuntu CPython 3.14.4 (322 tests, 0 failures, 66 skipped; the extra skips are
the Windows-only CMD/`fake-adb` suites).
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
| `paxck.py` | Magic sniffing, PAX checksums, local file changes, links, compression, verified extraction, direct-tarfile extraction, malformed archives, and CLI status. `TestVerifyScope` pins both verification layers as a contract: the no-record column mixes directories/links with recordless files (and the report splits it), a content change still fails, a deleted or planted member, changed metadata and a missing inventory **fail** (the inventory is required), `--allow-missing-inventory` keeps content checking only, and editing the inventory is caught by its own hash. `TestCreateInventory` covers the writer side (the inventory is the last member, its own record is correct, every entry matches the archive, the reserved name and duplicate paths are refused) -- kept in sync with docs/flow.md. |
| Interpreter bootstrap | Cache reuse without network, missing-interpreter errors, and local `.tar.zst` download+unpack using an offline fixture. |
| Local integration | `create | compress | verify`, system tar interoperability, restoration fidelity, and non-UTF-8 names where supported. |
| Tree listing (`tests/test_sourcetree_unit.py`) | Record parsing (7-field, 5-field, a `|` or a tab inside the path, garbage rejected), `_consume_stream` framing (newline and NUL records, a missing status trailer, remote stderr collected), the `DeviceListing` reassembly of the device-Python records (metadata+path pairing, bytes or text, a newline/`|`/tab inside a name, progress lines, `\x02U` unreadable entries, `\x03L` link targets, garbage dropped rather than fatal), the POSIX `TZ` derivation from `stat -c %y` (sign flip, half-hour zones, unusable answers), and `_TreeProgress` (silent for quiet/error, throttled by the interval, translated and tagged). |
| OUT planning / publishing (`tests/test_backup_out_unit.py`) | `OUT` planning (empty, directory, trailing separator, matching and mismatched suffix) and the target pre-flight: a missing target or an existing regular file is fine, an existing directory or a FIFO/special file is not, a read-only file is refused on Windows but accepted on POSIX; an existing target is never replaced without `force` (and the reason points at `--force`), while `--force` still does not bypass a target that genuinely cannot be written; `publish_archive` replaces on success and keeps the verified `.partial.*` file on failure; `_choose_output_path` returns a placeholder, creates missing parents, and handles interactive `y`/`n` (offer another path)/Enter (give up)/EOF (Windows reports NUL stdin as a TTY) without leaving anything behind; `_enabled` and the YAML `force:` key. |
| POSIX/CMD controllers | Fake ADB plus real `.sh` or `cmd.exe`/`.bat`, USB and TCP selection, binary bytes, temporary-output cleanup, configuration selection, `tree` (modes, owner/group, indentation, symlink targets, `--tree-out`, no archive written), `verify` (re-checking a good archive makes no adb call and leaves the file byte-identical; a modified archive exits 1 with a `[FAIL]` line; an unreadable archive exits 1), `extract` (recovers the tree, never writes the inventory member into the destination **in either extraction mode**, refuses an archive whose member was deleted and leaves no staging directory, and needs `--allow-missing-inventory` for an older archive), a first word that is not a function being reported as unknown instead of becoming a backup argument, `source_mode: host` (a host directory is archived with **no adb call at all**, the archive verifies and matches the source, a missing source directory fails immediately, and `--prune-source` is refused without touching the source), `clean env`/`clean host-cache`/`clean` (default `all`), the cleanup options as a last step (`backup --clean-host-cache` after a successful run), the migration guard for the old `--list-tree`/`--clean-*` spellings (exit code 2 with the new form), truncated-content skip-and-publish, `--prune-source` (only packed entries go, skipped entries and the source root stay, dry run deletes nothing, the remote manifest path in `device-python` mode), `device-python` upload/round-trip/cleanup without fallback, and the output pre-flight (a read-only existing target, or a path component that is a file, fails immediately with no adb call and no change to the original bytes), plus the existing-target policy (no `-f` keeps the file and fails, `-f` overwrites into a verifiable archive). |
| Device integration | Opt-in real ADB transport and source-byte checks. |
| Messages/language (`tests/test_i18n_unit.py`) | Catalog parity (keys, placeholders, non-empty), every template formats, unknown-key fallback, language-neutral tags, every key used by the source exists, no CJK literals left in the command modules, `normalize`/`resolve` precedence and fallback, the OS language outranking the process locale (the Windows UI language), `--lang` parsing, localized `--help`, and rejection of an invalid `--lang`; `i18n.os_error` preferring our own wording (the raw OS text is kept only for unknown errnos); `i18n.can_prompt` (quiet/error, non-TTY, a missing stdin, and NUL/DEVNULL not counting as a console on Windows). |
| Packed manifest / prune (`tests/test_prune_unit.py`) | Manifest parsing (`P`/`D`/`S`/`L`, non-UTF-8 paths, empty/unknown records) and the deletion plan: deepest-first, skipped entries and their parents kept, every directory kept with an incomplete listing, the source root never deleted, out-of-root paths refused, sibling prefixes not treated as children; commands use `rm -f`/`rmdir` only and quote paths correctly. |

The `tree` function's fake ADB answers the requested `stat -c` format, including
`%u`/`%g` (the Windows substitute reports `st_uid`/`st_gid`, normally 0), and
emulates the device-side one-shot pass (`find -exec stat … {} +`), the symlink
pass and the uploaded `tree_device.py` lister (running the real script against
the fake tree, then rewriting its records back to device paths), so both suites
assert that the `# listing: device-python|oneshot|per-entry` header matches the
strategy really used: `auto` goes one-shot, `FAKE_ADB_NO_ONESHOT=1` makes the
Windows substitute play a ROM that cannot do it (asserting the `[WARN]` and the
per-entry fallback), the device-Python body is asserted to be identical to the
one-shot one, and `auto` is asserted to prefer a device environment that is
already deployed. The POSIX suite additionally asserts that the per-entry
listing is identical to the one-shot one.
`tests/test_sourcetree_unit.py` covers record framing and progress throttling
without adb.

The verified-extraction tests assert that checksum failure or path traversal
does not publish a destination. Direct mode tests assert that the completion
text labels it as unverified/non-atomic and that ordinary tar archives can be
extracted for trusted interoperability.
