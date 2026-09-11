#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for the packed-entry manifest and the prune planner."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

sys.path.insert(0, T.SRC_DIR)

import prune  # noqa: E402


def manifest(*records):
    blob = b''
    for record in records:
        blob += record.encode('utf-8') + b'\0'
    return blob


class TestManifestParsing(unittest.TestCase):
    def test_records_are_split_by_status(self):
        packed, dirs, skipped, listing_ok = prune.parse_manifest(manifest(
            'P:/root/a.txt', 'D:/root/sub', 'S:/root/denied.pdf'))
        self.assertEqual(packed, ['/root/a.txt'])
        self.assertEqual(dirs, ['/root/sub'])
        self.assertEqual(skipped, ['/root/denied.pdf'])
        self.assertTrue(listing_ok)

    def test_listing_marker_clears_the_ok_flag(self):
        _, _, _, listing_ok = prune.parse_manifest(manifest('L:1', 'P:/root/a'))
        self.assertFalse(listing_ok)

    def test_empty_and_unknown_records_are_ignored(self):
        self.assertEqual(prune.parse_manifest(b''), ([], [], [], True))
        self.assertEqual(prune.parse_manifest(b'\0\0garbage\0'),
                         ([], [], [], True))

    def test_non_utf8_paths_survive(self):
        blob = b'P:/root/\xff\xfe.bin\0'
        packed, _, _, _ = prune.parse_manifest(blob)
        self.assertEqual(len(packed), 1)
        self.assertTrue(packed[0].startswith('/root/'))


class TestPlan(unittest.TestCase):
    ROOT = '/storage/emulated/0/qq'

    def _plan(self, packed=(), dirs=(), skipped=(), listing_ok=True):
        return prune.build_plan(list(packed), list(dirs), list(skipped),
                                listing_ok, self.ROOT)

    def test_packed_entries_are_deleted_deepest_first(self):
        to_delete, kept = self._plan(
            packed=[self.ROOT + '/a.txt', self.ROOT + '/sub/b.txt'],
            dirs=[self.ROOT + '/sub'])
        self.assertEqual(to_delete,
                         [self.ROOT + '/sub/b.txt',
                          self.ROOT + '/a.txt',
                          self.ROOT + '/sub'])
        self.assertEqual(kept, 0)

    def test_skipped_entries_and_their_parents_are_kept(self):
        to_delete, kept = self._plan(
            packed=[self.ROOT + '/a.txt', self.ROOT + '/sub/b.txt'],
            dirs=[self.ROOT + '/sub'],
            skipped=[self.ROOT + '/sub/locked.pdf'])
        # The packed sibling is still deleted; only the unlocked directory and
        # the skipped file stay behind.
        self.assertEqual(to_delete,
                         [self.ROOT + '/sub/b.txt', self.ROOT + '/a.txt'])
        self.assertEqual(kept, 2)          # the skipped file and its directory

    def test_incomplete_listing_keeps_every_directory(self):
        to_delete, kept = self._plan(
            packed=[self.ROOT + '/a.txt'],
            dirs=[self.ROOT + '/sub'],
            listing_ok=False)
        self.assertEqual(to_delete, [self.ROOT + '/a.txt'])
        self.assertEqual(kept, 1)

    def test_root_is_never_deleted(self):
        to_delete, kept = self._plan(packed=[self.ROOT], dirs=[self.ROOT])
        self.assertEqual(to_delete, [])
        self.assertEqual(kept, 0)

    def test_paths_outside_the_root_are_refused(self):
        to_delete, kept = self._plan(packed=['/storage/emulated/0/other/x.txt'],
                                     dirs=['/data/local/tmp/other'])
        self.assertEqual(to_delete, [])
        self.assertEqual(kept, 2)

    def test_sibling_prefix_is_not_treated_as_a_child(self):
        to_delete, _ = self._plan(packed=[self.ROOT + '-backup/a.txt'])
        self.assertEqual(to_delete, [])

    def test_a_skipped_entry_elsewhere_does_not_block_unrelated_dirs(self):
        to_delete, _ = self._plan(
            packed=[self.ROOT + '/keep/a.txt'],
            dirs=[self.ROOT + '/keep'],
            skipped=[self.ROOT + '/other/locked.txt'])
        self.assertEqual(to_delete, [self.ROOT + '/keep/a.txt',
                                     self.ROOT + '/keep'])


class TestCommandBuilding(unittest.TestCase):
    def test_paths_are_quoted_for_the_device_shell(self):
        import shlex
        paths = ["/storage/emulated/0/a b'c.txt",
                 '/storage/emulated/0/测试 目录/x.bin']
        command = prune.remove_command(paths)
        self.assertTrue(command.startswith('rm -f -- '))
        # The command must survive a POSIX shell round trip unchanged.
        self.assertEqual(shlex.split(command)[3:], paths)

    def test_directories_are_removed_with_rmdir_not_rm(self):
        import shlex
        directory = '/storage/emulated/0/测试 目录'
        command = prune.rmdir_command([directory])
        self.assertTrue(command.startswith('rmdir -- '))
        self.assertNotIn('rm -r', command)
        self.assertEqual(shlex.split(command)[2:], [directory])

    def test_split_plan_separates_files_from_directories(self):
        root = '/root'
        plan = [root + '/sub/b.txt', root + '/a.txt', root + '/sub']
        files, directories = prune.split_plan(plan, [root + '/sub'])
        self.assertEqual(files, [root + '/sub/b.txt', root + '/a.txt'])
        self.assertEqual(directories, [root + '/sub'])
        # A trailing slash on the recorded directory must not confuse the split.
        files, directories = prune.split_plan(plan, [root + '/sub/'])
        self.assertEqual(directories, [root + '/sub'])

    def test_chunking_keeps_the_order(self):
        paths = [f'/root/{index}' for index in range(5)]
        batches = list(prune.chunked(paths, size=2))
        self.assertEqual([len(batch) for batch in batches], [2, 2, 1])
        self.assertEqual([item for batch in batches for item in batch], paths)


if __name__ == '__main__':
    unittest.main()
