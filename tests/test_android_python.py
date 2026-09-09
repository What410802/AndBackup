#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for the device-python interpreter bootstrap.

Covers cache reuse (no network), the “missing interpreter” error contract, and
download+unpack using a committed local ``.tar.zst`` fixture so no network or
third-party package is needed.  ``cache_root`` is patched to a temp directory
so the tests never touch the real user cache.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'fixtures', 'python-mock-aarch64.tar.zst')


def load_module():
    sys.path.insert(0, T.SRC_DIR)
    try:
        spec = importlib.util.spec_from_file_location(
            'android_python_under_test', os.path.join(T.SRC_DIR, 'android_python.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


MOD = load_module()


class TestResolveExisting(T.BaseCase):
    def test_existing_interpreter_file_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            interp = os.path.join(tmp, 'python3.14')
            with open(interp, 'wb') as fh:
                fh.write(b'# mock\n')
            self.assertEqual(MOD.resolve(interp), interp)

    def test_existing_prefix_dir_is_returned_without_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = os.path.join(tmp, 'prefix')
            os.makedirs(os.path.join(prefix, 'bin'))
            with open(os.path.join(prefix, 'bin', 'python3.14'), 'wb') as fh:
                fh.write(b'# mock\n')
            # A non-resolvable URL proves no network fetch is attempted.
            self.assertEqual(MOD.resolve(prefix, True, url='http://127.0.0.1:1/nope'),
                             prefix)


class TestResolveErrors(T.BaseCase):
    def test_missing_explicit_path_without_download_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, 'does-not-exist')
            with self.assertRaises(RuntimeError):
                MOD.resolve(missing, False)

    def test_no_device_python_without_download_is_error(self):
        with self.assertRaises(RuntimeError):
            MOD.resolve('', False)


class TestBootstrapDownload(T.BaseCase):
    def _patched_cache(self, tmp):
        return mock.patch.object(MOD, 'cache_root', return_value=tmp)

    def test_local_url_downloads_and_unpacks_to_default_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patched_cache(tmp):
                prefix = MOD.resolve('', True, url=FIXTURE, quiet=True)
                self.assertTrue(os.path.isfile(os.path.join(prefix, 'bin', 'python3.14')))
                self.assertTrue(os.path.isfile(
                    os.path.join(prefix, 'lib', 'python3.14', 'os.py')))
                # Second call reuses the cache without re-extracting.
                again = MOD.resolve('', True, url=FIXTURE, quiet=True)
                self.assertEqual(again, prefix)

    def test_missing_target_path_is_used_as_download_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'android-python')
            got = MOD.resolve(target, True, url=FIXTURE, quiet=True)
            self.assertEqual(os.path.abspath(got), os.path.abspath(target))
            self.assertTrue(os.path.isfile(
                os.path.join(target, 'bin', 'python3.14')))

    def test_local_url_saves_archive_into_cache_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patched_cache(tmp):
                MOD.resolve('', True, url=FIXTURE, quiet=True)
                downloads = os.path.join(tmp, 'downloads')
                self.assertTrue(any(
                    name.endswith('.tar.zst')
                    for name in os.listdir(downloads)))


class TestExtract(T.BaseCase):
    def test_extract_fixture_yields_valid_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = os.path.join(tmp, 'prefix')
            MOD._extract_tar_zst(FIXTURE, prefix)
            self.assertTrue(MOD._is_prefix(prefix))
            # Symlink aliases (python/python3) and share/ are not extracted.
            self.assertEqual(sorted(os.listdir(os.path.join(prefix, 'bin'))),
                             ['python3.14'])
            self.assertFalse(os.path.exists(os.path.join(prefix, 'share')))
            self.assertTrue(os.path.isfile(
                os.path.join(prefix, 'lib', 'python3.14', 'site.py')))
            self.assertTrue(os.path.isfile(
                os.path.join(prefix, 'lib', 'python3.14', 'os.py')))


if __name__ == '__main__':
    unittest.main()
