"""backend/.env for the Linux campus scripts: which secrets are missing, asking
for them, and writing one value back.

The Python twin of the secret list and .env helpers in campus-config.ps1. The
bash scripts call this rather than editing .env with sed, because a Django
SECRET_KEY or a Neon password is full of the characters sed treats as syntax
($, /, &, \\), and a mangled secret only shows up later as rejected logins.

Standard library only: it runs before the virtualenv exists.

The list below MUST stay equal to $script:CampusSecretSpec in
campus-config.ps1 (same keys, same order, same Required flags).
backend/vehicles/test_campus_env.py fails when they drift.

Usage: python campus-env.py <env_file> missing [--required-only]
       python campus-env.py <env_file> prompt   [--only-missing]
       python campus-env.py <env_file> get KEY
       python campus-env.py <env_file> set KEY VALUE
`missing` prints one key per line and exits 1 when any are listed.
"""
import getpass
import os
import re
import sys

# (key, required, hidden, prompt). See campus-config.ps1 for why each is needed.
SECRET_SPEC = (
    ('SECRET_KEY',           True,  True,  "Django SECRET_KEY (byte-identical to Railway's)"),
    ('DATABASE_URL',         True,  True,  'Neon DATABASE_URL (the same one Railway uses)'),
    ('R2_ACCESS_KEY_ID',     True,  False, 'Cloudflare R2 access key id'),
    ('R2_SECRET_ACCESS_KEY', True,  True,  'Cloudflare R2 secret access key'),
    ('R2_BUCKET_NAME',       True,  False, 'R2 bucket name'),
    ('R2_ACCOUNT_ID',        True,  False, 'R2 account id'),
    ('R2_PUBLIC_URL',        True,  False, 'R2 public URL'),
    ('EMAIL_HOST_USER',      False, False, 'Gmail address this machine sends from (optional)'),
    ('EMAIL_HOST_PASSWORD',  False, True,  'Its 16-character Gmail App Password (optional)'),
)


def is_placeholder(value):
    """Test-CampusPlaceholder: the template's <...> and "same as Railway"
    phrases count as unset, so a machine never starts with them as real values."""
    v = (value or '').strip()
    return (v == '' or re.fullmatch(r'<.*>', v) is not None
            or re.search(r'same as Railway|CHANGE|your-|paste', v, re.I) is not None)


def read_lines(env_file):
    if not os.path.exists(env_file):
        return []
    with open(env_file, encoding='utf-8-sig') as fh:
        return fh.read().splitlines()


def env_map(env_file):
    out = {}
    for line in read_lines(env_file):
        if not line or line[0] in '# \t' or '=' not in line[1:]:
            continue
        key, _, value = line.partition('=')
        out[key] = value.strip()
    return out


def set_value(env_file, key, value):
    """Rewrite KEY=... in place (appending it if absent). The value is written
    exactly as given - no pattern substitution anywhere near it - and the file
    stays UTF-8 without a BOM, which python-dotenv would read into a key name."""
    lines = read_lines(env_file)
    for i, line in enumerate(lines):
        if line.startswith(f'{key}='):
            lines[i] = f'{key}={value}'
            break
    else:
        lines.append(f'{key}={value}')
    tmp = f'{env_file}.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(lines) + '\n')
    os.chmod(tmp, 0o600)               # secrets: readable by the server's user only
    os.replace(tmp, env_file)


def missing(env_file, required_only=False):
    values = env_map(env_file)
    return [key for key, required, _, _ in SECRET_SPEC
            if (required or not required_only) and is_placeholder(values.get(key))]


def prompt(env_file, only_missing=False):
    values = env_map(env_file)
    gaps = set(missing(env_file))
    print('        Press Enter to keep what is already in .env.')
    for key, _, hidden, text in SECRET_SPEC:
        if only_missing and key not in gaps:
            continue
        current = values.get(key, '')
        shown = '(not set)' if is_placeholder(current) else '(set - hidden)' if hidden else current
        ask = getpass.getpass if hidden else input
        entered = ask(f'  {text} [{shown}]: ').strip()
        if entered:
            set_value(env_file, key, entered)


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    env_file, cmd, rest = argv[0], argv[1], argv[2:]
    if cmd == 'missing':
        gaps = missing(env_file, required_only='--required-only' in rest)
        print('\n'.join(gaps))
        return 1 if gaps else 0
    if cmd == 'prompt':
        prompt(env_file, only_missing='--only-missing' in rest)
        return 0
    if cmd == 'get' and len(rest) == 1:
        print(env_map(env_file).get(rest[0], ''))
        return 0
    if cmd == 'set' and len(rest) == 2:
        set_value(env_file, rest[0], rest[1])
        return 0
    sys.exit(__doc__)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
