#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for paxck.LiveLine (the non-scrolling progress line)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

paxck = T.load_paxck()


class _FakeStream:
    def __init__(self, is_tty):
        self._tty = is_tty
        self.writes = []

    def isatty(self):
        return self._tty

    def write(self, text):
        self.writes.append(text)
        return len(text)

    def flush(self):
        pass


class TestLiveLine(unittest.TestCase):
    def test_tty_overwrites_one_line_and_clears(self):
        stream = _FakeStream(True)
        line = paxck.LiveLine(stream)
        line.update('alpha')
        line.update('beta')
        self.assertEqual(stream.writes, ['\r\x1b[Kalpha', '\r\x1b[Kbeta'])
        line.clear()
        self.assertEqual(stream.writes[-1], '\r\x1b[K')

    def test_non_tty_writes_plain_newline_lines(self):
        stream = _FakeStream(False)
        line = paxck.LiveLine(stream)
        line.update('alpha')
        line.update('beta')
        self.assertEqual(stream.writes, ['alpha\n', 'beta\n'])
        line.clear()
        self.assertEqual(len(stream.writes), 2)


if __name__ == '__main__':
    unittest.main()
