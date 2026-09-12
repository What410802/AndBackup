#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for the message catalog and language selection (``src/i18n.py``)."""
import errno
import io
import os
import re
import string
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testsupport as T  # noqa: E402

sys.path.insert(0, T.SRC_DIR)

import i18n  # noqa: E402


def _fields(template):
    """Placeholder names used by one catalog template (``{name}``, ``{n:03d}``)."""
    return sorted({name.split(':')[0].split('.')[0].strip('[]')
                   for _, name, _, _ in string.Formatter().parse(template)
                   if name})


class TestCatalog(unittest.TestCase):
    def test_every_language_defines_the_same_keys(self):
        keys = {lang: set(i18n.MESSAGES[lang]) for lang in i18n.LANGUAGES}
        base = keys[i18n.LANGUAGES[0]]
        for lang in i18n.LANGUAGES[1:]:
            self.assertEqual(base - keys[lang], set(),
                             f'{lang} is missing keys')
            self.assertEqual(keys[lang] - base, set(),
                             f'{lang} defines unknown keys')

    def test_templates_are_non_empty_and_use_matching_placeholders(self):
        base = i18n.MESSAGES[i18n.LANGUAGES[0]]
        for key, template in base.items():
            self.assertTrue(template, key)
            for lang in i18n.LANGUAGES[1:]:
                other = i18n.MESSAGES[lang][key]
                self.assertTrue(other, f'{lang}:{key}')
                self.assertEqual(_fields(template), _fields(other),
                                 f'placeholder mismatch for {key}')

    def test_unknown_key_returns_itself(self):
        self.assertEqual(i18n.t('no.such.key'), 'no.such.key')

    def test_every_template_formats_with_its_placeholders(self):
        """Catches parameter/placeholder collisions such as ``{key}``."""
        for lang in i18n.LANGUAGES:
            i18n.set_language(lang)
            for message_key, template in i18n.MESSAGES[lang].items():
                values = {field: 'x' for field in _fields(template)}
                text = i18n.t(message_key, **values)
                self.assertNotEqual(text, message_key, message_key)
        i18n.set_language(None)

    def test_checksum_header_placeholder_does_not_collide(self):
        for lang in i18n.LANGUAGES:
            i18n.set_language(lang)
            text = i18n.t('paxck.verify.no_checksum_records', count=2,
                          key=i18n.MESSAGES[lang]['paxck.cli.create_help'])
            self.assertIn(i18n.MESSAGES[lang]['paxck.cli.create_help'], text)
        i18n.set_language(None)

    def test_missing_translation_falls_back_to_the_default_language(self):
        original = dict(i18n.MESSAGES['en'])
        try:
            i18n.set_language('en')
            i18n.MESSAGES['en'].pop('paxck.cli.create_help')
            self.assertEqual(i18n.t('paxck.cli.create_help'),
                             i18n.MESSAGES['zh']['paxck.cli.create_help'])
        finally:
            i18n.MESSAGES['en'].clear()
            i18n.MESSAGES['en'].update(original)
            i18n.set_language(None)

    def test_tags_are_language_neutral(self):
        self.assertEqual(i18n.tag('error'), '[ERROR]')
        self.assertEqual(i18n.tag('warn'), '[WARN]')
        self.assertEqual(i18n.tag('done'), '[DONE]')
        for template in i18n.TAGS.values():
            self.assertTrue(template.startswith('[')
                            and template.endswith(']'), template)
        with self.assertRaises(ValueError):
            i18n.tag('nope')

    def test_every_key_used_by_the_source_exists(self):
        """Guard against typos: the code and the catalog must agree."""
        pattern = re.compile(r"i18n\.(?:t|tag)\(\s*'([^']+)'")
        used = set()
        for name in ('paxck.py', 'adb_source.py', 'backup.py',
                     'android_python.py'):
            with open(os.path.join(T.SRC_DIR, name), encoding='utf-8') as fh:
                used.update(pattern.findall(fh.read()))
        self.assertTrue(used)
        for message_key in sorted(used):
            if message_key in i18n.TAGS:
                continue
            self.assertIn(message_key, i18n.MESSAGES['zh'],
                          f'missing catalog key {message_key}')

    def test_no_user_facing_literals_remain_in_the_commands(self):
        """CJK literals belong in the catalog, not in the command modules."""
        import ast
        cjk = re.compile('[\\u3400-\\u9fff]')
        for name in ('paxck.py', 'adb_source.py', 'backup.py',
                     'android_python.py'):
            with open(os.path.join(T.SRC_DIR, name), encoding='utf-8') as fh:
                tree = ast.parse(fh.read())
            parents = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            leftovers = [node.lineno for node in ast.walk(tree)
                         if isinstance(node, ast.Constant)
                         and isinstance(node.value, str)
                         and cjk.search(node.value)
                         and not isinstance(parents.get(node), ast.Expr)]
            self.assertEqual(leftovers, [], f'{name} still has CJK literals')


