"""scripts/campus-env.py (the Linux campus scripts' .env helper) must ask for
exactly the secrets campus-config.ps1 asks for on Windows. Two copies of one
list is how the two entry points end up disagreeing, so this pins them equal.

No database: SimpleTestCase, so it runs without touching the shared test DB.
"""
import importlib.util
import os
import re
import tempfile

from django.conf import settings
from django.test import SimpleTestCase

SCRIPTS = os.path.join(settings.BASE_DIR, '..', 'scripts')


def _load_campus_env():
    spec = importlib.util.spec_from_file_location('campus_env', os.path.join(SCRIPTS, 'campus-env.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


campus_env = _load_campus_env()


def _ps1_spec():
    """(key, required, hidden, prompt) for each entry of $script:CampusSecretSpec."""
    with open(os.path.join(SCRIPTS, 'campus-config.ps1'), encoding='utf-8') as fh:
        text = fh.read()
    block = text[text.index('$script:CampusSecretSpec'):text.index('function Get-CampusSecretSpec')]
    out = []
    for entry in re.split(r'@\{', block)[1:]:
        key = re.search(r"Key\s*=\s*'([^']+)'", entry).group(1)
        required = re.search(r'Required\s*=\s*\$(true|false)', entry).group(1) == 'true'
        hidden = re.search(r'Hidden\s*=\s*\$(true|false)', entry).group(1) == 'true'
        prompt = re.search(r'''Prompt\s*=\s*(['"])(.*?)\1''', entry).group(2)
        out.append((key, required, hidden, prompt))
    return out


class SecretSpecMatchesWindowsTests(SimpleTestCase):
    def test_same_secrets_flags_and_prompts_in_the_same_order(self):
        self.assertEqual(list(campus_env.SECRET_SPEC), _ps1_spec())


class EnvFileTests(SimpleTestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.env')
        os.close(fd)
        self.addCleanup(os.remove, self.path)

    def write(self, text):
        with open(self.path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)

    def test_template_placeholders_count_as_missing(self):
        self.write('SECRET_KEY=<paste the same SECRET_KEY used on Railway>\n'
                   'DATABASE_URL=postgres://u:p@host/db\n'
                   'R2_BUCKET_NAME=same as Railway\n')
        gaps = campus_env.missing(self.path, required_only=True)
        self.assertIn('SECRET_KEY', gaps)
        self.assertIn('R2_BUCKET_NAME', gaps)
        self.assertNotIn('DATABASE_URL', gaps)
        self.assertNotIn('EMAIL_HOST_USER', gaps)      # optional

    def test_set_keeps_shell_and_regex_characters_verbatim(self):
        self.write('# comment\nSECRET_KEY=old\nDEBUG=false\n')
        value = r'a$1b&c/d\e$&f'
        campus_env.set_value(self.path, 'SECRET_KEY', value)
        campus_env.set_value(self.path, 'R2_ACCOUNT_ID', 'new')
        with open(self.path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines, ['# comment', f'SECRET_KEY={value}', 'DEBUG=false', 'R2_ACCOUNT_ID=new'])

    def test_written_without_a_bom(self):
        campus_env.set_value(self.path, 'SECRET_KEY', 'x')
        with open(self.path, 'rb') as fh:
            self.assertFalse(fh.read().startswith(b'\xef\xbb\xbf'))
