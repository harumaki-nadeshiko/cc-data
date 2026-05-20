#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SSH_KEY=${UBCC_SSH_KEY:-/mnt/data2/$USER/.ssh/id_rsa_hm}
BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
GIT_NAME=${UBCC_GIT_NAME:-$(git -C "$REPO_ROOT" config user.name || true)}
GIT_EMAIL=${UBCC_GIT_EMAIL:-$(git -C "$REPO_ROOT" config user.email || true)}
SSH_BASE=(ssh -i "$SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

if [ ! -f "$SSH_KEY" ]; then
  echo "missing ssh key: $SSH_KEY" >&2
  exit 1
fi

if [ -z "$GIT_NAME" ] || [ -z "$GIT_EMAIL" ]; then
  echo "missing git identity; set UBCC_GIT_NAME and UBCC_GIT_EMAIL or configure repo-local git identity" >&2
  exit 1
fi

git -C "$REPO_ROOT" remote get-url origin >/dev/null

AUTH_OUTPUT=$("${SSH_BASE[@]}" -T git@github.com 2>&1 || true)
case "$AUTH_OUTPUT" in
  *"successfully authenticated"*) ;;
  *)
    echo "$AUTH_OUTPUT" >&2
    echo "ssh auth to github failed" >&2
    exit 1
    ;;
esac

if [ "$BRANCH" = "HEAD" ]; then
  echo "detached HEAD; auto-push preflight requires a branch" >&2
  exit 1
fi

GIT_SSH_COMMAND="ssh -i $SSH_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  git -C "$REPO_ROOT" push --dry-run origin "HEAD:refs/heads/$BRANCH" >/dev/null

echo "preflight ok: commit identity present and push is non-interactive"
