"""Point backend/.env at a new Cloudflare quick-tunnel URL.

Quick tunnels get a new random URL every run, so these keys need updating each
session. Only the four tunnel-related keys are touched — every other line in
.env (secrets included) is left byte-for-byte alone and never printed.

Usage:
    python set_tunnel_url.py https://your-tunnel.trycloudflare.com

Then restart Daphne so the new values are loaded.
"""
import os
import re
import sys

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    url = sys.argv[1].strip().rstrip('/')
    if not url.startswith('https://'):
        print(f'error: expected an https:// URL, got: {url}')
        return 1

    updates = {
        'ALLOWED_HOSTS': 'localhost,127.0.0.1,.trycloudflare.com',
        'CORS_ALLOWED_ORIGINS': f'http://localhost:5173,{url}',
        'FRONTEND_URL': url,
        'BACKEND_URL': url,
    }

    if not os.path.exists(ENV_PATH):
        print(f'error: {ENV_PATH} not found')
        return 1

    with open(ENV_PATH, 'r', encoding='utf-8', newline='') as fh:
        lines = fh.readlines()

    nl = '\r\n' if (lines and lines[0].endswith('\r\n')) else '\n'
    seen, out = set(), []
    for line in lines:
        m = re.match(r'^\s*([A-Z0-9_]+)\s*=', line)
        key = m.group(1) if m else None
        if key in updates:
            out.append(f'{key}={updates[key]}{nl}')
            seen.add(key)
        else:
            out.append(line)

    for key in (k for k in updates if k not in seen):
        if out and not out[-1].endswith(('\n', '\r\n')):
            out.append(nl)
        out.append(f'{key}={updates[key]}{nl}')

    with open(ENV_PATH, 'w', encoding='utf-8', newline='') as fh:
        fh.writelines(out)

    print('Updated backend/.env:')
    for key, val in updates.items():
        print(f'  {key}={val}')
    print('\nNow restart Daphne so the new values are picked up.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