class TestLanguageSelection(unittest.TestCase):
    def setUp(self):
        i18n.set_language(None)

    def tearDown(self):
        i18n.set_language(None)

    def test_normalize_accepts_locale_spellings(self):
        for value in ('zh', 'zh_CN', 'zh-CN.UTF-8', 'Chinese (Simplified)_China',
                      'zh_Hans', 'ZH'):
            self.assertEqual(i18n.normalize(value), 'zh', value)
        for value in ('en', 'en_US.UTF-8', 'English_United States', 'C', 'POSIX'):
            self.assertEqual(i18n.normalize(value), 'en', value)
        for value in ('', None, 'auto', 'de_DE.UTF-8', 'fr'):
            self.assertIsNone(i18n.normalize(value), value)

    def test_resolve_prefers_cli_then_env_then_locale(self):
        env = {'ANDROBACKUP_LANG': 'zh', 'LANG': 'en_US.UTF-8'}
        self.assertEqual(i18n.resolve(cli='en', env=env), 'en')
        self.assertEqual(i18n.resolve(cli='auto', env=env), 'zh')
        self.assertEqual(i18n.resolve(cli=None, env=env), 'zh')
        self.assertEqual(i18n.resolve(cli=None, env={'LANG': 'en_GB.UTF-8'}), 'en')
        self.assertEqual(i18n.resolve(cli=None, env={'LC_ALL': 'zh_CN.UTF-8'}),
                         'zh')
        self.assertEqual(i18n.resolve(cli=None, env={'LANGUAGE': 'zh_CN:en'}),
                         'zh')

    def test_resolve_falls_back_to_english(self):
        from unittest import mock
        # No locale env, no OS language, and no process locale left to read.
        with mock.patch.object(i18n, '_os_language_name', return_value=None):
            with mock.patch.object(i18n.locale, 'getlocale', return_value=(None, None)):
                with mock.patch.object(i18n.locale, 'getdefaultlocale',
                                       return_value=(None, None)):
                    self.assertEqual(i18n.resolve(cli=None, env={}), 'en')

    def test_resolve_uses_the_os_locale(self):
        from unittest import mock
        with mock.patch.object(i18n, '_os_language_name', return_value=None):
            with mock.patch.object(i18n.locale, 'getlocale',
                                   return_value=('Chinese (Simplified)_China',
                                                 'cp936')):
                self.assertEqual(i18n.resolve(cli=None, env={}), 'zh')

    def test_os_language_wins_over_the_process_locale(self):
        """Windows under UTF-8 mode reports an English process locale.

        The OS language is what the system writes into error strings, so it
        has to win; otherwise one message mixes two languages.
        """
        from unittest import mock
        with mock.patch.object(i18n, '_os_language_name',
                               return_value='zh_CN'):
            with mock.patch.object(i18n.locale, 'getlocale',
                                   return_value=('English_United States',
                                                 'utf8')):
                self.assertEqual(i18n._detect(env={}), 'zh')
        # An explicit environment still outranks the OS language.
        with mock.patch.object(i18n, '_os_language_name',
                               return_value='zh_CN'):
            self.assertEqual(i18n._detect(env={'LANG': 'en_US.UTF-8'}), 'en')

    def test_os_error_prefers_the_catalog_text(self):
        """OS error strings must not leak a second language into a message."""
        i18n.set_language('en')
        existing = OSError(errno.EEXIST, '当文件已存在时，无法创建该文件。')
        self.assertEqual(i18n.os_error(existing), 'the file or directory already exists')
        i18n.set_language('zh')
        self.assertEqual(i18n.os_error(existing), '文件或目录已存在')
        denied = PermissionError(errno.EACCES, 'Access is denied.')
        self.assertNotIn('Access is denied', i18n.os_error(denied))

    def test_os_error_keeps_unknown_text(self):
        unknown = OSError(99999, 'something exotic')
        self.assertEqual(i18n.os_error(unknown), 'something exotic')
        # Exceptions that are not OS errors keep their own message.
        self.assertEqual(i18n.os_error(RuntimeError('boom')), 'boom')
        self.assertEqual(i18n.os_error(OSError('plain message')),
                         'plain message')

    def test_set_language_rejects_unknown_values(self):
        with self.assertRaises(ValueError):
            i18n.set_language('de')
        self.assertIn(i18n.language(), i18n.LANGUAGES)

    def test_set_language_pins_and_auto_resumes_detection(self):
        i18n.set_language('zh')
        self.assertEqual(i18n.language(), 'zh')
        self.assertEqual(i18n.t('paxck.cli.create_help'), '打包本机目录到 stdout')
        i18n.set_language('en')
        self.assertEqual(i18n.t('paxck.cli.create_help'),
                         'pack a local directory to stdout')
        i18n.set_language(i18n.AUTO)
        self.assertIn(i18n.language(), i18n.LANGUAGES)

    def test_export_sets_the_environment_variable(self):
        i18n.set_language('zh')
        env = {}
        self.assertIs(i18n.export(env), env)
        self.assertEqual(env[i18n.ENV_VAR], 'zh')

    def test_prescan_lang_reads_raw_argv(self):
        self.assertEqual(i18n.prescan_lang(['--lang', 'en']), 'en')
        self.assertEqual(i18n.prescan_lang(['--lang=en']), 'en')
        self.assertEqual(i18n.prescan_lang(['create', '/tmp']), None)
        self.assertEqual(i18n.prescan_lang(['--lang']), None)


