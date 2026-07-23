"""Set the outgoing-mail credentials in backend/.env.

Only touches EMAIL_HOST_USER and EMAIL_HOST_PASSWORD; every other line in
.env is left byte-for-byte alone. Values are never printed — the script
reports lengths only, so a shared terminal or screen recording never leaks
the app password.

Usage (prompts, nothing on the command line so it stays out of shell history):
    python set_email_creds.py

Or non-interactively:
    python set_email_creds.py --user you@gmail.com --password "xxxx xxxx xxxx xxxx"

Gmail requires an *App Password* (16 characters, from Google Account >
Security > 2-Step Verification > App passwords), not the account password.
Spaces in the app password are stripped automatically.
"""

import argparse
import getpass
import pathlib
import re
import sys

ENV_PATH = pathlib.Path(__file__).resolve().parent / '.env'
KEYS = ('EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD')


def _set_key(lines, key, value):
    """Replace `key=`'s value in-place, or append the pair if it is absent."""
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=')
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f'{key}={value}'
            return lines, 'updated'
    lines.append(f'{key}={value}')
    return lines, 'added'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--user', help='Gmail address used to send mail')
    ap.add_argument('--password', help='Gmail App Password (16 chars)')
    args = ap.parse_args()

    if not ENV_PATH.exists():
        sys.exit(f'ERROR: {ENV_PATH} not found.')

    user = (args.user or input('Gmail address: ')).strip()
    # Spaces are how Google displays the app password; SMTP wants it unspaced.
    raw = args.password if args.password is not None else getpass.getpass('App password: ')
    password = raw.replace(' ', '').strip()

    if '@' not in user:
        sys.exit('ERROR: that does not look like an email address.')
    if len(password) != 16:
        sys.exit(f'ERROR: Gmail app passwords are 16 characters; got {len(password)}. '
                 'Use an App Password, not the account password.')

    lines = ENV_PATH.read_text(encoding='utf-8').splitlines()
    lines, a1 = _set_key(lines, 'EMAIL_HOST_USER', user)
    lines, a2 = _set_key(lines, 'EMAIL_HOST_PASSWORD', password)
    ENV_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    # Deliberately no values echoed.
    print(f'  EMAIL_HOST_USER     {a1}  ({len(user)} chars)')
    print(f'  EMAIL_HOST_PASSWORD {a2}  ({len(password)} chars)')
    print(f'  wrote {ENV_PATH}')
    print('  Restart Daphne for the new credentials to take effect.')


if __name__ == '__main__':
    main()
