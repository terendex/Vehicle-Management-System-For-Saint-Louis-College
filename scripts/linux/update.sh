#!/usr/bin/env bash
# update.sh - pull the latest version and restart the server. The Linux
# version of the Windows launcher's "Update & restart" button.
#
#     scripts/linux/update.sh            update if there is anything new
#     scripts/linux/update.sh --check    only say whether there is
#
# Follows the branch in campus.conf (BRANCH, default main), not whatever the
# checkout happens to be on. The restart is the whole upgrade: run-campus.sh
# reinstalls Python and Node packages, and rebuilds the web bundle, only when
# the pulled commits changed them.
set -euo pipefail

# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "$(id -u)" != 0 ] || die 'Run as the server user, not root: the checkout must stay theirs.'
check_only=0
[ "${1:-}" = --check ] && check_only=1

cd "$SLC_REPO"
say "Checking for updates on $BRANCH..."
git fetch --quiet origin "$BRANCH"
behind="$(git rev-list --count "HEAD..origin/$BRANCH")"
current="$(git rev-parse --abbrev-ref HEAD)"
if [ "$behind" = 0 ] && [ "$current" = "$BRANCH" ]; then
    ok 'Already up to date.'
    exit 0
fi
say "$behind new commit(s) on $BRANCH:"
git log --oneline "HEAD..origin/$BRANCH" | head -n 15 | sed 's/^/    /'
[ "$check_only" = 0 ] || exit 0

# A file edited on this machine would block the pull. Say which, rather than
# leaving git's own message to explain it.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no | sed 's/^/    /'
    die 'These files were changed on this machine, so the update would overwrite them. Undo the changes (git checkout -- <file>) and run this again.'
fi
[ "$current" = "$BRANCH" ] || git checkout --quiet "$BRANCH"
git pull --ff-only --quiet origin "$BRANCH"
ok "Updated to $(git log -1 --format='%h %s')"

say 'Restarting the server (it reinstalls and rebuilds only what changed)...'
sudo systemctl restart slc-vms
ok 'Restarted. Follow it with: journalctl -u slc-vms -f'