class _FakeTty(io.StringIO):
    """A stdin that claims to be a terminal but has no OS handle."""

    def isatty(self):
        return True


class TestCanPrompt(unittest.TestCase):
    """Prompts must only be shown when a console can answer them."""

    def test_quiet_and_error_never_prompt(self):
        with mock.patch.object(sys, 'stdin', _FakeTty()):
            self.assertFalse(i18n.can_prompt('quiet'))
            self.assertFalse(i18n.can_prompt('error'))
            self.assertTrue(i18n.can_prompt('info'))

    def test_non_tty_and_missing_stdin_never_prompt(self):
        with mock.patch.object(sys, 'stdin', io.StringIO('')):
            self.assertFalse(i18n.can_prompt('info'))
        with mock.patch.object(sys, 'stdin', None):
            self.assertFalse(i18n.can_prompt('info'))

    @unittest.skipUnless(os.name == 'nt', 'Windows reports NUL as a TTY')
    def test_nul_stdin_is_not_a_console_on_windows(self):
        # The trap this probe exists for: `isatty()` is True for NUL/DEVNULL,
        # so `input()` would print a question nobody can answer.
        with open(os.devnull, 'rb') as devnull:
            self.assertTrue(devnull.isatty())
            with mock.patch.object(sys, 'stdin', devnull):
                self.assertFalse(i18n.can_prompt('info'))


class TestLocalizedCommands(unittest.TestCase):
    """The CLIs must honour --lang/ANDROBACKUP_LANG end to end."""

    def _run(self, args, lang=None):
        """Run paxck with a fixed environment language unless overridden."""
        env = dict(os.environ)
        env[i18n.ENV_VAR] = 'zh'
        if lang:
            env[lang[0]] = lang[1]
        import subprocess
        proc = subprocess.run([sys.executable, T.PAXCK] + list(args), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return proc.returncode, proc.stdout.decode('utf-8', 'replace')

    def test_help_follows_lang_flag(self):
        rc, text = self._run(['--help', '--lang', 'en'])
        self.assertEqual(rc, 0, text)
        self.assertIn('create/verify tar archives', text)
        self.assertNotIn('创建', text)

        rc, text = self._run(['--help', '--lang', 'zh'])
        self.assertEqual(rc, 0, text)
        self.assertIn('创建/校验带 pax 内嵌 SHA-256', text)

    def test_language_can_come_from_the_environment(self):
        rc, text = self._run(['create', os.path.join(T.REPO_ROOT, 'no-such-dir')],
                             lang=(i18n.ENV_VAR, 'en'))
        self.assertEqual(rc, 1, text)
        self.assertIn('[ERROR]', text)
        self.assertIn('source directory does not exist', text)

        rc, text = self._run(['create', os.path.join(T.REPO_ROOT, 'no-such-dir')],
                             lang=(i18n.ENV_VAR, 'zh'))
        self.assertIn('源目录不存在', text)

    def test_invalid_lang_choice_is_rejected(self):
        rc, text = self._run(['--lang', 'de', 'create', '/tmp/whatever'])
        self.assertEqual(rc, 2, text)
        self.assertIn("invalid choice: 'de'", text)

    def test_lang_is_accepted_after_the_subcommand_too(self):
        rc, text = self._run(['create', os.path.join(T.REPO_ROOT, 'no-such-dir'),
                              '--lang', 'en'])
        self.assertEqual(rc, 1, text)
        self.assertIn('source directory does not exist', text)


class TestLanguagePropagation(unittest.TestCase):
    """The controller hands its language to the child tools it spawns."""

    def test_backup_exports_the_language_to_children(self):
        backup = T.load_backup()
        env = {}
        import i18n as catalog
        catalog.set_language('zh')
        try:
            catalog.export(env)
        finally:
            catalog.set_language(None)
        self.assertEqual(env[catalog.ENV_VAR], 'zh')

    def test_backup_help_is_localized(self):
        import subprocess
        env = dict(os.environ)
        env['ANDROBACKUP_LANG'] = 'en'
        proc = subprocess.run(
            [sys.executable, T.BACKUP, '--help'], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = proc.stdout.decode('utf-8', 'replace')
        self.assertEqual(proc.returncode, 0, text)
        self.assertIn('AndBackup controller', text)
        self.assertIn('message language', text)
        self.assertNotIn('主控', text)


if __name__ == '__main__':
    unittest.main()
